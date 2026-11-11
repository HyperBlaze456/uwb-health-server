"""report/store.py와 맥락 모으기(chat.load_context)의 DB 시험 (SPEC RP-2, RP-3, RP-4, RP-7).

summary 시험의 도우미로 시각을 정한 구간·run·걷기 구간·보폭 줄을 바로 넣고, api 역할로 읽습니다(tools/ask.py와 같음).
기대 값은 store를 거치지 않고 시험 안의 SQL과 metrics.period_values로 따로 계산합니다.
"""

from datetime import date, timedelta

import pytest

from tests.summary.conftest import bout, device, run, segment, subject, walk, wear
from uwbserver.pipeline import clock, metrics
from uwbserver.report import chat, store

pytestmark = pytest.mark.db

DAY1 = date(2026, 10, 27)
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)


def at(d: date, h: int, m: int = 0):
    return clock.day_start(d) + timedelta(hours=h, minutes=m)


@pytest.fixture
def world(env):
    """착용자 S01: 이틀 동안 구간 셋. 기간 [DAY1, DAY3)에 드는 걷기 구간은 A(DAY1)와 C(DAY2 밤, 기간 끝을 넘음).
    E는 기간 앞에 시작해 기간으로 넘어오고, D는 기간 뒤, B는 다른 착용자와 겹쳐 주인이 모호함."""
    s = subject(env, "S01")
    env.q("UPDATE registry.subject SET height_m = 1.7, leg_length_m = 0.9, paretic_side = 'left' WHERE id = %s", (s,))
    other = subject(env, "S02")
    dev = device(env, "head-aabbccddeeff")
    wear(env, s, dev, at(DAY1, 0) - timedelta(days=40))
    wear(env, other, dev, at(DAY2, 9, 5), at(DAY2, 9, 30))  # B(09:10)만 모호, 구간(09:00 시작)은 아님

    seg0 = segment(env, dev, at(DAY1, 0) - timedelta(minutes=10), at(DAY1, 0, 20))
    seg1 = segment(env, dev, at(DAY1, 10), at(DAY1, 11))
    seg2 = segment(env, dev, at(DAY2, 9), at(DAY2, 10))
    seg3 = segment(env, dev, at(DAY2, 23, 50), at(DAY3, 0, 20))
    r0, r1, r2, r3 = run(env, seg0), run(env, seg1), run(env, seg2), run(env, seg3, unpacked=False)
    env.q("UPDATE analysis.run SET result_status = 'partial' WHERE id = %s", (r1,))
    ids = {
        "E": bout(env, r0, at(DAY1, 0) - timedelta(minutes=1), 120, walk(120)),
        "A": bout(env, r1, at(DAY1, 10, 5), 120, walk(120)),
        "B": bout(env, r2, at(DAY2, 9, 10), 60, walk(60)),
        "C": bout(env, r3, at(DAY2, 23, 58), 300, walk(300, sides=("left",))),
        "D": bout(env, r3, at(DAY3, 0, 5), 60, walk(60)),
    }
    return {"subject": s, "runs": (r0, r1, r2, r3), "bouts": ids}


def expected_values(env, bout_ids: list[int], wear_s: float):
    cat = metrics.load()
    bouts = env.q("SELECT id, run_id, start_at, end_at, mode FROM analysis.bout WHERE id = ANY(%s) ORDER BY start_at",
                  (bout_ids,))  # fmt: skip
    records = env.q("SELECT bout_id, kind, side, v FROM feature.record WHERE bout_id = ANY(%s) ORDER BY id", (bout_ids,))
    formats = {r["id"]: None for r in env.q("SELECT id FROM analysis.run")}
    formats |= {r["run_id"]: r["formats"] for r in env.q("SELECT run_id, formats FROM feature.unpack")}
    data = metrics.bouts_from_rows(cat, bouts, records, formats)
    return [(v.metric, v.side, v.value, v.n, v.sufficient) for v in metrics.period_values(cat, data, wear_time_s=wear_s)]


