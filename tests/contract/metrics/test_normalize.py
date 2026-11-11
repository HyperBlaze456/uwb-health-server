"""줄 하나의 풀이(SPEC FT-4 normalize, FT-3 inspect)와 형식 읽기(MC-3 formats_of).

MC-4 표의 줄마다 줄 하나 수준에서 "서버가 하는 일"대로 되는지 봅니다. run 전체를 푸는 수준(MC-7 3번의
feature.unpack 숫자)은 M8의 시험입니다.
"""

import math
import tomllib

import pytest

from uwbserver.pipeline import metrics

STRIDE = {
    "side": "left", "start_s": 811.9, "end_s": 813.0, "duration_s": 1.1, "length_m": 1.29, "speed_mps": 1.17,
    "clearance_m": 0.05, "heading_deg": 3.1, "swing_s": 0.44, "stance_s": 0.66, "stance_ratio": 0.6,
    "uwb_support": 0.95,
}  # fmt: skip
WANT = {
    "stride.duration_s": 1.1, "stride.length_m": 1.29, "stride.speed_mps": 1.17, "stride.clearance_m": 0.05,
    "stride.heading_deg": 3.1, "stride.swing_s": 0.44, "stride.stance_s": 0.66, "stride.stance_ratio": 0.6,
    "stride.uwb_support": 0.95,
}  # fmt: skip
V1 = "gaitfusion.stride/1"


@pytest.fixture(scope="module")
def catalog() -> metrics.Catalog:
    return metrics.load()


def with_v2(catalog: metrics.Catalog) -> metrics.Catalog:
    """지표 목록 파일에 /2 판과 names 한 줄을 더한 것(MC-4 "단위나 뜻이 바뀜"에서 사람이 할 일)."""
    doc = tomllib.loads(metrics.default_path().read_text())
    for t in doc["table"]:
        if t["field"] == "strides":
            t["formats"].append("gaitfusion.stride/2")
    for m in doc["metric"]:
        if m["id"] == "stride.length_m":
            m["names"] = {"gaitfusion.stride/2": {"name": "length_cm", "scale": 0.01}}
    return metrics.parse(doc)


def test_record_as_emitted_today(catalog):
    assert metrics.normalize(catalog, "stride", V1, STRIDE) == WANT
    assert metrics.inspect(catalog, "stride", V1, STRIDE) == (0, [])


def test_extra_field_is_kept_out_and_reported(catalog):
    """MC-4 "값 필드가 늘어남": 풀이에는 없고 unknown_fields에 이름."""
    rec = {**STRIDE, "min_clearance_m": 0.017, "lateral_dev_m": 0.02}
    assert metrics.normalize(catalog, "stride", V1, rec) == WANT
    assert metrics.inspect(catalog, "stride", V1, rec) == (0, ["lateral_dev_m", "min_clearance_m"])


@pytest.mark.parametrize("how", ["missing", "null"])
def test_missing_or_null_field_has_no_value(catalog, how):
    """MC-4 "필드가 빠지거나 null": 그 지표만 값 없음. 틀린 값으로 세지 않음."""
    rec = dict(STRIDE)
    if how == "missing":
        del rec["uwb_support"]
    else:
        rec["uwb_support"] = None
    want = {k: v for k, v in WANT.items() if k != "stride.uwb_support"}
    assert metrics.normalize(catalog, "stride", V1, rec) == want
    assert metrics.inspect(catalog, "stride", V1, rec) == (0, [])


def test_renamed_field_is_unknown_until_names_has_it(catalog):
    """MC-4 "필드 이름이 바뀜": 새 이름은 unknown_fields. names 한 줄을 더하면 다시 풀지 않고 읽힘."""
    rec = {k: v for k, v in STRIDE.items() if k != "length_m"} | {"stride_length_m": 1.29}
    assert "stride.length_m" not in metrics.normalize(catalog, "stride", V1, rec)
    assert metrics.inspect(catalog, "stride", V1, rec) == (0, ["stride_length_m"])

    doc = tomllib.loads(metrics.default_path().read_text())
    for m in doc["metric"]:
        if m["id"] == "stride.length_m":
            m["names"] = {V1: {"name": "stride_length_m"}}
    fixed = metrics.parse(doc)
    assert metrics.check(fixed) == []
    assert metrics.normalize(fixed, "stride", V1, rec) == WANT
    assert metrics.inspect(fixed, "stride", V1, rec) == (0, [])


