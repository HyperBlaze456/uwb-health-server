"""M9-6 (14.6 속도): 착용자 1년치 합성 데이터에서 하루 계산 300 ms 이하, 추세 조회(지표 5개 × 1년) 20 ms 이하이고 색인을 탐.

데이터: 착용자 하나, 장치 하나, 365일. 하루에 1시간 구간 12개, 구간마다 2.5분 걷기 구간 3개(하루 1.5시간 걷기,
14.5의 가정). 잰 날의 앞뒤 사흘(7일)은 양발 1.1초 간격의 실제 크기 보폭 줄(하루 약 9,800줄)이고, 나머지 날은 걷기 구간마다
발당 5줄입니다(1년을 모두 실제 크기로 만들면 줄이 360만 개라 시험이 너무 오래 걸림. 하루 계산은 그날의 줄만 색인으로 읽으므로
다른 날의 줄 수는 그날 계산 시간에 거의 들지 않음). 일별 값은 summary 작업으로 1년을 실제로 계산해 만들고, 추세 색인이
실제와 비슷하게 쓰이도록 다른 착용자 49명의 일별 값 1년치를 더 넣습니다.
"""

import statistics
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from uwbserver.pipeline import clock, jobs, metrics
from uwbserver.process.api import create_app
from uwbserver.registry import rules, store
from uwbserver.summary import run as summary_run
from uwbserver.summary import store as summary_store

pytestmark = [pytest.mark.db, pytest.mark.slow]

DAYS = 365
FIRST = date(2025, 10, 27)  # 시험 시계(2026-10-26)까지 365일
HEAVY = (200, 207)  # 실제 크기 줄을 넣는 날(첫날부터 센 순번, 끝 제외)
MEASURED = FIRST + timedelta(days=203)
DEV = "head-aabbcc000001"
TREND = ["period.bout_count", "period.walking_time_s", "period.speed_median_mps", "period.stride_length_median_m",
         "period.stride_time_cv"]  # fmt: skip


def _fill_year(env, subject_id: int) -> None:
    t0 = clock.day_start(FIRST)
    env.q("INSERT INTO registry.device (id, mac, registered_at) VALUES (%s, 'aa:bb:cc:00:00:01', %s)", (DEV, t0))
    env.q("INSERT INTO registry.wear (subject_id, device_id, started_at) VALUES (%s, %s, %s)", (subject_id, DEV, t0))
    env.q(
        """
        INSERT INTO segment.segment (device_id, head_boot_id, geometry_hash, sides, first_seq, last_seq, n_chunks,
            first_head_us, last_head_us, utc_offset_us, time_source, start_at, end_at, state, closed_reason,
            last_chunk_at)
        SELECT %(d)s, g, 1, 'both', 0, 3599, 3600, 0, 3599000000, 0, 'server',
               %(t0)s + (g / 12) * interval '1 day' + (8 + g %% 12) * interval '1 hour',
               %(t0)s + (g / 12) * interval '1 day' + (9 + g %% 12) * interval '1 hour', 'closed', 'silence_60s',
               %(t0)s + (g / 12) * interval '1 day' + (9 + g %% 12) * interval '1 hour'
        FROM generate_series(0, %(n)s - 1) g
        """,
        {"d": DEV, "t0": t0, "n": DAYS * 12},
    )
    env.q(
        """
        INSERT INTO analysis.run (segment_id, segment_revision, input, n_chunks_sent, origin_head_us, origin_at,
            core_version, context, status, result_path, result_bytes, result_sha256, started_at, finished_at)
        SELECT id, 0, 'raw', 3600, 0, start_at, 'x', '{}', 'done', '/data/result/' || id || '.json.gz', 1,
               '\\x00', end_at, end_at
        FROM segment.segment
        """
    )
    env.q(
        """
        INSERT INTO feature.unpack (run_id, catalog_version, formats, n_records, n_dropped_records, n_bad_values,
            unknown_fields, unknown_tables, core_version, unpacked_at)
        SELECT id, 1, '{"strides": "gaitfusion.stride/1"}', 0, 0, 0, '{}', '{}', 'x', finished_at FROM analysis.run
        """
    )
    env.q(
        """
        INSERT INTO analysis.bout (run_id, idx, start_at, end_at, mode, sides, quality)
        SELECT r.id, k, r.origin_at + k * interval '20 minutes', r.origin_at + k * interval '20 minutes' + interval '150 s',
               CASE WHEN k = 2 THEN 'imu_only' ELSE 'fused' END, 'both', '{}'
        FROM analysis.run r CROSS JOIN generate_series(0, 2) k
        """
    )
    heavy_from, heavy_to = (clock.day_start(FIRST + timedelta(days=d)) for d in HEAVY)
    env.q(
        """
        INSERT INTO feature.record (bout_id, kind, idx, side, start_at, end_at, v)
        SELECT b.id, 'stride', 2 * k + s, side,
               b.start_at + make_interval(secs => 1.1 * k + 0.55 * s),
               b.start_at + make_interval(secs => 1.1 * k + 0.55 * s + 1.1),
               jsonb_build_object('side', side, 'start_s', 1.1 * k + 0.55 * s, 'end_s', 1.1 * k + 0.55 * s + 1.1,
                   'duration_s', 1.1 + 0.01 * (k %% 5), 'length_m', 1.21 + 0.005 * (k %% 3),
                   'speed_mps', 1.1 + 0.002 * (k %% 7), 'clearance_m', 0.07, 'heading_deg', 0.0, 'swing_s', 0.45,
                   'stance_s', 0.65, 'stance_ratio', 0.59, 'uwb_support', 0.8)
        FROM analysis.bout b
        CROSS JOIN (VALUES (0, 'left'), (1, 'right')) AS f(s, side)
        CROSS JOIN LATERAL generate_series(0, CASE WHEN b.start_at >= %s AND b.start_at < %s THEN 135 ELSE 4 END) k
        """,
        (heavy_from, heavy_to),
    )
    env.q("ANALYZE")


