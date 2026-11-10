"""analysis 단계 실행: `analysis {segment_id}` 작업 하나 → run 하나 또는 0개 (AN-1–AN-10).

순서: 구간 확인 → 입력 고르기 → 문맥 → run 번호 → C2 호출 → 응답 검사 → 파일(임시 이름 → fsync → 이름 바꾸기)
→ 한 트랜잭션(run·bout·궤적 행, 작업 끝, 다음 작업) → 커밋 뒤 옛 궤적 삭제.
"""

import gzip
import hashlib
import time
from datetime import timedelta

import httpx
import psycopg

from uwbserver.analysis import gaitfusion, rules, store
from uwbserver.analysis.store import SegmentView
from uwbserver.pipeline import clock, files, jobs, log, settings
from uwbserver.pipeline.jobs import Job

WAIT_INSTALLATION = timedelta(minutes=10)  # AN-3
WAIT_INSTALLATION_MAX = timedelta(hours=1)


class _Input:
    def __init__(self, kind: str, records: list[tuple[str, int, int]], head_us: list[int]) -> None:
        self.kind = kind  # 'raw' | 'archive'
        self.records = records  # (경로, 위치, 길이), seq 순
        self.head_us = head_us  # 같은 순서의 head_us_start


def _choose_input(conn: psycopg.Connection, seg: SegmentView) -> _Input | None:
    """AN-4: 구간의 묶음 행이 모두 intake.chunk에 있으면 raw, 아니면 archive, 둘 다 없으면 None."""
    rows = store.raw_chunks(conn, seg)
    if len(rows) == seg.n_chunks:
        return _Input("raw", [(r["raw_path"], r["raw_offset"], r["raw_len"]) for r in rows], [r["head_us_start"] for r in rows])
    ranges = store.archive_ranges(conn, seg.id)
    if not ranges:
        return None
    records: list[tuple[str, int, int]] = []
    head_us: list[int] = []
    for rg in ranges:
        recs, _ = files.scan_raw(rg["path"])
        for rec in recs:
            records.append((rg["path"], rec.offset, len(rec.data)))
            head_us.append(_head_us(rec.data))
    return _Input("archive", records, head_us)


def _head_us(chunk: bytes) -> int:
    return int.from_bytes(chunk[12:20], "little", signed=True)


def _context(conn: psycopg.Connection, seg: SegmentView, run_id: int, origin_head_us: int) -> dict:
    """AN-5."""
    anchors = None
    if seg.installation_id is not None:
        inst = store.installation(conn, seg.installation_id)
        if inst and inst["surveyed"]:
            anchors = inst["anchors"]
    subject = store.subject_of_segment(conn, seg.id)
    return rules.build_context(
        segment_id=seg.id,
        run_id=run_id,
        sides=seg.sides,
        origin_head_us=origin_head_us,
        anchors=anchors,
        subject=dict(subject) if subject else None,
        params=settings.get().analysis_params,
    )


def handle(
    conn: psycopg.Connection, job: Job, client: gaitfusion.Client, *, gaitfusion_version: str | None = None
) -> str:
    """작업 하나를 처리합니다. 결과를 짧은 말로 돌려줍니다(로그·시험용).

    gaitfusion_version: core가 /v1/version으로 확인한 값. skipped run에 적음(7.4).
    """
    now = clock.now()
    with conn.transaction():
        seg = store.lock_segment(conn, job.segment_id)
        if seg is None:
            jobs.fail(conn, job.id, "no_segment", now=now)
            return "no_segment"
        if seg.state == "open":  # AN-2
            jobs.finish(conn, job.id, now=now, note="segment_open")
            return "segment_open"
        if seg.installation_id is None and now - job.created_at < WAIT_INSTALLATION_MAX:  # AN-3
            jobs.defer(conn, job.id, until=now + WAIT_INSTALLATION, note="waiting_installation")
            return "waiting_installation"
        source = _choose_input(conn, seg)
        if source is None:
            jobs.finish(conn, job.id, now=now, note="no_input")
            return "no_input"
        run_id = store.next_run_id(conn)
        context = _context(conn, seg, run_id, source.head_us[0])

    origin_head_us = source.head_us[0]
    origin_at = clock.from_us(origin_head_us + seg.utc_offset_us)
    t_last = rules.t_last_s(origin_head_us, source.head_us[-1])
    body = b"".join(files.c1_record(c) for c in files.read_many(source.records))
    run = {
        "id": run_id,
        "segment_id": seg.id,
        "segment_revision": seg.revision,
        "input": source.kind,
        "n_chunks_sent": len(source.records),
        "origin_head_us": origin_head_us,
        "origin_at": origin_at,
        "gaitfusion_version": None,
        "core_version": settings.get().core_version,
        "context": context,
        "summary": None,
        "status": None,
        "result_status": None,
        "skip_reason": None,
        "result_path": None,
        "result_bytes": None,
        "result_sha256": None,
        "started_at": now,
        "finished_at": None,
    }

    try:
        reply = client.analyze(context, body)
    except httpx.HTTPError as e:  # 시간 초과, 연결 실패
        return _retry(conn, job, f"c2_{type(e).__name__}: {e}")

    if reply.status == 422:
        run |= {"status": "skipped", "skip_reason": reply.reason or "unspecified", "finished_at": clock.now()}
        run["gaitfusion_version"] = gaitfusion_version
        with conn.transaction():
            store.insert_run(conn, run)
            _finish_and_follow(conn, job, run_id, seg)
        log.info("analysis_skipped", segment=seg.id, run=run_id, reason=run["skip_reason"])
        return "skipped"
    if reply.status == 400:
        with conn.transaction():
            jobs.fail(conn, job.id, f"c2_400: {reply.reason} (chunk {reply.chunk_index})", now=clock.now())
        log.error("analysis_c2_400", segment=seg.id, run=run_id, reason=reply.reason, chunk_index=reply.chunk_index)
        return "failed_400"
    if reply.status != 200:
        return _retry(conn, job, f"c2_http_{reply.status}")

    try:
        checked = rules.check_response(
            reply.parts, segment_sides=seg.sides, anchors_sent=context["anchors"] is not None, t_last=t_last
        )
    except rules.ResponseInvalid as e:
        with conn.transaction():
            jobs.fail(conn, job.id, f"c2_invalid: {e.rule}: {e}", now=clock.now())
        log.error("analysis_c2_invalid", segment=seg.id, run=run_id, rule=e.rule, error=str(e))
        return "failed_invalid"

    parts = {p.name: p.data for p in reply.parts}
    return _store_done(conn, job, seg, run, checked, parts, origin_at)


