"""받기 실행: 검사 → 파일 → fdatasync → DB → 응답 (IN-4–IN-6), 재색인 (IN-9, TL-3), 다시 넣기 (IN-10).

HTTP(routes.py), 재색인 도구, 다시 넣기 도구가 모두 이 경로 하나를 씁니다.
"""

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import psycopg

from uwbserver.intake import rules, store
from uwbserver.pipeline import clock, db, files, log, wire
from uwbserver.registry.store import Device


@dataclass(frozen=True, slots=True)
class Reply:
    status: int  # HTTP 상태
    body: bytes = b""
    codes: tuple[int, ...] = ()


class StorageError(Exception):
    """파일이나 DB 쓰기가 실패함 → 503. Head가 다시 보냄(IN-6)."""


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 3)


def receive(
    conn: psycopg.Connection,
    device: Device,
    body: bytes,
    *,
    received_us: int | None = None,
    keep_rejected: bool = True,
) -> Reply:
    """요청 하나 = 트랜잭션 하나. keep_rejected=False는 다시 넣기(IN-10)용: 거절을 또 적지 않음."""
    received_us = clock.now_us() if received_us is None else received_us
    t0 = time.perf_counter()
    try:
        records = wire.split_request(body)
    except wire.FramingError as e:
        return _reject_framing(conn, device, body, str(e), received_us, keep_rejected)

    verdicts = rules.check_records(records, device_mac=device.mac_bytes, server_utc_us=received_us)
    t_decode = _ms(t0)
    timing: dict[str, float] = {"decode_ms": t_decode}
    try:
        with conn.transaction():
            t1 = time.perf_counter()
            db.lock_device(conn, device.id)  # IN-4
            keys = [v.key for v in verdicts if v.stored]
            verdicts = rules.mark_duplicates(verdicts, store.existing_keys(conn, device.id, keys))  # IN-5
            timing["db_read_ms"] = _ms(t1)
            with files.Appender() as appender:
                t2 = time.perf_counter()
                chunk_rows, blocks, rejected = _write_files(appender, device, verdicts, received_us, keep_rejected)
                timing["file_ms"] = _ms(t2)
                t3 = time.perf_counter()
                appender.sync()  # IN-6 3번
                timing["fdatasync_ms"] = _ms(t3)
                t4 = time.perf_counter()
                if chunk_rows:
                    db.lock_chunk_insert(conn)  # PL-3
                    store.insert_chunks(conn, chunk_rows)
                    store.insert_health(conn, blocks["health"])
                    store.insert_system(conn, blocks["system"])
                    store.insert_survey(conn, blocks["survey"])
                store.insert_rejected(conn, rejected)
                timing["db_write_ms"] = _ms(t4)
    except (OSError, psycopg.Error) as e:
        log.error("intake_storage_failed", device=device.id, error=f"{type(e).__name__}: {e}")
        return Reply(503)

    codes = tuple(v.code for v in verdicts)
    log.info(
        "intake_request",
        device=device.id,
        n_chunks=len(codes),
        codes=dict(Counter(codes)),
        total_ms=_ms(t0),
        **timing,
    )
    return Reply(200, wire.encode_ack(clock.now_us(), codes), codes)


def _write_files(
    appender: files.Appender,
    device: Device,
    verdicts: list[rules.Verdict],
    received_us: int,
    keep_rejected: bool,
) -> tuple[list[dict], dict[str, list[dict]], list[dict]]:
    """IN-6 2번: 통과한 묶음은 raw에, 거절한 묶음은 rejected에 이어 씀. 표에 넣을 행을 돌려줍니다."""
    chunk_rows: list[dict] = []
    blocks: dict[str, list[dict]] = {"health": [], "system": [], "survey": []}
    rejected: list[dict] = []
    for v in verdicts:
        if v.stored:
            h = v.chunk.header
            path = files.raw_path(device.id, h.head_boot_id, received_us)
            offset = appender.append(path, files.raw_record_head(len(v.data), received_us), v.data)
            chunk_rows.append(rules.chunk_row(device.id, v.chunk, received_us, path, offset))
            for kind, row in rules.block_rows(device.id, v.chunk, received_us).items():
                blocks[kind].append(row)
        elif v.rejected and keep_rejected:
            path = files.rejected_path(device.id, received_us)
            offset = appender.append(path, files.rejected_record_head(len(v.data), received_us, v.code, v.check), v.data)
            rejected.append(
                rules.rejected_row(
                    device.id,
                    received_us=received_us,
                    code=v.code,
                    check=v.check,
                    detail=v.detail,
                    data=v.data,
                    path=path,
                    offset=offset,
                )
            )
    return chunk_rows, blocks, rejected