def _other_subjects(env, n: int = 49, per_day: int = 30) -> None:
    """다른 착용자 n명의 일별 값 1년치(추세 색인이 착용자 여럿인 표에서 쓰이는지 보려고)."""
    env.q("INSERT INTO registry.subject (code) SELECT 'X' || g FROM generate_series(1, %s) g", (n,))
    env.q(
        """
        INSERT INTO summary.day (subject_id, date, catalog_version, computed_at)
        SELECT s.id, %s::date + d, 1, now() FROM registry.subject s CROSS JOIN generate_series(0, %s - 1) d
        WHERE s.code LIKE 'X%%'
        """,
        (FIRST, DAYS),
    )
    period = [m.id for m in metrics.load().metrics_of("period")]
    env.q(
        """
        INSERT INTO summary.daily_value (subject_id, date, metric, side, value, n, sufficient)
        SELECT d.subject_id, d.date, m.metric, m.side, 1.0, 10, true
        FROM summary.day d
        JOIN registry.subject s ON s.id = d.subject_id AND s.code LIKE 'X%%'
        CROSS JOIN (SELECT metric, CASE WHEN k %% 2 = 0 THEN NULL ELSE 'left' END AS side
                    FROM unnest(%s::text[]) WITH ORDINALITY AS u(metric, k) LIMIT %s) m
        """,
        (period, per_day),
    )
    env.q("ANALYZE")


def _times(fn, k: int) -> list[float]:
    out = []
    for _ in range(k):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return out


def _p95(times: list[float]) -> float:
    return statistics.quantiles(times, n=20)[18]


def _plan(env, sql: str, params) -> str:
    return "\n".join(r["QUERY PLAN"] for r in env.q("EXPLAIN " + sql, params))


