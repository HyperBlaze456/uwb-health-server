"""summary 단계 실행: `summary {device_id, date_from, date_to}` 작업 하나 → 착용자·날짜마다 summary.day와
summary.daily_value 행 (SM-1–SM-3).

그 장치에 범위와 겹치는 착용 기록이 있는 착용자마다, 범위의 한국 날짜마다(SM-2):
그날 시작한 그 착용자의 현재 run 걷기 구간과 그 줄을 읽고 → metrics.bouts_from_rows로 풀이 → metrics.period_values로
계산(보고서와 같은 함수, 14.3) → 한 트랜잭션에서 그 착용자·날짜의 행을 지우고 값이 있는 것만 새로 넣음. 걷기가 없는
날은 행이 없습니다. 날짜마다 따로 커밋하므로 중간에 멈춰도 다시 돌면 같은 결과입니다.

아직 풀리지 않은 run의 걷기 구간은 줄 없이 셉니다(횟수·시간). 그 run이 풀리면 FT-8이 같은 날짜를 다시 넣습니다.

실패: 지표 목록을 읽지 못함, DB 오류 → PL-2의 재시도. 지표 목록이 period 계산과 맞지 않음(ValueError) → 바로 failed.
"""

import time
from datetime import date, timedelta

import psycopg

from uwbserver.pipeline import clock, jobs, log, metrics
from uwbserver.pipeline.jobs import Job
from uwbserver.summary import store

ONE_DAY = timedelta(days=1)


def handle(conn: psycopg.Connection, job: Job) -> str:
    """작업 하나를 처리합니다. 결과를 짧은 말로 돌려줍니다(로그·시험용)."""
    t0 = time.perf_counter()
    n_days = n_values = 0
    try:
        catalog = metrics.load()
        subjects = store.subjects_on_device(
            conn, job.device_id, clock.day_start(job.date_from), clock.day_start(job.date_to + ONE_DAY)
        )
        for subject_id in subjects:
            d = job.date_from
            while d <= job.date_to:
                n_values += compute_day(conn, catalog, subject_id, d)
                n_days += 1
                d += ONE_DAY
        with conn.transaction():
            jobs.finish(conn, job.id, now=clock.now())
    except (metrics.CatalogError, OSError, psycopg.Error) as e:
        return _retry(conn, job, f"{type(e).__name__}: {e}")
    except ValueError as e:  # 지표 목록이 RP-3 계산과 맞지 않음: 목록을 고치기 전에는 다시 해도 같음
        with conn.transaction():
            jobs.fail(conn, job.id, f"catalog_mismatch: {e}", now=clock.now())
        log.error("summary_failed", job=job.id, device=job.device_id, error=str(e))
        return "failed"
    log.info("summary_done", job=job.id, device=job.device_id, date_from=job.date_from.isoformat(),
             date_to=job.date_to.isoformat(), subjects=len(subjects), subject_days=n_days, values=n_values,
             ms=round((time.perf_counter() - t0) * 1000, 1))  # fmt: skip
    return "done"


def compute_day(conn: psycopg.Connection, catalog: metrics.Catalog, subject_id: int, day: date) -> int:
    """착용자 하루(SM-2, SM-3)를 계산해 바꿔 넣습니다. 넣은 값의 수를 돌려줍니다(걷기가 없으면 0, 행 없음)."""
    start, end = clock.day_start(day), clock.day_start(day + ONE_DAY)
    bouts = store.day_bouts(conn, subject_id, start, end)
    rows: list[tuple] = []
    if bouts:
        records = store.records_of_bouts(conn, [b["id"] for b in bouts])
        formats = {b["run_id"]: b["formats"] for b in bouts}
        data = metrics.bouts_from_rows(catalog, bouts, records, formats)
        wear_s = store.wear_seconds(conn, subject_id, start, end)
        rows = [
            (subject_id, day, v.metric, v.side, float(v.value), v.n, v.sufficient)
            for v in metrics.period_values(catalog, data, wear_time_s=wear_s)
            if v.value is not None
        ]
    with conn.transaction():
        store.delete_values(conn, subject_id, day)
        store.delete_day(conn, subject_id, day)
        if rows:
            store.insert_day(conn, subject_id, day, catalog.version, clock.now())
            store.copy_values(conn, rows)
    return len(rows)


def _retry(conn: psycopg.Connection, job: Job, error: str) -> str:
    with conn.transaction():
        state = jobs.retry_or_fail(conn, job, error, now=clock.now())
    log.warning("summary_retry", job=job.id, device=job.device_id, attempt=job.attempts + 1, state=state, error=error)
    return f"retry_{state}"