def _store_done(
    conn: psycopg.Connection,
    job: Job,
    seg: SegmentView,
    run: dict,
    checked: rules.Checked,
    parts: dict[str, bytes],
    origin_at,
) -> str:
    run_id = run["id"]
    written: list[str] = []
    committing = False
    t_files = time.perf_counter()
    try:
        result_path = files.result_path(run_id)
        files.write_atomic(result_path, gzip.compress(checked.result_bytes, mtime=0))
        written.append(result_path)
        bouts = rules.bout_rows(checked, run_id=run_id, origin_at=origin_at)
        traj_rows: list[dict] = []
        for b in checked.bouts:
            for side, part_name in b.trajectories:
                path = files.trajectory_path(run_id, b.idx, side)
                files.write_atomic(path, parts[part_name])  # TRJ1 그대로(7.3)
                written.append(path)
                trj = checked.trajectories[part_name]
                traj_rows.append(
                    {"idx": b.idx, "side": side, "path": path, "rate_hz": trj.rate_hz,
                     "t0": rules.trajectory_t0(origin_at, trj), "n": trj.n, "bytes": trj.nbytes}
                )  # fmt: skip
        run |= {
            "status": "done",
            "gaitfusion_version": checked.gaitfusion_version,
            "summary": checked.summary,
            "result_status": checked.result_status,
            "result_path": result_path,
            "result_bytes": len(checked.result_bytes),
            "result_sha256": hashlib.sha256(checked.result_bytes).digest(),
            "finished_at": clock.now(),
        }
        file_ms = round((time.perf_counter() - t_files) * 1000, 1)
        t_db = time.perf_counter()
        with conn.transaction():
            store.insert_run(conn, run)
            by_idx: dict[int, int] = {}
            for bout_id, row in zip(store.next_bout_ids(conn, len(bouts)), bouts, strict=True):
                row["id"] = by_idx[row["idx"]] = bout_id
            store.copy_bouts(conn, bouts)
            for t in traj_rows:
                t["bout_id"] = by_idx[t["idx"]]
            store.copy_trajectories(conn, traj_rows)
            _finish_and_follow(conn, job, run_id, seg)
            committing = True  # 이 블록을 나가며 COMMIT
        db_ms = round((time.perf_counter() - t_db) * 1000, 1)
    except (OSError, psycopg.Error) as e:
        if not committing:  # COMMIT 중의 실패는 run 행이 있을 수 있으므로 파일을 남김
            for path in written:
                files.delete(path)
        return _retry(conn, job, f"store_{type(e).__name__}: {e}")

    log.info(
        "analysis_done", segment=seg.id, run=run_id, bouts=len(bouts), trajectories=len(traj_rows),
        file_ms=file_ms, db_ms=db_ms,
    )  # fmt: skip
    _drop_old_trajectories(conn, seg.id, run_id)
    return "done"


def _finish_and_follow(conn: psycopg.Connection, job: Job, run_id: int, seg: SegmentView) -> None:
    """AN-7: 작업 done → archive 작업 → 보낸 뒤 구간이 바뀌었으면 analysis 다시(자기를 먼저 done, PL-2)."""
    now = clock.now()
    jobs.finish(conn, job.id, now=now)
    jobs.enqueue_archive(conn, run_id, now=now)
    if store.segment_revision(conn, seg.id) != seg.revision:
        jobs.enqueue_analysis(conn, seg.id, now=now)


def _drop_old_trajectories(conn: psycopg.Connection, segment_id: int, run_id: int) -> None:
    """커밋 뒤 같은 구간의 옛 run 궤적 파일과 행을 지움(AN-9). 실패해도 다음 run 때 다시 지움."""
    try:
        old = store.old_trajectories(conn, segment_id, run_id)
        for t in old:
            files.delete(t["path"])
            with conn.transaction():
                store.delete_trajectory(conn, t["bout_id"], t["side"])
        for old_run in {t["run_id"] for t in old}:
            files.delete_tree(files.trajectory_dir(old_run))
    except (OSError, psycopg.Error) as e:
        log.warning("trajectory_cleanup_failed", segment=segment_id, error=str(e))


def _retry(conn: psycopg.Connection, job: Job, error: str) -> str:
    with conn.transaction():
        state = jobs.retry_or_fail(conn, job, error, now=clock.now())
    log.warning("analysis_retry", job=job.id, segment=job.segment_id, attempt=job.attempts + 1, state=state, error=error)
    return f"retry_{state}"