def _reject_framing(
    conn: psycopg.Connection, device: Device, body: bytes, reason: str, received_us: int, keep: bool
) -> Reply:
    """IN-6 400: 본문 전체를 code 255 레코드로 남긴 뒤 400. 그 쓰기가 실패하면 503."""
    if keep:
        try:
            with conn.transaction():
                db.lock_device(conn, device.id)
                with files.Appender() as appender:
                    path = files.rejected_path(device.id, received_us)
                    head = files.rejected_record_head(len(body), received_us, wire.FRAMING_CODE, 1)
                    offset = appender.append(path, head, body)
                    appender.sync()
                    row = rules.rejected_row(
                        device.id,
                        received_us=received_us,
                        code=wire.FRAMING_CODE,
                        check=1,
                        detail=reason,
                        data=body,
                        path=path,
                        offset=offset,
                    )
                    store.insert_rejected(conn, [row])
        except (OSError, psycopg.Error) as e:
            log.error("intake_storage_failed", device=device.id, error=f"{type(e).__name__}: {e}")
            return Reply(503)
    log.info("intake_request", device=device.id, n_chunks=0, framing_error=reason)
    return Reply(400, reason.encode())


# ---------------------------------------------------------------- 재색인 (IN-9, TL-3)


@dataclass
class ReindexStats:
    files: int = 0
    records: int = 0
    inserted: int = 0
    truncated_bytes: int = 0
    unreadable: list[str] = field(default_factory=list)


def reindex(
    conn: psycopg.Connection,
    *,
    since_hour: datetime | None = None,
    device_id: str | None = None,
    repair_tail: bool = False,
) -> ReindexStats:
    """raw 파일 → intake.* 행. 이미 있는 키는 건너뜀. 파일 하나 = 트랜잭션 하나(전역 잠금 포함).

    repair_tail=True는 intake가 받기를 열기 전에만 씁니다(다른 작성자가 없을 때만 꼬리를 자를 수 있음).
    """
    stats = ReindexStats()
    for path in files.list_files("raw"):
        name = files.parse_raw_path(path)
        if device_id and name.device_id != device_id:
            continue
        if since_hour and name.hour < since_hour:
            continue
        garbage: list[tuple[int, int]] = []
        records, valid_end = files.scan_raw(path, garbage)
        middle = [g for g in garbage if g[0] + g[1] < files.size(path)]
        if middle:
            log.warning("raw_garbage_skipped", path=path, spans=middle[:5])  # 쓰다 만 레코드 뒤의 레코드도 읽음
        if repair_tail:
            cut = files.truncate_torn_tail(path, valid_end)
            if cut:
                log.warning("raw_torn_tail_truncated", path=path, bytes=cut)
                stats.truncated_bytes += cut
        stats.files += 1
        stats.records += len(records)
        chunk_rows: list[dict] = []
        blocks: dict[str, list[dict]] = {"health": [], "system": [], "survey": []}
        seen: set[tuple[int, int]] = set()
        for rec in records:
            try:
                chunk = wire.decode_chunk(rec.data)
            except ValueError as e:
                stats.unreadable.append(f"{path}@{rec.offset}: {e}")
                continue
            key = (chunk.header.head_boot_id, chunk.header.chunk_seq)
            if key in seen:  # 같은 파일 안 반복은 먼저 나온 것(5.2)
                continue
            seen.add(key)
            chunk_rows.append(rules.chunk_row(name.device_id, chunk, rec.received_us, path, rec.offset))
            for kind, row in rules.block_rows(name.device_id, chunk, rec.received_us).items():
                blocks[kind].append(row)
        with conn.transaction():
            # 받기와 같은 잠금 순서(장치 → 전역). 받는 중인 요청이 있으면 그 커밋을 기다리고, 뒤의 요청은 이 행을 중복으로 봄
            db.lock_device(conn, name.device_id)
            db.lock_chunk_insert(conn)
            stats.inserted += store.insert_chunks(conn, chunk_rows, skip_conflicts=True)
            store.insert_health(conn, blocks["health"], skip_conflicts=True)
            store.insert_system(conn, blocks["system"], skip_conflicts=True)
            store.insert_survey(conn, blocks["survey"], skip_conflicts=True)
    return stats


