"""기간의 지표 값 계산(SPEC 14.7 RP-3 period_values). M9 시험 1번과 2번의 일부(기간을 둘로 쪼갠 합).

DB 없는 순수 시험입니다. 입력은 손으로 만든 작은 걷기 구간과 보폭 줄이고, 기대 값은 시험 안에 계산을 적어 둡니다.
규칙의 뜻을 하나씩 보는 시험은 작은 지표 목록(아래 catalog_with)을 쓰고, 끝의 두 시험은 진짜 지표 목록을 씁니다.
"""

import copy
import math
import random
import tomllib

import pytest

from uwbserver.pipeline import metrics
from uwbserver.pipeline.metrics import BoutData, Record

V1 = "gaitfusion.stride/1"


# --- 준비물 ---------------------------------------------------------------------------


def pm(id_, rule, from_=None, *, where=None, by_side=False, min_n=1, type_="number", unit="s", needs="one_foot"):
    """period 지표 항목 하나(지표 목록 파일의 [[metric]]과 같은 꼴)."""
    entry = {
        "id": f"period.{id_}", "level": "period", "type": type_, "unit": unit, "producer": "server",
        "show": "detail", "needs": needs, "trust": "proxy", "label": id_, "meaning": id_, "sources": [],
        "rule": rule, "by_side": by_side, "min_n": min_n,
    }  # fmt: skip
    if from_ is not None:
        entry["from"] = from_
    if where is not None:
        entry["where"] = where
    return entry


def catalog_with(*period_entries) -> metrics.Catalog:
    """진짜 지표 목록에서 보폭 표와 보폭 지표는 그대로 두고, period 지표만 주어진 것으로 바꾼 목록."""
    doc = tomllib.loads(metrics.default_path().read_text())
    doc["metric"] = [m for m in doc["metric"] if m["level"] != "period"] + list(period_entries)
    return metrics.parse(doc)


def s(side, **values) -> Record:
    """보폭 줄 하나. 값의 키는 이름 부분만(duration_s → stride.duration_s)."""
    return Record(side, {f"stride.{k}": v for k, v in values.items() if v is not None})


def bout(duration_s, *strides, mode="fused") -> BoutData:
    return BoutData(duration_s, mode, {"stride": strides})


def by_key(values) -> dict:
    """[Value] → {(지표 이름 부분, 발): Value}. 같은 열쇠가 둘이면 시험 실패."""
    out = {(v.metric.split(".", 1)[1], v.side): v for v in values}
    assert len(out) == len(values)
    return out


def one(catalog, bouts, wear_time_s=None) -> metrics.Value:
    """지표가 하나뿐인 목록의 값 하나(발도 하나)."""
    [v] = metrics.period_values(catalog, bouts, wear_time_s=wear_time_s)
    return v


# --- rule마다 ------------------------------------------------------------------------


def test_rules_on_bout_durations():
    """bout.duration_s에서 내는 count·sum·max·median·p90·p95·ratio. 10초 미만 걷기 구간도 셈."""
    cat = catalog_with(
        pm("n", "count", "bout.duration_s", type_="integer", unit="count"),
        pm("total_s", "sum", "bout.duration_s"),
        pm("max_s", "max", "bout.duration_s"),
        pm("median_s", "median", "bout.duration_s"),
        pm("p90_s", "p90", "bout.duration_s"),
        pm("p95_s", "p95", "bout.duration_s"),
        pm("fused", "ratio", "bout.duration_s", where={"mode": "fused"}, unit="ratio"),
        pm("over_60s", "ratio", "bout.duration_s", where={"bout_min_s": 60}, unit="ratio"),
    )
    bouts = [bout(100, mode="imu_only"), bout(5, mode="imu_only"), bout(60), bout(30, mode="imu_only"), bout(10)]
    got = by_key(metrics.period_values(cat, bouts, wear_time_s=None))

    # 정렬 [5, 10, 30, 60, 100], 합 205
    assert got["n", None].value == 5 and type(got["n", None].value) is int
    assert got["total_s", None].value == 205
    assert got["max_s", None].value == 100
    assert got["median_s", None].value == 30
    # p90: 자리 (5−1)×0.9 = 3.6 → 60 + 0.6×(100−60) = 84.  p95: 3.8 → 60 + 0.8×40 = 92
    assert got["p90_s", None].value == pytest.approx(84)
    assert got["p95_s", None].value == pytest.approx(92)
    # ratio: 융합 구간 (10 + 60) ÷ 205,  60초 이상 (60 + 100) ÷ 205. n은 모든 걷기 구간의 수
    assert got["fused", None].value == pytest.approx(70 / 205)
    assert got["over_60s", None].value == pytest.approx(160 / 205)
    assert {v.n for v in got.values()} == {5}
    assert all(v.sufficient and v.reason is None for v in got.values())