def test_new_format_is_not_interpreted_until_the_catalog_lists_it(catalog):
    """MC-4 "단위나 뜻이 바뀜(판이 오름)": formats에 없는 판의 줄은 풀이에 쓰지 않음."""
    rec = {k: v for k, v in STRIDE.items() if k != "length_m"} | {"length_cm": 129.0}
    assert metrics.normalize(catalog, "stride", "gaitfusion.stride/2", rec) == {}
    n_bad, unknown = metrics.inspect(catalog, "stride", "gaitfusion.stride/2", rec)
    assert n_bad == 0 and "length_cm" in unknown and "duration_s" in unknown  # 이 판은 목록이 모름

    v2 = with_v2(catalog)
    assert metrics.check(v2) == []
    got = metrics.normalize(v2, "stride", "gaitfusion.stride/2", rec)
    assert got.keys() == WANT.keys()
    assert math.isclose(got["stride.length_m"], 1.29)
    assert metrics.inspect(v2, "stride", "gaitfusion.stride/2", rec) == (0, [])
    assert metrics.normalize(v2, "stride", V1, STRIDE) == WANT  # 옛 판은 옛 이름 그대로


@pytest.mark.parametrize(("field", "value"), [
    ("length_m", 3.5),          # 범위 밖
    ("length_m", -0.1),
    ("stance_ratio", 1.2),
    ("length_m", "1.29"),       # 형
    ("length_m", True),
    ("length_m", float("nan")),  # 유한하지 않음
    ("length_m", float("inf")),
    ("length_m", [1.29]),
])  # fmt: skip
def test_bad_value_is_dropped_and_counted(catalog, field, value):
    rec = {**STRIDE, field: value}
    got = metrics.normalize(catalog, "stride", V1, rec)
    assert f"stride.{field}" not in got
    assert len(got) == len(WANT) - 1
    assert metrics.inspect(catalog, "stride", V1, rec) == (1, [])


def test_integer_value_is_a_number(catalog):
    assert metrics.normalize(catalog, "stride", V1, {**STRIDE, "length_m": 1})["stride.length_m"] == 1


def test_key_fields_are_not_unknown_and_not_metrics(catalog):
    got = metrics.normalize(catalog, "stride", V1, STRIDE)
    assert not {"side", "start_s", "end_s"} & set(got)
    assert metrics.inspect(catalog, "stride", V1, {"side": "right", "start_s": 0.0, "end_s": 1.0}) == (0, [])


def test_table_without_metrics_stores_everything_as_unknown(catalog):
    """turns는 표는 있으나 지표가 아직 없음: 풀이는 비고, 값 필드는 unknown_fields."""
    turn = {"side": None, "start_s": 1.0, "end_s": 2.0, "angle_deg": 90.0}
    assert metrics.normalize(catalog, "turn", "gaitfusion.turn/1", turn) == {}
    assert metrics.inspect(catalog, "turn", "gaitfusion.turn/1", turn) == (0, ["angle_deg"])


def test_unknown_level_is_an_error(catalog):
    with pytest.raises(KeyError):
        metrics.normalize(catalog, "period", "x/1", {})


# --- MC-3 formats_of -----------------------------------------------------------------

DEFAULTS = {
    "strides": "gaitfusion.stride/1", "steps": "gaitfusion.step/1", "turns": "gaitfusion.turn/1",
    "windows": "gaitfusion.window/1", "events": "gaitfusion.event/1",
}  # fmt: skip


@pytest.mark.parametrize("summary", [
    {}, None, {"formats": None}, {"formats": "gaitfusion.stride/1"}, {"n_imu": 10},
])  # fmt: skip
def test_formats_default_to_v1_when_not_declared(catalog, summary):
    assert metrics.formats_of(summary, catalog) == DEFAULTS


def test_declared_formats_win_and_new_tables_keep_theirs(catalog):
    summary = {"formats": {"strides": "gaitfusion.stride/2", "gaze": "gaitfusion.gaze/1", "turns": 3}}
    assert metrics.formats_of(summary, catalog) == DEFAULTS | {
        "strides": "gaitfusion.stride/2", "gaze": "gaitfusion.gaze/1"}  # 글자가 아닌 값은 무시하고 기본값
