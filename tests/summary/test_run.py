"""M9 일별 값 (SM-1–SM-3, FT-8). SPEC 14.10 M9 시험 2–4.

- 날짜 경계: 한국 자정을 넘는 걷기 구간은 시작한 날에만, UTC 자정 근처의 걷기 구간은 한국 날짜로.
- run이 끝나 풀리면 그날의 값이 생기고, 재분석 뒤에는 새 run의 값만 남음(옛 run에만 걷기가 있던 날은 행이 없어짐).
- 착용 기록을 고치면 잃은 착용자와 얻은 착용자의 그 날짜가 다시 계산됨.
- 저장된 그날의 값 = 그날을 기간으로 준 period_values(모든 값을 비교).
"""

from datetime import UTC, date, timedelta

import pytest

from tests.feature.conftest import patched_core
from tests.summary.conftest import (
    bout, device, enqueue, expected, run, segment, stored, subject, summarize, walk, wear,
)  # fmt: skip
from tests.support import closed_segment, make_core, run_jobs
from uwbserver.pipeline import clock, jobs, metrics

pytestmark = pytest.mark.db

D = date(2026, 10, 26)  # 한국 날짜
DEV = "head-aabbcc000001"


def _days(env, subject_id: int) -> list[date]:
    return [r["date"] for r in env.q("SELECT date FROM summary.day WHERE subject_id = %s ORDER BY date", (subject_id,))]


def _value(env, subject_id: int, day: date, metric: str, side=None):
    return stored(env, subject_id, day).get((metric, side), (None,))[0]


def test_bout_crossing_korean_midnight_counts_on_the_day_it_started(env, seoul_day):
    """SM-2: 자정을 넘는 걷기 구간은 시작한 날에만. UTC 자정 근처(한국 아침)의 걷기 구간은 한국 날짜로.
    자료가 있던 시간(period.wear_time_s)은 구간을 날짜 경계에서 잘라 셈(RP-3 given)."""
    a = subject(env, "A")
    device(env, DEV)
    wear(env, a, DEV, seoul_day(D, 0))
    s1 = segment(env, DEV, seoul_day(D, 23, 30), seoul_day(D, 24, 30))  # 한국 23:30–다음 날 00:30
    r1 = run(env, s1)
    bout(env, r1, seoul_day(D, 23, 59), 120, walk(120))  # 자정을 넘음 → D
    bout(env, r1, seoul_day(D, 24, 10), 30, walk(30))  # 다음 날 00:10 → D+1
    s2 = segment(env, DEV, seoul_day(D, 32, 40), seoul_day(D, 32, 59))  # D+1 08:40 KST = D 23:40 UTC
    r2 = run(env, s2)
    near_utc_midnight = seoul_day(D, 32, 55)
    assert near_utc_midnight.astimezone(clock.SEOUL).date() == D + timedelta(days=1)
    assert near_utc_midnight.astimezone(UTC).date() == D  # UTC로는 아직 D
    bout(env, r2, near_utc_midnight, 60, walk(60))

    enqueue(env, DEV, D - timedelta(days=1), D + timedelta(days=1))
    assert summarize(env) == ["done"]

    assert _days(env, a) == [D, D + timedelta(days=1)]  # D−1은 걷기가 없어 행이 없음
    assert _value(env, a, D, "period.bout_count") == 1
    assert _value(env, a, D, "period.walking_time_s") == 120
    assert _value(env, a, D, "period.wear_time_s") == 30 * 60
    nxt = D + timedelta(days=1)
    assert _value(env, a, nxt, "period.bout_count") == 2
    assert _value(env, a, nxt, "period.walking_time_s") == 90
    assert _value(env, a, nxt, "period.wear_time_s") == 30 * 60 + 19 * 60
    for day in (D, nxt):
        assert stored(env, a, day) == expected(env, a, day)
    day_row = env.one("SELECT catalog_version, computed_at FROM summary.day WHERE subject_id = %s AND date = %s", (a, D))
    assert day_row == {"catalog_version": metrics.load().version, "computed_at": clock.now()}