def test_where_boundaries_min_inclusive_max_exclusive():
    """bout_min_s는 이상, bout_max_s는 미만. 10 s, 30 s, 60 s 경계와 길이별 나눔."""
    cat = catalog_with(
        pm("all_10", "sum", "stride.speed_mps", where={"bout_min_s": 10}, unit="m/s"),
        pm("b10_30", "sum", "stride.speed_mps", where={"bout_min_s": 10, "bout_max_s": 30}, unit="m/s"),
        pm("b30_60", "sum", "stride.speed_mps", where={"bout_min_s": 30, "bout_max_s": 60}, unit="m/s"),
        pm("over_60", "sum", "stride.speed_mps", where={"bout_min_s": 60}, unit="m/s"),
        pm("short", "count", "bout.duration_s", where={"bout_max_s": 30}, type_="integer", unit="count"),
    )
    # 걷기 구간마다 왼발 보폭 하나, 속도로 어느 구간인지 알아봄
    bouts = [bout(d, s("left", speed_mps=v)) for d, v in [(9.99, 1), (10, 2), (29.99, 3), (30, 4), (59.99, 5), (60, 6)]]
    got = by_key(metrics.period_values(cat, bouts, wear_time_s=None))

    assert (got["all_10", None].value, got["all_10", None].n) == (2 + 3 + 4 + 5 + 6, 5)  # 9.99 s 빠짐
    assert (got["b10_30", None].value, got["b10_30", None].n) == (2 + 3, 2)  # 10 s 듦, 30 s 빠짐
    assert (got["b30_60", None].value, got["b30_60", None].n) == (4 + 5, 2)  # 30 s 듦, 60 s 빠짐
    assert (got["over_60", None].value, got["over_60", None].n) == (6, 1)
    assert got["short", None].value == 3  # 9.99, 10, 29.99


def test_mode_condition():
    cat = catalog_with(pm("imu", "count", "bout.duration_s", where={"mode": "imu_only"}, type_="integer", unit="count"))
    v = one(cat, [bout(12), bout(12, mode="imu_only"), bout(40, mode="imu_only")])
    assert (v.value, v.n) == (2, 2)


def test_percentiles_interpolate_linearly():
    cat = catalog_with(
        pm("med", "median", "stride.speed_mps", where={"bout_min_s": 10}, unit="m/s"),
        pm("p90", "p90", "stride.speed_mps", where={"bout_min_s": 10}, unit="m/s"),
        pm("p95", "p95", "stride.speed_mps", where={"bout_min_s": 10}, unit="m/s"),
    )
    # 두 걷기 구간에서 모은 값 [4, 1, 3, 2] → 정렬 [1, 2, 3, 4]
    bouts = [
        bout(20, s("left", speed_mps=4), s("left", speed_mps=1)),
        bout(15, s("left", speed_mps=3), s("left", speed_mps=2)),
    ]
    got = by_key(metrics.period_values(cat, bouts, wear_time_s=None))
    assert got["med", None].value == pytest.approx(2.5)  # 자리 1.5 → 2 + 0.5×1
    assert got["p90", None].value == pytest.approx(3.7)  # 자리 3×0.9 = 2.7 → 3 + 0.7×1
    assert got["p95", None].value == pytest.approx(3.85)  # 자리 2.85 → 3 + 0.85×1
    assert {v.n for v in got.values()} == {4}

    # 값 하나면 모든 백분위가 그 값
    got = by_key(metrics.period_values(cat, [bout(20, s("left", speed_mps=1.3))], wear_time_s=None))
    assert {v.value for v in got.values()} == {1.3}