def test_year_of_data(env):
    conn = env.conn("api")
    with conn.transaction():
        sid = store.insert_subject(conn, code="Y1", height_m=1.7, leg_length_m=0.9, paretic_side=None)
        store.insert_user(conn, email="y@x.org", password_hash=rules.hash_password("password-123"), role="member",
                          subject_id=sid, now=clock.now())  # fmt: skip
    t = time.perf_counter()
    _fill_year(env, sid)
    fill_s = time.perf_counter() - t
    assert env.scalar("SELECT count(*) FROM analysis.bout") == DAYS * 36

    # 1년을 summary 작업 하나로 계산(SM-1의 길 그대로)
    core = env.conn("core")
    with core.transaction():
        jobs.enqueue_summary(core, DEV, FIRST, FIRST + timedelta(days=DAYS - 1), now=clock.now())
    with core.transaction():
        job = jobs.claim(core, ("summary",), worker="t", now=clock.now())
    t = time.perf_counter()
    assert summary_run.handle(core, job) == "done"
    year_s = time.perf_counter() - t
    assert env.scalar("SELECT count(*) FROM summary.day WHERE subject_id = %s", (sid,)) == DAYS
    _other_subjects(env)

    # 하루 계산(실제 크기의 날): 그날의 걷기 구간 36개, 보폭 줄 약 9,800개
    start = clock.day_start(MEASURED)
    n_rec = env.scalar(
        "SELECT count(*) FROM feature.record r JOIN analysis.bout b ON b.id = r.bout_id WHERE b.start_at >= %s "
        "AND b.start_at < %s", (start, start + timedelta(days=1)))  # fmt: skip
    assert n_rec == 36 * 2 * 136
    catalog = metrics.load()
    day_ms = _times(lambda: summary_run.compute_day(core, catalog, sid, MEASURED), 20)

    # 추세 조회: 지표 5개(그중 발마다 둘) × 1년 = 7줄 × 365점
    with TestClient(create_app()) as api:
        token = api.post("/v1/auth/token", json={"email": "y@x.org", "password": "password-123"}).json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        params = {"metrics": ",".join(TREND), "from": FIRST.isoformat(), "to": (FIRST + timedelta(days=DAYS - 1)).isoformat()}
        body = api.get(f"/v1/subjects/{sid}/trend", params=params, headers=h).json()
        assert [len(s["points"]) for s in body] == [DAYS] * 7
        trend_ms = _times(lambda: api.get(f"/v1/subjects/{sid}/trend", params=params, headers=h), 60)

    results = {"fill_s": round(fill_s, 1), "year_job_s": round(year_s, 1), "day_median_ms": round(statistics.median(day_ms), 1),
               "day_p95_ms": round(_p95(day_ms), 1), "trend_median_ms": round(statistics.median(trend_ms), 2),
               "trend_p95_ms": round(_p95(trend_ms), 2)}  # fmt: skip
    print(results)
    assert results["day_p95_ms"] <= 300, results
    assert results["trend_p95_ms"] <= 20, results

    # 색인을 타는지(9절 규칙)
    end = start + timedelta(days=1)
    plans = {
        "trend": _plan(env, _sql(summary_store.trend), (sid, TREND, FIRST, MEASURED)),
        "day_bouts": _plan(env, _sql(summary_store.day_bouts), (start, end, sid)),
        "records": _plan(env, _sql(summary_store.records_of_bouts),
                         ([r["id"] for r in summary_store.day_bouts(core, sid, start, end)],)),
        "wear": _plan(env, _sql(summary_store.wear_seconds),
                      {"subject": sid, "start": start, "end": end, "lookback": start - summary_store.SEGMENT_LOOKBACK}),
    }  # fmt: skip
    assert "Seq Scan on daily_value" not in plans["trend"] and "daily_value_subject_id" in plans["trend"], plans["trend"]
    assert "Seq Scan on bout" not in plans["day_bouts"], plans["day_bouts"]
    assert "Seq Scan on record" not in plans["records"], plans["records"]
    assert "Seq Scan on segment " not in plans["wear"], plans["wear"]


def _sql(fn) -> str:
    """store 함수 안의 SQL 글자(EXPLAIN용)."""
    import inspect
    import re

    src = inspect.getsource(fn)
    m = re.search(r'"""\s*(SELECT.*?)"""', src, re.S) or re.search(r'"(SELECT[^"]*)"', src)
    return m.group(1)