def test_day_uses_all_bouts_of_the_subject_whatever_device(env, seoul_day):
    """SM-2: 그 착용자의 그날 걷기 구간은 장치와 상관없이 모두(장치 하나의 작업이라도). 남의 것은 넣지 않음."""
    a, b = subject(env, "A"), subject(env, "B")
    dev2 = device(env, "head-aabbcc000002")
    device(env, DEV)
    wear(env, a, DEV, seoul_day(D, 0), seoul_day(D, 12))
    wear(env, a, dev2, seoul_day(D, 12))
    wear(env, b, DEV, seoul_day(D, 12))
    bout(env, run(env, segment(env, DEV, seoul_day(D, 8), seoul_day(D, 9))), seoul_day(D, 8, 10), 40, walk(40))
    bout(env, run(env, segment(env, dev2, seoul_day(D, 14), seoul_day(D, 15))), seoul_day(D, 14, 5), 50, walk(50))
    bout(env, run(env, segment(env, DEV, seoul_day(D, 16), seoul_day(D, 17))), seoul_day(D, 16, 5), 70, walk(70))

    enqueue(env, DEV, D, D)
    assert summarize(env) == ["done"]
    assert _value(env, a, D, "period.bout_count") == 2
    assert _value(env, a, D, "period.walking_time_s") == 90
    assert _value(env, a, D, "period.wear_time_s") == 2 * 3600
    assert _value(env, b, D, "period.bout_count") == 1
    assert _value(env, b, D, "period.walking_time_s") == 70
    for s in (a, b):
        assert stored(env, s, D) == expected(env, s, D)


def test_values_equal_period_values_of_that_day(env, seoul_day):
    """M9-4: 저장된 그날의 값이 그날을 기간으로 준 period_values의 값(값이 있는 것 전부)과 같음. 짧은·긴 걷기 구간,
    한 발, 모드가 섞인 하루."""
    a = subject(env, "A")
    device(env, DEV)
    wear(env, a, DEV, seoul_day(D, 0))
    r = run(env, segment(env, DEV, seoul_day(D, 7), seoul_day(D, 8)))
    bout(env, r, seoul_day(D, 7, 1), 8, walk(8))
    bout(env, r, seoul_day(D, 7, 5), 25, walk(25, speed_mps=0.9), mode="imu_only")
    bout(env, r, seoul_day(D, 7, 10), 45, walk(45, sides=("left",), speed_mps=1.0))
    bout(env, r, seoul_day(D, 7, 20), 95, walk(95, speed_mps=1.3))
    enqueue(env, DEV, D, D)
    assert summarize(env) == ["done"]
    got = stored(env, a, D)
    assert got == expected(env, a, D)
    assert len(got) >= 20 and ("period.stride_time_cv", "left") in got and ("period.stride_time_cv", "right") in got


def test_finished_run_gives_that_days_values_then_reanalysis_replaces_them(env, api, admin, people, device):
    """M9-3: run이 끝나 풀리면 그날 값이 생김. 재분석 뒤에는 새 run의 값만. 새 run에 걷기가 없으면 그날 행이 없어짐."""
    a = people["A"]
    seg = closed_segment(env, device)
    run_jobs(env, make_core(), ("analysis", "feature"))
    assert summarize(env) == ["done", "done"]  # 착용 기록을 만들 때 넣은 것(SM-1)과 풀기가 넣은 것(FT-8)
    day = clock.local_date(env.scalar("SELECT min(start_at) FROM analysis.bout"))
    assert _days(env, a) == [day]
    assert stored(env, a, day) == expected(env, a, day)
    assert _value(env, a, day, "period.speed_median_mps") == pytest.approx(1.1)
    assert _days(env, people["B"]) == []

    def slower(doc: dict) -> None:
        for b in doc["bouts"]:
            for s in b["strides"]:
                s["speed_mps"] = 0.9

    env.clock.advance(60)
    conn = env.conn("core")
    with conn.transaction():
        assert jobs.enqueue_analysis(conn, seg, now=clock.now())
    run_jobs(env, patched_core(slower), ("analysis", "feature"))
    assert summarize(env) == ["done"]
    assert _value(env, a, day, "period.speed_median_mps") == pytest.approx(0.9)
    assert stored(env, a, day) == expected(env, a, day)

    env.clock.advance(60)
    with conn.transaction():
        assert jobs.enqueue_analysis(conn, seg, now=clock.now())
    run_jobs(env, patched_core(lambda doc: doc.update(bouts=[])), ("analysis", "feature"))
    assert summarize(env) == ["done"]
    assert _days(env, a) == []
    assert env.scalar("SELECT count(*) FROM summary.daily_value") == 0