def test_cv_and_sd_are_medians_of_per_bout_values():
    """걷기 구간마다 표본 표준편차(n − 1)와 변동 계수, 그 중앙값. 값이 3개보다 적은 구간과 30초 미만 구간은 뺌."""
    cat = catalog_with(
        pm("cv", "cv", "stride.duration_s", where={"bout_min_s": 30}, unit="ratio", min_n=3),
        pm("sd_s", "sd", "stride.duration_s", where={"bout_min_s": 30}, min_n=3),
    )

    def strides(*ds):
        return [s("left", duration_s=d) for d in ds]

    bouts = [
        bout(40, *strides(1.0, 1.2, 1.4)),  # 평균 1.2, sd √((0.04 + 0 + 0.04)/2) = 0.2, cv 0.2/1.2 = 1/6
        bout(31, *strides(2, 2, 2, 2)),  # sd 0, cv 0
        bout(30, *strides(1, 3, 5)),  # 평균 3, sd √((4 + 0 + 4)/2) = 2, cv 2/3
        bout(90, *strides(1, 2)),  # 값 2개 → 뺌
        bout(29, *strides(1, 5, 9)),  # 30초 미만 → 뺌
    ]
    got = by_key(metrics.period_values(cat, bouts, wear_time_s=None))
    # cv의 중앙값: [0, 1/6, 2/3] → 1/6.  sd: [0, 0.2, 2] → 0.2.  n은 쓴 걷기 구간의 수
    assert got["cv", None].value == pytest.approx(1 / 6)
    assert got["sd_s", None].value == pytest.approx(0.2)
    assert got["cv", None].n == got["sd_s", None].n == 3
    assert got["cv", None].sufficient and got["sd_s", None].sufficient


def test_cv_skips_bout_with_zero_mean_but_sd_keeps_it():
    cat = catalog_with(
        pm("cv", "cv", "stride.heading_deg", where={"bout_min_s": 30}, unit="ratio"),
        pm("sd_deg", "sd", "stride.heading_deg", where={"bout_min_s": 30}, unit="deg"),
    )
    bouts = [
        bout(40, *[s("left", heading_deg=x) for x in (-1, 0, 1)]),  # 평균 0 → cv 뺌. sd √((1 + 0 + 1)/2) = 1
        bout(40, *[s("left", heading_deg=x) for x in (2, 4, 6)]),  # 평균 4, sd 2, cv 0.5
    ]
    got = by_key(metrics.period_values(cat, bouts, wear_time_s=None))
    assert (got["cv", None].value, got["cv", None].n) == (pytest.approx(0.5), 1)
    assert (got["sd_deg", None].value, got["sd_deg", None].n) == (pytest.approx(1.5), 2)  # [1, 2]의 중앙값


def test_cadence_is_median_of_120_over_stride_time():
    cat = catalog_with(pm("cadence_spm", "cadence", "stride.duration_s", where={"bout_min_s": 10}, unit="steps/min"))
    # 120/1.0 = 120, 120/1.2 = 100. 0은 뺌, 값이 없는 줄도 뺌 → [100, 120]의 중앙값 110, n 2
    v = one(cat, [bout(20, s("left", duration_s=1.0), s("left", duration_s=1.2), s("left", duration_s=0.0), s("left"))])
    assert (v.value, v.n) == (pytest.approx(110), 2)


def test_given_takes_wear_time():
    cat = catalog_with(pm("wear_time_s", "given"))
    v = one(cat, [bout(20)], wear_time_s=3600.0)
    assert (v.value, v.n, v.sufficient, v.reason) == (3600.0, 1, True, None)
    v = one(cat, [bout(20)], wear_time_s=None)
    assert (v.value, v.n, v.sufficient, v.reason) == (None, 0, False, "no_data")


def test_unknown_given_metric_is_an_error():
    cat = catalog_with(pm("something_else_s", "given"))
    with pytest.raises(ValueError, match="something_else_s"):
        metrics.period_values(cat, [bout(20)], wear_time_s=1.0)


# --- 발 ------------------------------------------------------------------------------


MAIN_FOOT_BOUTS = [
    bout(20, *[s("left", length_m=1)] * 3, *[s("right", length_m=5)] * 2),  # 왼발 3 > 오른발 2 → 왼발 1+1+1 = 3
    bout(20, *[s("left", length_m=2)] * 2, *[s("right", length_m=7)] * 2),  # 같음 → 왼발 2+2 = 4
    bout(20, s("left", length_m=9), *[s("right", length_m=3)] * 2),  # 오른발 → 3+3 = 6
    bout(20, *[s(None, length_m=0.5)] * 2),  # 발 없는 줄만 → 그 줄 0.5+0.5 = 1
    bout(20, s("left", length_m=1.5), s(None, length_m=8)),  # 왼발 줄이 있으므로 발 없는 줄은 안 씀 → 1.5
]


