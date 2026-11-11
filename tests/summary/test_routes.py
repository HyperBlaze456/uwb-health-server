"""앱 API (14.8절, SM-5): GET /v1/metrics, GET /v1/subjects/{id}/trend. M9 시험 5번."""

from datetime import date, timedelta

import pytest

from tests.summary.conftest import bout, enqueue, run, segment, stored, summarize, walk
from uwbserver.pipeline import metrics

pytestmark = pytest.mark.db

DAYS = [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 23)]


@pytest.fixture
def three_days(env, people, device, seoul_day) -> int:
    """A가 사흘(10-20, 10-21, 10-23) 걸음. 10-23은 왼발만. A의 착용은 이 날들을 덮도록 앞당김."""
    env.q("UPDATE registry.wear SET started_at = '2026-10-01T00:00:00Z'")
    dev = device.device.id
    for k, d in enumerate(DAYS):
        r = run(env, segment(env, dev, seoul_day(d, 10), seoul_day(d, 11)))
        sides = ("left",) if k == 2 else ("left", "right")
        bout(env, r, seoul_day(d, 10, 5), 40 + 10 * k, walk(40 + 10 * k, sides=sides))
        bout(env, r, seoul_day(d, 10, 30), 15, walk(15, sides=sides))
    enqueue(env, dev, DAYS[0], DAYS[-1])
    summarize(env)
    return people["A"]


def test_metrics_lists_the_catalog(api, people):
    r = api.get("/v1/metrics", headers=people["b"])  # 로그인한 누구나
    assert r.status_code == 200, r.text
    cat = metrics.load()
    body = r.json()
    assert body["catalog_version"] == cat.version
    assert body["metrics"] == [
        {"id": m.id, "level": m.level, "label": m.label, "unit": m.unit, "meaning": m.meaning, "trust": m.trust,
         "needs": m.needs, "show": m.show}
        for m in cat.metrics
    ]  # fmt: skip
    assert len(body["metrics"]) == len(cat.metrics) >= 33
    assert api.get("/v1/metrics").status_code == 401


def test_trend_returns_daily_values_per_metric_and_side(env, api, admin, people, three_days):
    a = three_days
    q = "period.bout_count,period.stride_length_median_m,period.wear_time_s"
    r = api.get(f"/v1/subjects/{a}/trend", params={"metrics": q, "from": "2026-10-19", "to": "2026-10-31"},
                headers=people["a"])  # fmt: skip
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(s["metric"], s["side"]) for s in body] == [
        ("period.bout_count", None),
        ("period.stride_length_median_m", "left"),
        ("period.stride_length_median_m", "right"),
        ("period.wear_time_s", None),
    ]  # 요청한 지표 순서, 발은 없음·왼발·오른발
    for s in body:
        want_days = DAYS if s["side"] != "right" else DAYS[:2]  # 10-23은 왼발만
        assert [p["date"] for p in s["points"]] == [d.isoformat() for d in want_days]
        for p in s["points"]:
            assert set(p) == {"date", "value", "n", "sufficient"}
            value, n, sufficient = stored(env, a, date.fromisoformat(p["date"]))[(s["metric"], s["side"])]
            assert (p["value"], p["n"], p["sufficient"]) == (value, n, sufficient)
    assert [p["value"] for p in body[0]["points"]] == [2, 2, 2]
    assert body[3]["points"][0]["value"] == 3600

    # 범위를 좁히면 그 안의 날만(양 끝 포함), admin도 봄
    r = api.get(f"/v1/subjects/{a}/trend", params={"metrics": "period.bout_count", "from": "2026-10-21",
                                                   "to": "2026-10-23"}, headers=admin)  # fmt: skip
    assert [p["date"] for p in r.json()[0]["points"]] == ["2026-10-21", "2026-10-23"]
    # 값이 없는 지표는 나오지 않음
    r = api.get(f"/v1/subjects/{a}/trend", params={"metrics": "period.bout_count", "from": "2026-11-01",
                                                   "to": "2026-11-05"}, headers=admin)  # fmt: skip
    assert r.json() == []


def test_trend_default_range_is_the_last_366_days(api, people, three_days):
    """from·to가 없으면 오늘(한국)까지 366일. 시험 시계는 2026-10-26."""
    r = api.get(f"/v1/subjects/{three_days}/trend", params={"metrics": "period.bout_count"}, headers=people["a"])
    assert r.status_code == 200, r.text
    assert [p["date"] for p in r.json()[0]["points"]] == [d.isoformat() for d in DAYS]


def test_trend_of_someone_elses_subject_is_404(api, admin, people, three_days):
    url = f"/v1/subjects/{three_days}/trend"
    assert api.get(url, params={"metrics": "period.bout_count"}, headers=people["b"]).status_code == 404
    assert api.get("/v1/subjects/999999/trend", params={"metrics": "period.bout_count"}, headers=admin).status_code == 404
    assert api.get(url, params={"metrics": "period.bout_count"}).status_code == 401


@pytest.mark.parametrize(
    "params",
    [
        {"metrics": "period.no_such_metric"},  # 모르는 지표
        {"metrics": "stride.length_m"},  # period가 아닌 지표
        {"metrics": ""},
        {"metrics": ",".join(m.id for m in metrics.load().metrics_of("period")[:11])},  # 11개
        {"metrics": "period.bout_count", "from": "2026-10-26", "to": "2026-10-25"},  # from > to
        {"metrics": "period.bout_count", "from": "2025-10-24", "to": "2026-10-25"},  # 양 끝 포함 367일
        {"metrics": "period.bout_count", "from": "not-a-date"},
        {},  # metrics 없음
    ],
)
def test_trend_bad_request_is_422(api, people, params):
    r = api.get(f"/v1/subjects/{people['A']}/trend", params=params, headers=people["a"])
    assert r.status_code == 422, r.text


def test_trend_accepts_exactly_366_days_and_10_metrics(api, people):
    ten = ",".join(m.id for m in metrics.load().metrics_of("period")[:10])
    first = date(2026, 10, 25) - timedelta(days=365)
    r = api.get(f"/v1/subjects/{people['A']}/trend", params={"metrics": ten, "from": first.isoformat(),
                                                             "to": "2026-10-25"}, headers=people["a"])  # fmt: skip
    assert r.status_code == 200, r.text