def test_wear_edit_recomputes_both_subjects(env, api, admin, people, device, seoul_day):
    """M9-3 (SM-1): 착용 기록을 고치면 그 기간을 잃은 착용자와 얻은 착용자의 그 날짜가 다시 계산됨. 작업은 api가 넣음."""
    a, b = people["A"], people["B"]
    dev = device.device.id
    day = D - timedelta(days=1)  # A의 착용은 2026-10-25T00:00Z(한국 10-25 09:00)부터
    r = run(env, segment(env, dev, seoul_day(day, 18), seoul_day(day, 19)))
    bout(env, r, seoul_day(day, 18, 5), 60, walk(60))
    bout(env, r, seoul_day(day, 18, 30), 40, walk(40))
    summarize(env)
    assert _value(env, a, day, "period.bout_count") == 2

    wear_a = api.get(f"/v1/subjects/{a}/wear", headers=admin).json()[0]
    cut = seoul_day(day, 18, 20)
    r1 = api.patch(f"/v1/wear/{wear_a['id']}", json={"ended_at": cut.isoformat()}, headers=admin)
    assert r1.status_code == 200, r1.text
    # 고치기 전(끝 없음 → 오늘)과 고친 뒤(끝 = cut)를 합친 한국 날짜 범위
    assert _queued(env) == [(dev, date(2026, 10, 25), clock.today())]
    assert summarize(env) == ["done"]
    assert _value(env, a, day, "period.bout_count") == 1  # A는 cut 앞의 걷기 구간만
    assert _value(env, a, day, "period.walking_time_s") == 60

    r2 = api.post("/v1/wear", json={"subject_id": b, "device_id": dev, "started_at": cut.isoformat()}, headers=admin)
    assert r2.status_code == 201, r2.text
    assert _queued(env) == [(dev, day, clock.today())]  # 새 착용 기록(끝 없음 → 오늘)
    assert summarize(env) == ["done"]
    assert _value(env, b, day, "period.bout_count") == 1
    assert _value(env, b, day, "period.walking_time_s") == 40
    for s in (a, b):
        assert stored(env, s, day) == expected(env, s, day)


def _queued(env) -> list[tuple]:
    rows = env.q("SELECT device_id, date_from, date_to FROM pipeline.job WHERE kind = 'summary' AND state = 'queued'")
    return [(r["device_id"], r["date_from"], r["date_to"]) for r in rows]


def test_unknown_catalog_failure_retries(env, seoul_day, monkeypatch):
    """지표 목록을 읽지 못하면 PL-2의 재시도(값은 그대로)."""
    device(env, DEV)
    enqueue(env, DEV, D, D)

    def broken(*_a, **_k):
        raise metrics.CatalogError("broken")

    monkeypatch.setattr(metrics, "load", broken)
    assert summarize(env) == ["retry_queued"]


def test_skipped_reanalysis_removes_the_old_runs_daily_values(env, api, admin, people, device):
    """skipped run도 현재 run이 됨(AN-8). analysis가 skipped run을 저장하는 트랜잭션에서 옛 run의 걷기 구간 날짜로
    summary 작업을 넣으므로, 옛 run의 일별 값이 그날에서 빠짐."""
    a = people["A"]
    seg = closed_segment(env, device)
    run_jobs(env, make_core(), ("analysis", "feature"))
    summarize(env)
    day = clock.local_date(env.scalar("SELECT min(start_at) FROM analysis.bout"))
    assert _days(env, a) == [day]

    env.clock.advance(60)
    conn = env.conn("core")
    with conn.transaction():
        assert jobs.enqueue_analysis(conn, seg, now=clock.now())
    assert run_jobs(env, make_core("422"), ("analysis",)) == ["skipped"]
    assert summarize(env) == ["done"]
    assert _days(env, a) == []
    assert env.scalar("SELECT count(*) FROM summary.daily_value WHERE subject_id = %s", (a,)) == 0


def test_skipped_first_run_enqueues_no_summary(env, device):
    """옛 run의 걷기 구간이 없으면 넣지 않음."""
    closed_segment(env, device)
    assert run_jobs(env, make_core("422"), ("analysis",)) == ["skipped"]
    assert env.scalar("SELECT count(*) FROM pipeline.job WHERE kind = 'summary'") == 0