def test_main_foot_when_not_by_side():
    cat = catalog_with(pm("distance_m", "sum", "stride.length_m", where={"bout_min_s": 10}, unit="m"))
    v = one(cat, MAIN_FOOT_BOUTS)
    assert v.side is None
    assert v.value == pytest.approx(3 + 4 + 6 + 1 + 1.5)  # 15.5
    assert v.n == 3 + 2 + 2 + 2 + 1


def test_main_foot_counts_records_not_values():
    """주 발은 보폭 줄의 수로 정함. 그 지표의 값이 null인 줄도 셈."""
    cat = catalog_with(pm("distance_m", "sum", "stride.length_m", where={"bout_min_s": 10}, unit="m"))
    # 왼발 3줄(값은 하나), 오른발 2줄 → 왼발의 값 1.0 하나만
    left = [s("left"), s("left"), s("left", length_m=1.0)]
    v = one(cat, [bout(20, *left, s("right", length_m=4), s("right", length_m=4))])
    assert (v.value, v.n) == (1.0, 1)


def test_by_side_uses_each_foot_and_ignores_no_side_records():
    cat = catalog_with(pm("length_m", "sum", "stride.length_m", where={"bout_min_s": 10}, by_side=True, unit="m"))
    got = by_key(metrics.period_values(cat, MAIN_FOOT_BOUTS, wear_time_s=None))
    assert list(got) == [("length_m", "left"), ("length_m", "right")]
    # 왼발: 3 + 4 + 9 + 1.5 = 17.5 (7줄).  오른발: 10 + 14 + 6 = 30 (6줄)
    assert (got["length_m", "left"].value, got["length_m", "left"].n) == (pytest.approx(17.5), 7)
    assert (got["length_m", "right"].value, got["length_m", "right"].n) == (pytest.approx(30), 6)


def test_by_side_feet_come_from_any_stride_of_the_period():
    """보고하는 발은 where와 상관없이 기간의 보폭 줄에 나온 발. 그 발의 값이 where로 다 빠지면 no_data."""
    cat = catalog_with(pm("length_m", "sum", "stride.length_m", where={"bout_min_s": 10}, by_side=True, unit="m"))
    bouts = [bout(20, s("left", length_m=1)), bout(5, s("right", length_m=1))]
    got = by_key(metrics.period_values(cat, bouts, wear_time_s=None))
    assert got["length_m", "left"].value == 1
    right = got["length_m", "right"]
    assert (right.value, right.n, right.sufficient, right.reason) == (None, 0, False, "no_data")

    # 한 발만 나오면 그 발만
    got = metrics.period_values(cat, [bout(20, s("right", length_m=1))], wear_time_s=None)
    assert [v.side for v in got] == ["right"]

    # 발이 하나도 없으면 발 없는 값 하나, no_data
    [v] = metrics.period_values(cat, [bout(20, s(None, length_m=1)), bout(20)], wear_time_s=None)
    assert (v.side, v.value, v.reason) == (None, None, "no_data")


def test_by_side_on_bout_duration_is_an_error():
    cat = catalog_with(pm("walk_s", "sum", "bout.duration_s", by_side=True))
    with pytest.raises(ValueError, match="walk_s"):
        metrics.period_values(cat, [bout(20, s("left"))], wear_time_s=None)


# --- 값 없음, 양발, 충분함 -------------------------------------------------------------


def test_null_values_are_skipped():
    cat = catalog_with(pm("clear_m", "median", "stride.clearance_m", where={"bout_min_s": 10}, unit="m"))
    v = one(cat, [bout(20, s("left", clearance_m=0.04), s("left"), s("left", clearance_m=0.06))])
    assert (v.value, v.n) == (pytest.approx(0.05), 2)


def test_no_values_gives_no_data():
    cat = catalog_with(
        pm("clear_m", "median", "stride.clearance_m", where={"bout_min_s": 10}, unit="m"),
        pm("n", "count", "stride.duration_s", where={"bout_min_s": 10}, type_="integer", unit="count"),
        pm("walk_s", "sum", "bout.duration_s", where={"bout_min_s": 1000}),
        pm("share", "ratio", "bout.duration_s", where={"bout_min_s": 10}, unit="ratio"),
        pm("cv", "cv", "stride.duration_s", where={"bout_min_s": 30}, unit="ratio"),
    )
    # 값이 없는 줄, 9초 구간의 줄, 걷기 구간은 있지만 1000초 이상은 없음, 3개 미만인 구간뿐인 cv
    bouts = [bout(40, s("left"), s("left", duration_s=1.0)), bout(9, s("left", duration_s=1.0))]
    got = by_key(metrics.period_values(cat, bouts, wear_time_s=None))
    for name in ("clear_m", "walk_s", "cv"):
        v = got[name, None]
        assert (v.value, v.n, v.sufficient, v.reason) == (None, 0, False, "no_data"), name
    assert got["n", None].value == 1
    assert got["share", None].value == pytest.approx(40 / 49)

    # 걷기 구간이 하나도 없으면 ratio도 no_data
    got = by_key(metrics.period_values(cat, [], wear_time_s=None))
    assert all(v.reason == "no_data" for v in got.values())


