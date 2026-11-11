"""feature 단계 실행: `feature {run_id}` 작업 하나 → feature.unpack 행 하나와 그 run의 feature.record 행들 (FT-1–FT-5).

순서: run 확인 → 결과 원문 읽기(sha256 대조) → 지표 목록 → 행 만들기(rules) → 한 트랜잭션(그 run의 옛 행 지우기,
COPY, 풀기 기록, 작업 끝, summary 작업 넣기 FT-8). 다시 풀어도 같은 행이 나옵니다(FT-5). 이 단계는 feature.*와 자기 작업 행만 쓰므로,
여기서 실패해도 run·걷기 구간·궤적·원본 보관에는 영향이 없습니다.

실패:
- run이 없음, 결과 원문 파일이 없음, 파일이 깨졌거나 sha256이 run과 다름, 원문과 analysis.bout가 어긋남
  → 재시도해도 같으므로 바로 failed(last_error에 이유).
- 파일을 읽다 난 그 밖의 OS 오류, 지표 목록을 읽지 못함, DB 오류 → PL-2의 재시도.
"""

import time

import psycopg

from uwbserver.feature import rules, store
from uwbserver.pipeline import clock, files, jobs, log, metrics, settings
from uwbserver.pipeline.jobs import Job


def handle(conn: psycopg.Connection, job: Job) -> str:
    """작업 하나를 처리합니다. 결과를 짧은 말로 돌려줍니다(로그·시험용)."""
    t0 = time.perf_counter()
    run = store.run_info(conn, job.run_id)
    if run is None:
        _fail(conn, job, "no_run")
        return "no_run"
    if run["status"] != "done" or run["result_path"] is None:  # skipped run(도구가 넣었을 때)
        with conn.transaction():
            jobs.finish(conn, job.id, now=clock.now(), note="no_result")
        return "no_result"
    try:
        gz = files.resolve(run["result_path"]).read_bytes()
        result = rules.read_result(gz, run["result_sha256"])
        catalog = metrics.load()
        unpacked = rules.unpack(result, catalog, origin_at=run["origin_at"], bout_ids=store.bout_ids(conn, run["id"]))
    except FileNotFoundError:
        return _fail(conn, job, f"result_missing: {run['result_path']}")
    except rules.ResultInvalid as e:
        return _fail(conn, job, f"result_invalid: {e}")
    except rules.UnpackError as e:
        return _fail(conn, job, f"unpack_mismatch: {e}")
    except (OSError, metrics.CatalogError, psycopg.Error) as e:
        return _retry(conn, job, f"read_{type(e).__name__}: {e}")
    parse_ms = round((time.perf_counter() - t0) * 1000, 1)

    t_tx = time.perf_counter()
    try:
        with conn.transaction():
            store.delete_records(conn, run["id"])  # FT-5: 다시 풀기
            store.delete_unpack(conn, run["id"])
            store.copy_records(conn, unpacked.rows)
            store.insert_unpack(
                conn,
                {
                    "run_id": run["id"],
                    "catalog_version": catalog.version,
                    "formats": unpacked.formats,
                    "n_records": unpacked.n_records,
                    "n_dropped_records": unpacked.n_dropped_records,
                    "n_bad_values": unpacked.n_bad_values,
                    "unknown_fields": unpacked.unknown_fields,
                    "unknown_tables": unpacked.unknown_tables,
                    "core_version": settings.get().core_version,
                    "unpacked_at": clock.now(),
                },
            )
            jobs.finish(conn, job.id, now=clock.now())
            _enqueue_summary(conn, run["id"])  # FT-8
    except psycopg.Error as e:
        return _retry(conn, job, f"store_{type(e).__name__}: {e}")
    tx_ms = round((time.perf_counter() - t_tx) * 1000, 1)

    fields = {
        "run": run["id"], "records": unpacked.n_records, "dropped": unpacked.n_dropped_records,
        "bad_values": unpacked.n_bad_values, "unknown_fields": unpacked.unknown_fields,
        "unknown_tables": unpacked.unknown_tables, "parse_ms": parse_ms, "tx_ms": tx_ms,
    }  # fmt: skip
    if unpacked.has_warnings:  # FT-3
        log.warning("feature_unrecognized", **fields)
    log.info("feature_done", **fields)
    return "done"


def _enqueue_summary(conn: psycopg.Connection, run_id: int) -> None:
    """FT-8: 걷기 구간이 시작한 한국 날짜들을 덮는 summary {장치, 처음 날짜, 끝 날짜} 하나. 그 구간의 옛 run의
    걷기 구간도 합침(재분석이면 옛 run의 값이 그날에서 빠져야 하므로). 걷기 구간이 하나도 없으면 넣지 않음."""
    span = store.bout_starts_of_segment(conn, run_id)
    if span is None or span["first"] is None:
        return
    jobs.enqueue_summary(
        conn, span["device_id"], clock.local_date(span["first"]), clock.local_date(span["last"]), now=clock.now()
    )


def _fail(conn: psycopg.Connection, job: Job, error: str) -> str:
    with conn.transaction():
        jobs.fail(conn, job.id, error, now=clock.now())
    log.error("feature_failed", job=job.id, run=job.run_id, error=error)
    return "failed"


def _retry(conn: psycopg.Connection, job: Job, error: str) -> str:
    with conn.transaction():
        state = jobs.retry_or_fail(conn, job, error, now=clock.now())
    log.warning("feature_retry", job=job.id, run=job.run_id, attempt=job.attempts + 1, state=state, error=error)
    return f"retry_{state}"
