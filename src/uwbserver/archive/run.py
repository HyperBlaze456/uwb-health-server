"""archive 단계 실행.

- handle: `archive {run_id}` 작업. 그 run의 걷기 구간 ± 15초 원본을 raw에서 잘라 영구 파일로 둠(AR-1–AR-5).
- purge: 매일 한 번. 30일 지난 원본 중 조건을 만족하는 것만 지움(AR-6). 원본 삭제는 이 함수 한 곳에서만 합니다.
  PURGE_ENABLED = false(기본)이면 지울 대상만 로그에 적습니다(AR-7). 나중에 AI 모델이 확정되면 이 스위치만 켭니다.
"""

import hashlib
from dataclasses import asdict, dataclass
from datetime import timedelta

import psycopg

from uwbserver.archive import rules, store
from uwbserver.pipeline import clock, db, files, jobs, log, settings, wire
from uwbserver.pipeline.jobs import Job


def handle(conn: psycopg.Connection, job: Job) -> str:
    now = clock.now()
    written: list[str] = []
    try:
        with conn.transaction():
            run = store.run_info(conn, job.run_id)
            if run is None:
                jobs.fail(conn, job.id, "no_run", now=now)
                return "no_run"
            store.lock_device(conn, run["device_id"])
            if store.cut_exists(conn, run["id"]):  # 이미 끝난 run(되살린 작업이 다시 온 경우)
                jobs.finish(conn, job.id, now=now, note="already_cut")
                return "already_cut"
            n_ranges, n_chunks = _cut(conn, run, written, now)
            store.insert_cut(conn, run["id"], n_ranges, n_chunks, now)
            jobs.finish(conn, job.id, now=now)
    except (OSError, psycopg.Error) as e:
        for path in written:  # 행이 없는 archive 파일을 남기지 않음
            files.delete(path)
        with conn.transaction():
            state = jobs.retry_or_fail(conn, job, f"archive_{type(e).__name__}: {e}", now=clock.now())
        log.warning("archive_retry", job=job.id, run=job.run_id, state=state, error=str(e))
        return f"retry_{state}"
    log.info("archive_done", run=job.run_id, ranges=n_ranges, chunks=n_chunks)
    return "done"


def _cut(conn: psycopg.Connection, run: dict, written: list[str], now) -> tuple[int, int]:
    """AR-2–AR-4. 새로 만든 (범위 수, 묶음 수)."""
    pad = settings.get().archive_pad_s
    # run이 쓴 시각 기준(origin_at − origin_head_us)으로 Head 시각을 UTC로 바꿈
    offset = clock.to_us(run["origin_at"]) - run["origin_head_us"]
    bouts = [(clock.to_us(b["start_at"]), clock.to_us(b["end_at"])) for b in store.bouts(conn, run["id"])]
    if not bouts:
        return 0, 0
    chunks = store.segment_chunks(conn, run)
    by_seq = {c["chunk_seq"]: c for c in chunks}
    wanted = rules.ranges_for_bouts(
        [(c["chunk_seq"], c["head_us_start"] + offset) for c in chunks], rules.bout_windows(bouts, pad)
    )
    todo = rules.subtract(wanted, store.covered(conn, run["device_id"], run["head_boot_id"]))
    n_chunks = 0
    pieces = rules.trim_to_present(todo, list(by_seq))
    for rg, seqs in pieces:
        rows = [by_seq[s] for s in seqs]
        locations = [(r["raw_path"], r["raw_offset"], r["raw_len"]) for r in rows]
        data = b"".join(
            files.raw_record_head(len(chunk), clock.to_us(r["received_at"])) + chunk
            for r, chunk in zip(rows, files.read_many(locations), strict=True)
        )
        start_us = rows[0]["head_us_start"] + run["utc_offset_us"]
        path = files.archive_path(run["device_id"], run["head_boot_id"], rg.first, rg.last, start_us)
        files.write_atomic(path, data)
        written.append(path)
        store.insert_range(
            conn,
            {
                "device_id": run["device_id"],
                "head_boot_id": run["head_boot_id"],
                "segment_id": run["segment_id"],
                "first_seq": rg.first,
                "last_seq": rg.last,
                "n_chunks": len(rows),
                "start_at": clock.from_us(start_us),
                "end_at": clock.from_us(rows[-1]["head_us_start"] + run["utc_offset_us"] + 1_000_000),
                "path": path,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).digest(),
                "from_run_id": run["id"],
                "created_at": now,
            },
        )
        n_chunks += len(rows)
    return len(pieces), n_chunks


# ---------------------------------------------------------------- 30일 삭제 (AR-6, AR-7)


@dataclass
class PurgeStats:
    enabled: bool = False
    raw_files_checked: int = 0
    raw_files_deleted: int = 0  # 꺼져 있으면 "지울" 수
    raw_files_kept: int = 0
    chunk_rows: int = 0
    block_rows: int = 0
    rejected_files: int = 0
    rejected_rows: int = 0