def test_needs_both_feet():
    cat = catalog_with(
        pm("speed_mps", "median", "stride.speed_mps", where={"bout_min_s": 10}, unit="m/s", needs="both_feet"),
        pm("walk_s", "sum", "bout.duration_s", needs="both_feet"),
    )
    left_only = bout(20, s("left", speed_mps=1.0), s("left", speed_mps=1.0))
    got = by_key(metrics.period_values(cat, [left_only], wear_time_s=None))
    assert {(v.value, v.n, v.sufficient, v.reason) for v in got.values()} == {(None, 0, False, "needs_both_feet")}

    # 양발 구간이 생기면 그 구간만 씀: 주 발 오른발(2 > 1)의 [1.4, 1.6] → 1.5. 시간은 양발 구간 30초만
    both = bout(30, s("left", speed_mps=9.0), s("right", speed_mps=1.4), s("right", speed_mps=1.6))
    got = by_key(metrics.period_values(cat, [left_only, both], wear_time_s=None))
    assert (got["speed_mps", None].value, got["speed_mps", None].n) == (pytest.approx(1.5), 2)
    assert (got["walk_s", None].value, got["walk_s", None].n) == (30, 1)

    # 걷기 구간이 아예 없으면 needs_both_feet가 아니라 no_data
    got = by_key(metrics.period_values(cat, [], wear_time_s=None))
    assert {v.reason for v in got.values()} == {"no_data"}


@pytest.mark.parametrize(("count", "sufficient"), [(2, False), (3, True)])
def test_sufficient_is_n_at_least_min_n(count, sufficient):
    cat = catalog_with(pm("speed_mps", "median", "stride.speed_mps", where={"bout_min_s": 10}, unit="m/s", min_n=3))
    v = one(cat, [bout(20, *[s("left", speed_mps=1.0)] * count)])
    assert (v.value, v.n, v.sufficient) == (1.0, count, sufficient)


def test_value_as_dict_shape():
    cat = catalog_with(
        pm("cv", "cv", "stride.duration_s", where={"bout_min_s": 30}, by_side=True, unit="ratio", min_n=3),
        pm("walk_s", "sum", "bout.duration_s"),
    )
    got = metrics.period_values(cat, [bout(40, *[s("left", duration_s=d) for d in (1.0, 1.2, 1.4)])], wear_time_s=None)
    assert [v.as_dict() for v in got] == [
        {"metric": "period.cv", "side": "left", "value": pytest.approx(1 / 6), "unit": "ratio", "n": 1,
         "sufficient": False, "trust": "proxy", "where": {"bout_min_s": 30}},
        {"metric": "period.walk_s", "side": None, "value": 40, "unit": "s", "n": 1,
         "sufficient": True, "trust": "proxy", "where": {}},
    ]  # fmt: skip
    empty = metrics.period_values(cat, [], wear_time_s=None)
    assert empty[0].as_dict() == {
        "metric": "period.cv", "side": None, "value": None, "unit": "ratio", "n": 0,
        "sufficient": False, "trust": "proxy", "where": {"bout_min_s": 30}, "reason": "no_data",
    }  # fmt: skip


# --- 진짜 지표 목록 ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def catalog() -> metrics.Catalog:
    return metrics.load()


def stride_record(catalog, side, d, length, speed, swing):
    """gaitfusion이 낸 꼴의 줄 → normalize를 거친 Record(저장된 줄을 읽는 길과 같음)."""
    raw = {
        "side": side, "start_s": 0.0, "end_s": d, "duration_s": d, "length_m": length, "speed_mps": speed,
        "clearance_m": 0.05, "heading_deg": 1.0, "swing_s": swing, "stance_s": d - swing,
        "stance_ratio": round((d - swing) / d, 3), "uwb_support": 0.9,
    }  # fmt: skip
    return Record(side, metrics.normalize(catalog, "stride", V1, raw))