def test_period_selection_and_coverage(env, world):
    conn = env.conn("api")
    start, end = at(DAY1, 0), at(DAY3, 0)
    rows = store.period_bouts(conn, world["subject"], start, end)
    assert [r["id"] for r in rows] == [world["bouts"]["A"], world["bouts"]["C"]]
    assert store.ambiguous_bouts(conn, world["subject"], start, end) == 1
    # 구간 겹침: seg0의 20분 + seg1·seg2 한 시간씩 + seg3의 10분(기간 안쪽만)
    assert store.wear_seconds(conn, world["subject"], start, end) == pytest.approx(1200 + 3600 + 3600 + 600)

    ctx = chat.load_context(conn, metrics.load(), world["subject"], start, end)
    assert ctx["subject"] == {"code": "S01", "height_m": pytest.approx(1.7), "leg_length_m": pytest.approx(0.9),
                              "paretic_side": "left"}  # fmt: skip
    assert ctx["runs"] == sorted([world["runs"][1], world["runs"][3]])
    cov = ctx["coverage"]
    assert cov["wear_time_s"] == pytest.approx(9000.0)
    assert cov["bout_count"] == 2 and cov["walking_time_s"] == pytest.approx(420.0)
    assert cov["feet"] == ["left", "right"] and cov["sensor_position"] == "lateral_ankle"
    assert (cov["partial_runs"], cov["ambiguous_bouts"], cov["runs_not_unpacked"]) == (1, 1, 1)
    assert ctx["baseline_comparable"] is True

    got = [(v["metric"], v["side"], v["value"], v["n"], v["sufficient"]) for v in ctx["values"]]
    want = expected_values(env, [world["bouts"]["A"], world["bouts"]["C"]], 9000.0)
    assert got == want
    assert [v["id"] for v in ctx["values"]] == [f"v{i}" for i in range(1, len(want) + 1)]


def test_bout_crossing_the_boundary_belongs_to_the_period_it_starts_in(env, world):
    conn = env.conn("api")
    before = store.period_bouts(conn, world["subject"], at(DAY2, 0), at(DAY3, 0))
    after = store.period_bouts(conn, world["subject"], at(DAY3, 0), at(DAY3, 12))
    assert world["bouts"]["C"] in [r["id"] for r in before]
    assert world["bouts"]["C"] not in [r["id"] for r in after] and world["bouts"]["D"] in [r["id"] for r in after]


def put_days(env, subject_id: int, metric: str, n_days: int, value: float, *, side=None, sufficient=True):
    for i in range(1, n_days + 1):
        d = DAY1 - timedelta(days=i)
        env.q("INSERT INTO summary.day VALUES (%s, %s, 1, %s) ON CONFLICT DO NOTHING", (subject_id, d, clock.now()))
        env.q("INSERT INTO summary.daily_value VALUES (%s, %s, %s, %s, %s, 40, %s)",
              (subject_id, d, metric, side, value, sufficient))  # fmt: skip


def test_baseline_with_seven_or_more_sufficient_days(env, world):
    s = world["subject"]
    put_days(env, s, "period.wear_time_s", 8, 3600.0)
    put_days(env, s, "period.walking_time_s", 6, 100.0)  # 7일보다 적음
    put_days(env, s, "period.bout_count", 9, 5.0, sufficient=False)  # 충분하지 않은 날만
    env.q("INSERT INTO summary.day VALUES (%s, %s, 1, %s)", (s, DAY1 - timedelta(days=40), clock.now()))
    env.q("INSERT INTO summary.daily_value VALUES (%s, %s, 'period.walking_time_s', NULL, 1, 1, true)",
          (s, DAY1 - timedelta(days=40)))  # 30일 밖  # fmt: skip
    ctx = chat.load_context(env.conn("api"), metrics.load(), s, at(DAY1, 0), at(DAY3, 0))
    by = {v["metric"]: v for v in ctx["values"] if v["side"] is None}
    assert by["period.wear_time_s"]["baseline"] == {"median": 3600.0, "days": 8, "change": pytest.approx(5400.0)}
    assert "baseline" not in by["period.walking_time_s"]
    assert "baseline" not in by["period.bout_count"]


def test_sub_day_period_is_not_comparable(env, world):
    ctx = chat.load_context(env.conn("api"), metrics.load(), world["subject"], at(DAY1, 10), at(DAY1, 12))
    assert ctx["baseline_comparable"] is False
    assert ctx["coverage"]["bout_count"] == 1


def test_unknown_subject(env):
    with pytest.raises(LookupError):
        chat.load_context(env.conn("api"), metrics.load(), 999, at(DAY1, 0), at(DAY2, 0))