def _file_keys(path: str) -> tuple[str | None, list[tuple[int, int]]]:
    """raw 파일의 (장치, 온전한 레코드의 묶음 키들). 파일이 이미 없으면 (장치, [])."""
    name = files.parse_raw_path(path)
    if not files.exists(path):
        return name.device_id, []
    records, _ = files.scan_raw(path)
    return name.device_id, [wire.peek_key(r.data) for r in records]


def _purge_raw_file(conn: psycopg.Connection, path: str, enabled: bool, now, stats: PurgeStats) -> str | None:
    """raw 파일 하나. 지웠으면(꺼져 있으면 "지울" 것이면) None, 남기면 그 이유.

    AR-6: 파일 안의 **모든 레코드**가 행으로 있어야 하고 그 행들이 조건을 만족해야 함. 행만 보면, DB만 재시작해
    잃은 행(받기 커밋은 디스크를 기다리지 않음)의 레코드를 확인 없이 지우게 됨.
    검사부터 행 삭제까지 묶음 넣기 전역 잠금(PL-3)을 쥐어, 그 사이에 재색인 등이 넣은 행을 확인 없이 지우지 않음.
    """
    device_id, keys = _file_keys(path)
    with conn.transaction():
        db.lock_chunk_insert(conn)
        chunk_states = store.chunk_states(conn, path)
        if not chunk_states and store.purged_exists(conn, path):
            leftover = True  # 어제 행을 지운 뒤 파일을 지우기 전에 멈춤
        else:
            leftover = False
            missing = store.keys_without_row(conn, device_id, keys)
            if missing:
                log.error("purge_records_without_row", path=path, count=len(missing), first=missing[:5],
                          hint="tools/reindex_raw.py로 행을 되살린 뒤 다음 날 다시 판단")  # fmt: skip
                return "records_without_row"
            cursor = jobs.get_cursor(conn)
            segs = store.segment_states(conn, sorted({c.segment_id for c in chunk_states if c.segment_id}))
            verdict = rules.file_verdict(chunk_states, segs, cursor)
            if not verdict.deletable:
                if verdict.orphan:
                    log.error("purge_chunk_without_segment", path=path)
                return verdict.reason
            if not enabled:
                counts = store.count_rows_for_path(conn, path)
                stats.raw_files_deleted += 1
                stats.chunk_rows += counts["chunk"]
                stats.block_rows += counts["health"] + counts["system"]
                return None
            stats.block_rows += store.delete_blocks_for_path(conn, path)  # 1. 블록 행
            n = store.delete_chunks_for_path(conn, path)  # 2. 묶음 행
            stats.chunk_rows += n
            store.insert_purged(conn, path, len(keys), n, now)  # 무엇을 언제 지웠는지
    if not enabled:
        if leftover:
            stats.raw_files_deleted += 1
        return None
    files.delete(path)  # 3. 파일
    stats.raw_files_deleted += 1
    if leftover:
        log.warning("purge_leftover_file_deleted", path=path)
    return None


def purge(conn: psycopg.Connection) -> PurgeStats:
    s = settings.get()
    now = clock.now()
    before = now - timedelta(days=s.raw_retention_days)
    stats = PurgeStats(enabled=s.purge_enabled)
    kept_reasons: dict[str, int] = {}

    def raw_expired(path: str) -> bool:
        return rules.raw_file_expired(files.parse_raw_path(path).hour, now, s.raw_retention_days)

    def rejected_expired(path: str) -> bool:
        return rules.rejected_file_expired(files.parse_rejected_path(path)[1], now, s.raw_retention_days)

    # 후보 = 파일 목록 ∪ 행이 가리키는 경로(파일만 먼저 지워진 경우도 행을 정리)
    candidates = {p for p in [*files.list_files("raw"), *store.old_raw_paths(conn, before)] if raw_expired(p)}
    for path in sorted(candidates):
        stats.raw_files_checked += 1
        reason = _purge_raw_file(conn, path, s.purge_enabled, now, stats)
        if reason:
            stats.raw_files_kept += 1
            kept_reasons[reason] = kept_reasons.get(reason, 0) + 1

    # 거절한 묶음: 30일이 지나면 조건 없이
    rejected = {p for p in [*files.list_files("rejected"), *store.old_rejected_paths(conn, before)] if rejected_expired(p)}
    for path in sorted(rejected):
        stats.rejected_files += 1
        with conn.transaction():
            if s.purge_enabled:
                stats.rejected_rows += store.delete_rejected_for_path(conn, path)
            else:
                stats.rejected_rows += store.count_rejected_for_path(conn, path)
        if s.purge_enabled:
            files.delete(path)

    log.info("purge" if s.purge_enabled else "purge_dry_run", kept_reasons=kept_reasons, **asdict(stats))
    return stats