def startup_reindex(conn: psycopg.Connection) -> ReindexStats:
    """IN-9: 받기를 열기 전에, 마지막으로 남은 행 기준 1시간 앞 hour부터 다시 읽음."""
    since = rules.reindex_since_hour(store.max_received_at(conn))
    conn.commit()
    stats = reindex(conn, since_hour=since, repair_tail=True)
    log.info("startup_reindex", since=since, **{k: v for k, v in vars(stats).items() if k != "unreadable"})
    for item in stats.unreadable:
        log.error("raw_record_unreadable", where=item)
    return stats


DB_RESTART_LOCK = 5  # 작업자가 여럿이어도 재색인은 하나만


def reindex_if_db_restarted(conn: psycopg.Connection, state: dict) -> ReindexStats | None:
    """IN-9 보강: DB만 재시작한 경우. 받기 커밋은 디스크를 기다리지 않으므로 마지막 1초 안쪽 행을 잃을 수 있는데,
    intake 컨테이너는 그대로라 시작 때 재색인이 돌지 않습니다. DB가 다시 뜬 것을 보면 같은 기준으로 재색인합니다.

    state: 부르는 쪽이 유지하는 dict(직전에 본 DB 시작 시각). 받기를 막지 않으므로 꼬리는 자르지 않습니다.
    """
    started = conn.execute("SELECT pg_postmaster_start_time() AS t").fetchone()["t"]
    previous = state.get("db_started")
    state["db_started"] = started
    if previous is None or started == previous:
        return None
    if not conn.execute("SELECT pg_try_advisory_lock(%s, 0) AS ok", (DB_RESTART_LOCK,)).fetchone()["ok"]:
        return None
    try:
        since = rules.reindex_since_hour(store.max_received_at(conn))
        stats = reindex(conn, since_hour=since, repair_tail=False)
        log.warning("db_restart_reindex", since=since, inserted=stats.inserted, files=stats.files)
        return stats
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s, 0)", (DB_RESTART_LOCK,))


# ---------------------------------------------------------------- 거절한 묶음 다시 넣기 (IN-10)


def reingest_rejected(
    conn: psycopg.Connection, device: Device, path: str, *, since: datetime | None = None
) -> Counter:
    """rejected 레코드를 받기와 같은 경로로 다시 넣습니다. rejected 레코드는 그대로 둡니다.

    수신 시각은 처음 받았던 시각을 씁니다(구간 시각 SG-7이 실제 도착을 따르게).
    돌려주는 값: 결과 코드별 수. 프레이밍이 깨졌던 본문(255)은 400이면 'framing'으로 셉니다.
    """
    records, _ = files.scan_rejected(path)
    out: Counter = Counter()
    for rec in records:
        if since and clock.from_us(rec.received_us) < since:
            continue
        body = rec.data if rec.code == wire.FRAMING_CODE else wire.join_records([rec.data])
        reply = receive(conn, device, body, received_us=rec.received_us, keep_rejected=False)
        if reply.status == 200:
            out.update(reply.codes)
        else:
            out[{400: "framing", 503: "storage"}.get(reply.status, str(reply.status))] += 1
    return out
