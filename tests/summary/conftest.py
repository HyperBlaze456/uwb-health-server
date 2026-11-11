"""summary 시험 공용: 시각을 정확히 정한 합성 구간·run·걷기 구간·보폭 줄을 SQL로 넣는 도우미, 기대 값 계산, 앱 API 준비물.

가짜 gaitfusion을 거치는 시험(끝난 run, 재분석)은 tests.support를 쓰고, 날짜 경계처럼 시각이 중요한 시험은 여기 도우미로
행을 바로 넣습니다. 기대 값은 store를 거치지 않고 시험 안의 SQL과 metrics.period_values로 따로 계산합니다(M9 시험 4번).
"""

import itertools
from datetime import date, datetime, timedelta

import pytest

from tests.feature.conftest import admin, api, people  # noqa: F401 - 같은 앱 API 준비물을 씀
from tests.support import make_core, run_jobs
from uwbserver.pipeline import clock, metrics

V1 = "gaitfusion.stride/1"
_seq = itertools.count(1)


def subject(env, code: str) -> int:
    return env.scalar("INSERT INTO registry.subject (code) VALUES (%s) RETURNING id", (code,))


def device(env, device_id: str) -> str:
    mac = ":".join(device_id[5:][i : i + 2] for i in range(0, 12, 2))
    env.q("INSERT INTO registry.device (id, mac, registered_at) VALUES (%s, %s, %s)", (device_id, mac, clock.now()))
    return device_id


def wear(env, subject_id: int, device_id: str, start: datetime, end: datetime | None = None) -> int:
    return env.scalar(
        "INSERT INTO registry.wear (subject_id, device_id, started_at, ended_at) VALUES (%s, %s, %s, %s) RETURNING id",
        (subject_id, device_id, start, end),
    )


def segment(env, device_id: str, start: datetime, end: datetime) -> int:
    k = next(_seq)
    return env.scalar(
        """
        INSERT INTO segment.segment (device_id, head_boot_id, geometry_hash, sides, first_seq, last_seq, n_chunks,
            first_head_us, last_head_us, utc_offset_us, time_source, start_at, end_at, state, closed_reason,
            last_chunk_at)
        VALUES (%s, %s, 1, 'both', 0, 9, 10, 0, 9000000, 0, 'server', %s, %s, 'closed', 'silence_60s', %s)
        RETURNING id
        """,
        (device_id, k, start, end, end),
    )


def run(env, segment_id: int, *, formats: dict | None = None, unpacked: bool = True) -> int:
    """done run 하나. unpacked면 feature.unpack 행도(formats = 응답이 밝힌 형식)."""
    k = next(_seq)
    origin = env.scalar("SELECT start_at FROM segment.segment WHERE id = %s", (segment_id,))
    run_id = env.scalar(
        """
        INSERT INTO analysis.run (segment_id, segment_revision, input, n_chunks_sent, origin_head_us, origin_at,
            core_version, context, status, result_path, result_bytes, result_sha256, started_at, finished_at)
        VALUES (%s, 0, 'raw', 10, 0, %s, 'x', '{}', 'done', %s, 1, '\\x00', %s, %s)
        RETURNING id
        """,
        (segment_id, origin, f"/data/result/seed-{k}.json.gz", clock.now(), clock.now()),
    )
    if unpacked:
        env.q(
            """
            INSERT INTO feature.unpack (run_id, catalog_version, formats, n_records, n_dropped_records, n_bad_values,
                unknown_fields, unknown_tables, core_version, unpacked_at)
            VALUES (%s, 1, %s, 0, 0, 0, '{}', '{}', 'x', %s)
            """,
            (run_id, _json(formats if formats is not None else {"strides": V1}), clock.now()),
        )
    return run_id


def _json(x):
    from psycopg.types.json import Jsonb

    return Jsonb(x)


def stride(side: str, t: float, **v) -> dict:
    """보폭 줄 하나(걷기 구간 시작에서 t초). 값은 가짜 gaitfusion과 같은 꼴, 주지 않은 값은 보통 걸음."""
    base = {"duration_s": 1.1, "length_m": 1.21, "speed_mps": 1.1, "clearance_m": 0.07, "swing_s": 0.45,
            "stance_s": 0.65, "stance_ratio": 0.59, "uwb_support": 0.8}  # fmt: skip
    return {"side": side, "start_s": t, "end_s": t + 1.1, **base, **v}