def synthetic_period(catalog) -> list[BoutData]:
    rnd = random.Random(7)

    def strides(n):
        out = []
        for side in ("left", "right"):
            for _ in range(n):
                d = 1.0 + rnd.uniform(-0.1, 0.1)
                out.append(stride_record(catalog, side, d, 1.2 + rnd.uniform(-0.1, 0.1), 1.2, 0.4))
        return out

    return [
        BoutData(70.0, "fused", {"stride": strides(30)}),
        BoutData(40.0, "fused", {"stride": strides(18)}),
        BoutData(35.0, "imu_only", {"stride": strides(15)}),
        BoutData(12.0, "imu_only", {"stride": strides(5)}),
        BoutData(6.0, "imu_only", {"stride": strides(2)}),
    ]


def test_real_catalog_end_to_end(catalog):
    bouts = synthetic_period(catalog)
    got = metrics.period_values(catalog, bouts, wear_time_s=8 * 3600.0)
    period = catalog.metrics_of("period")
    assert len(period) == 24

    want = []
    for m in period:
        want += [(m.id, "left"), (m.id, "right")] if m.by_side else [(m.id, None)]
    assert [(v.metric, v.side) for v in got] == want  # 목록 순서, 발은 왼발·오른발
    assert len(got) == 15 + 9 * 2

    v = {(x.metric.split(".", 1)[1], x.side): x for x in got}
    assert v["wear_time_s", None].value == 8 * 3600.0
    assert v["walking_time_s", None].value == pytest.approx(70 + 40 + 35 + 12 + 6)  # 163
    assert v["bout_count", None].value == 5 and type(v["bout_count", None].value) is int
    assert v["fused_ratio", None].value == pytest.approx(110 / 163)
    assert v["walking_share_over_60s", None].value == pytest.approx(70 / 163)
    assert v["bout_duration_max_s", None].value == 70
    assert v["bout_duration_median_s", None].value == 35
    # 10초 이상 구간의 발마다 보폭: 30 + 18 + 15 + 5 = 68
    assert v["stride_count", "left"].value == v["stride_count", "right"].value == 68
    assert v["speed_median_mps", None].value == pytest.approx(1.2)
    assert v["speed_median_mps", None].n == 68  # 주 발(같으면 왼발)만
    assert v["speed_10_30s_median_mps", None].n == 5
    assert v["speed_30_60s_median_mps", None].n == 18 + 15
    assert v["speed_over_60s_median_mps", None].n == 30
    assert v["stride_time_cv", "left"].n == 3  # 30초 이상 구간 셋
    assert not v["speed_p95_mps", None].sufficient  # 68 < min_n 100
    assert all(x.value is not None for x in got)
    assert all(x.unit == catalog.metric(x.metric).unit and x.trust == catalog.metric(x.metric).trust for x in got)


def test_halves_add_up_to_the_whole(catalog):
    """M9 2번의 일부: 기간을 둘로 쪼개 낸 개수와 합을 더하면 통째로 낸 값과 같음."""
    bouts = synthetic_period(catalog)
    whole = by_key(metrics.period_values(catalog, bouts, wear_time_s=None))
    first = by_key(metrics.period_values(catalog, bouts[::2], wear_time_s=None))
    second = by_key(metrics.period_values(catalog, bouts[1::2], wear_time_s=None))
    keys = [("walking_time_s", None), ("bout_count", None), ("distance_m", None),
            ("stride_count", "left"), ("stride_count", "right")]  # fmt: skip
    for key in keys:
        assert first[key].value + second[key].value == pytest.approx(whole[key].value), key
        assert first[key].n + second[key].n == whole[key].n, key


def test_order_of_input_does_not_matter_and_input_is_not_changed(catalog):
    bouts = synthetic_period(catalog)
    before = copy.deepcopy(bouts)
    want = metrics.period_values(catalog, bouts, wear_time_s=100.0)
    assert bouts == before

    rnd = random.Random(3)
    shuffled = []
    for b in reversed(bouts):
        strides = list(b.records["stride"])
        rnd.shuffle(strides)
        shuffled.append(BoutData(b.duration_s, b.mode, {"stride": strides}))
    got = metrics.period_values(catalog, shuffled, wear_time_s=100.0)
    assert [x.as_dict() for x in got] == [x.as_dict() for x in want]
    assert not any(isinstance(x.value, float) and math.isnan(x.value) for x in got)
