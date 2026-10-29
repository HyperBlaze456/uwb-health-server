"""M5-6: 착용자 1년치 합성 데이터에서 앱 요청 p95 20 ms·궤적 50 ms(9절), 주요 조회가 색인을 탐(EXPLAIN)."""

import statistics
import struct
import time
import zlib

import pytest
from fastapi.testclient import TestClient

from uwbserver.analysis import store as analysis_store
from uwbserver.pipeline import clock, files
from uwbserver.process.api import create_app
from uwbserver.registry import rules, store

pytestmark = [pytest.mark.db, pytest.mark.slow]
DAYS = 365
SEGS_PER_DAY = 12
BOUTS_PER_SEG = 3


def _fill_year(env, device_id: str, subject_id: int) -> None:
    t0 = clock.now()
    env.q("INSERT INTO registry.device (id, mac, registered_at) VALUES (%s, 'aa:bb:cc:00:00:01', %s)", (device_id, t0))
    env.q("INSERT INTO registry.wear (subject_id, device_id, started_at) VALUES (%s, %s, %s)", (subject_id, device_id, t0))
    env.q(
        """
        INSERT INTO segment.segment (device_id, head_boot_id, geometry_hash, sides, first_seq, last_seq, n_chunks,
            first_head_us, last_head_us, utc_offset_us, time_source, start_at, end_at, state, closed_reason,
            last_chunk_at)
        SELECT %(d)s, g / 100, 1, 'left', (g %% 100) * 4000, (g %% 100) * 4000 + 3599, 3600, 0, 3599000000, 0, 'server',
               %(t0)s + g * interval '2 hours', %(t0)s + g * interval '2 hours' + interval '1 hour', 'closed',
               'silence_60s', %(t0)s + g * interval '2 hours' + interval '1 hour'
        FROM generate_series(0, %(n)s - 1) g
        """,
        {"d": device_id, "t0": t0, "n": DAYS * SEGS_PER_DAY},
    )
    env.q(
        """
        INSERT INTO segment.segment_tag (segment_id, side, tag_mac, tag_boot_id, first_seen_at)
        SELECT id, 'left', 'aa:00:00:00:00:01', 1, start_at FROM segment.segment
        """
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
        INSERT INTO analysis.bout (run_id, idx, start_at, end_at, mode, sides, quality)
        SELECT r.id, k, r.origin_at + k * interval '15 minutes', r.origin_at + k * interval '15 minutes' + interval '1 minute',
               'fused', 'left', '{}'
        FROM analysis.run r CROSS JOIN generate_series(0, %s - 1) k
        """,
        (BOUTS_PER_SEG,),
    )
    env.q("ANALYZE")


def _trj(n: int) -> bytes:
    head = struct.pack("<4sBBHIId", b"TRJ1", 0, 13, 100, n, 0, 0.0)
    body = struct.pack(f"<{13 * n}f", *([0.5] * (13 * n)))
    data = head + body
    return data + struct.pack("<I", zlib.crc32(data))


def _p95(fn, k=60) -> float:
    times = []
    for _ in range(k):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    return statistics.quantiles(times, n=20)[18]


def test_year_of_data(env):
    conn = env.conn("api")
    with conn.transaction():
        sid = store.insert_subject(conn, code="Y1", height_m=1.7, leg_length_m=0.9, paretic_side=None)
        store.insert_user(conn, email="y@x.org", password_hash=rules.hash_password("password-123"), role="member",
                          subject_id=sid, now=clock.now())
    _fill_year(env, "head-aabbcc000001", sid)
    n_bouts = env.scalar("SELECT count(*) FROM analysis.bout")
    assert n_bouts == DAYS * SEGS_PER_DAY * BOUTS_PER_SEG

    # 궤적 하나: 걷기 1분 + 앞뒤 2초 = 6400 표본
    bout_id = env.scalar("SELECT max(id) FROM analysis.bout")
    path = files.trajectory_path(999999, 0, "left")
    files.write_atomic(path, _trj(6400))
    env.q("INSERT INTO analysis.trajectory VALUES (%s, 'left', %s, 100, now(), 6400, %s)", (bout_id, path, len(_trj(6400))))

    with TestClient(create_app()) as api:
        token = api.post("/v1/auth/token", json={"email": "y@x.org", "password": "password-123"}).json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        week_from = clock.from_us(clock.to_us(clock.now()) + 200 * 86400 * 1_000_000).isoformat()
        week_to = clock.from_us(clock.to_us(clock.now()) + 207 * 86400 * 1_000_000).isoformat()
        q_bouts = f"/v1/subjects/{sid}/bouts"
        params = {"from": week_from, "to": week_to, "limit": 1000}
        assert len(api.get(q_bouts, params=params, headers=h).json()) == 7 * SEGS_PER_DAY * BOUTS_PER_SEG
        results = {
            "me": _p95(lambda: api.get("/v1/me", headers=h)),
            "subject_bouts_week": _p95(lambda: api.get(q_bouts, params=params, headers=h)),
            "subject_wear": _p95(lambda: api.get(f"/v1/subjects/{sid}/wear", headers=h)),
            "trajectory_1min_25hz": _p95(lambda: api.get(f"/v1/bouts/{bout_id}/trajectory", headers=h)),
        }
    print({k: round(v, 2) for k, v in results.items()})
    assert results["trajectory_1min_25hz"] <= 50
    for k in ("me", "subject_bouts_week", "subject_wear"):
        assert results[k] <= 20, (k, results[k])

    # 색인을 타는지(9절 규칙): 착용자 걷기 구간 조회에 analysis.bout 순차 스캔이 없어야 함
    plan = "\n".join(
        r["QUERY PLAN"]
        for r in env.q(
            "EXPLAIN " + _subject_bouts_sql(),
            (clock.from_us(clock.to_us(clock.now()) + 200 * 86400 * 1_000_000),
             clock.from_us(clock.to_us(clock.now()) + 207 * 86400 * 1_000_000), sid, None, None, 100),
        )
    )
    assert "Seq Scan on bout" not in plan, plan


def _subject_bouts_sql() -> str:
    import inspect
    import re

    src = inspect.getsource(analysis_store.subject_bouts)
    return re.search(r'"""\s*(SELECT.*?)"""', src, re.S).group(1)