def walk(seconds: float, *, sides=("left", "right"), **v) -> list[dict]:
    """걷기 구간 길이만큼 발마다 1.1초 간격 보폭 줄. 보폭 시간에 작은 흔들림을 넣어 변동 지표도 값이 나옴."""
    out = []
    for side in sides:
        t, i = 0.0, 0
        while t + 1.1 <= seconds:
            out.append(stride(side, t, duration_s=round(1.1 + 0.01 * (i % 5), 6), **v))
            t += 1.1
            i += 1
    return out


def bout(env, run_id: int, start: datetime, seconds: float, strides: list[dict], *, mode: str = "fused") -> int:
    idx = env.scalar("SELECT count(*) FROM analysis.bout WHERE run_id = %s", (run_id,))
    sides = {s["side"] for s in strides} or {"left"}
    bout_id = env.scalar(
        """
        INSERT INTO analysis.bout (run_id, idx, start_at, end_at, mode, sides, quality)
        VALUES (%s, %s, %s, %s, %s, %s, '{}') RETURNING id
        """,
        (run_id, idx, start, start + timedelta(seconds=seconds), mode, "both" if len(sides) == 2 else sides.pop()),
    )
    for i, s in enumerate(strides):
        env.q(
            """
            INSERT INTO feature.record (bout_id, kind, idx, side, start_at, end_at, v)
            VALUES (%s, 'stride', %s, %s, %s, %s, %s)
            """,
            (bout_id, i, s["side"], start + timedelta(seconds=s["start_s"]), start + timedelta(seconds=s["end_s"]),
             _json(s)),
        )  # fmt: skip
    return bout_id


# --- 계산과 검사 ------------------------------------------------------------------------


def summarize(env) -> list[str]:
    """대기 중인 summary 작업을 다 처리(core의 작업 스레드와 같은 길)."""
    return run_jobs(env, make_core(), ("summary",))


def enqueue(env, device_id: str, d1: date, d2: date) -> None:
    from uwbserver.pipeline import jobs

    conn = env.conn("core")
    with conn.transaction():
        jobs.enqueue_summary(conn, device_id, d1, d2, now=clock.now())


def stored(env, subject_id: int, day: date) -> dict:
    """그날 저장된 값: {(지표, 발): (값, n, 충분)}."""
    rows = env.q(
        "SELECT metric, side, value, n, sufficient FROM summary.daily_value WHERE subject_id = %s AND date = %s",
        (subject_id, day),
    )
    return {(r["metric"], r["side"]): (r["value"], r["n"], r["sufficient"]) for r in rows}


def expected(env, subject_id: int, day: date) -> dict:
    """그날을 기간으로 준 period_values(RP-2·RP-3)를 store 없이 계산: 현재 run의 걷기 구간 중 그날 시작했고 그 착용자의
    것, 그 줄, 그 run의 형식, 그 착용자의 구간이 그날과 겹친 시간. 값이 있는 것만, stored와 같은 꼴."""
    start = clock.day_start(day)
    end = clock.day_start(day + timedelta(days=1))
    bouts = env.q(
        """
        SELECT b.id, b.run_id, b.start_at, b.end_at, b.mode FROM analysis.bout b
        JOIN analysis.bout_subject bs ON bs.bout_id = b.id
        WHERE bs.subject_id = %s AND b.start_at >= %s AND b.start_at < %s
          AND b.run_id = (SELECT max(r2.id) FROM analysis.run r2
                          WHERE r2.segment_id = (SELECT segment_id FROM analysis.run WHERE id = b.run_id))
        ORDER BY b.id
        """,
        (subject_id, start, end),
    )
    if not bouts:
        return {}
    ids = [b["id"] for b in bouts]
    records = env.q("SELECT bout_id, kind, side, v FROM feature.record WHERE bout_id = ANY(%s) ORDER BY id", (ids,))
    formats = {r["run_id"]: r["formats"] for r in env.q("SELECT run_id, formats FROM feature.unpack")}
    wear_s = 0.0
    for s in env.q(
        """
        SELECT s.start_at, s.end_at FROM segment.segment s JOIN analysis.segment_subject ss ON ss.segment_id = s.id
        WHERE ss.subject_id = %s
        """,
        (subject_id,),
    ):
        wear_s += max(0.0, (min(s["end_at"], end) - max(s["start_at"], start)).total_seconds())
    cat = metrics.load()
    values = metrics.period_values(cat, metrics.bouts_from_rows(cat, bouts, records, formats), wear_time_s=wear_s)
    return {(v.metric, v.side): (float(v.value), v.n, v.sufficient) for v in values if v.value is not None}


@pytest.fixture
def seoul_day():
    """한국 날짜 d의 h시 m분(UTC 시각)."""

    def at(d: date, h: int, m: int = 0, s: int = 0) -> datetime:
        return clock.day_start(d) + timedelta(hours=h, minutes=m, seconds=s)

    return at
