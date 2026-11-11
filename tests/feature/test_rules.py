"""feature 단계의 순수 규칙(FT-2, FT-3, MC-4 표). DB 없이."""

import gzip
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta

import pytest

from uwbserver.feature import rules
from uwbserver.pipeline import metrics

ORIGIN = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)
V1 = "gaitfusion.stride/1"


@pytest.fixture(scope="module")
def catalog() -> metrics.Catalog:
    return metrics.load()


def stride(side: str | None = "left", start: float = 1.0, end: float = 2.1, **extra) -> dict:
    rec = {"side": side, "start_s": start, "end_s": end, "duration_s": round(end - start, 6), "length_m": 1.29,
           "speed_mps": 1.17, "clearance_m": 0.05, "heading_deg": 3.1, "swing_s": 0.44, "stance_s": 0.66,
           "stance_ratio": 0.6, "uwb_support": 0.95}  # fmt: skip
    return rec | extra


def bout(*strides: dict, **extra) -> dict:
    b = {"start_s": 0.0, "end_s": 30.0, "mode": "fused", "sides": "both", "quality": {"uwb_accept_ratio": 0.9},
         "trajectories": [{"side": "left", "part": "traj-0-left"}], "strides": list(strides), "turns": [],
         "steps": [], "windows": [], "events": []}  # fmt: skip
    return b | extra


def result(*bouts: dict, summary: dict | None = None) -> dict:
    return {"c2_version": 1, "gaitfusion_version": "t", "status": "complete", "summary": summary or {},
            "bouts": list(bouts), "models": []}  # fmt: skip


def unpack(doc: dict, catalog: metrics.Catalog, bout_ids: dict[int, int] | None = None) -> rules.Unpacked:
    ids = bout_ids if bout_ids is not None else {k: 100 + k for k in range(len(doc["bouts"]))}
    return rules.unpack(doc, catalog, origin_at=ORIGIN, bout_ids=ids)


# --- FT-2: 낸 그대로 줄 하나씩 ------------------------------------------------------


def test_each_record_is_one_row_as_emitted(catalog):
    s0, s1 = stride("left", 1.0, 2.1), stride("right", 1.5, 2.6)
    s2 = stride("left", 2.1, 3.2)
    doc = result(bout(s0, s1), bout(s2, start_s=40.0, end_s=70.0))
    u = unpack(doc, catalog)
    assert [(r["bout_id"], r["kind"], r["idx"], r["side"]) for r in u.rows] == [
        (100, "stride", 0, "left"), (100, "stride", 1, "right"), (101, "stride", 0, "left"),
    ]  # fmt: skip
    assert [r["v"] for r in u.rows] == [s0, s1, s2]
    assert u.rows[1]["start_at"] == ORIGIN + timedelta(seconds=1.5)
    assert u.rows[1]["end_at"] == ORIGIN + timedelta(seconds=2.6)
    assert (u.n_records, u.n_dropped_records, u.n_bad_values) == (3, 0, 0)
    assert (u.unknown_fields, u.unknown_tables) == ([], [])
    assert u.formats == {"strides": V1, "steps": "gaitfusion.step/1", "turns": "gaitfusion.turn/1",
                         "windows": "gaitfusion.window/1", "events": "gaitfusion.event/1"}  # fmt: skip


def test_times_round_to_microseconds_like_bouts(catalog):
    (row,) = unpack(result(bout(stride(start=0.1234567, end=0.2))), catalog).rows
    assert row["start_at"] == ORIGIN + timedelta(microseconds=123457)


def test_every_catalog_table_is_unpacked_with_its_level(catalog):
    turn = {"side": None, "start_s": 3.0, "end_s": 4.0}
    window = {"start_s": 0.0, "end_s": 10.0}  # 발 필드가 없음 = 발 구분 없음
    u = unpack(result(bout(turns=[turn], windows=[window])), catalog)
    assert [(r["kind"], r["side"], r["v"]) for r in u.rows] == [("turn", None, turn), ("window", None, window)]


def test_bout_without_id_is_an_error(catalog):
    """결과 원문의 걷기 구간이 analysis.bout에 없으면 풀지 않음(파일과 DB가 어긋남)."""
    with pytest.raises(rules.UnpackError):
        unpack(result(bout(), bout(start_s=40.0, end_s=50.0)), catalog, bout_ids={0: 1})


def test_result_without_bouts_array_is_an_error(catalog):
    with pytest.raises(rules.UnpackError):
        rules.unpack({"summary": {}}, catalog, origin_at=ORIGIN, bout_ids={})


# --- FT-3: 버리는 것은 시작·끝·발이 틀린 줄뿐 ----------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"start_s": None},
        {"start_s": "1.0"},
        {"end_s": True},
        {"end_s": math.nan},
        {"start_s": math.inf},
        {"end_s": 0.5},  # 끝이 시작보다 앞섬
        {"side": "middle"},
        {"side": 0},
        {"start_s": 1e300, "end_s": 2e300},  # 시각으로 나타낼 수 없음
    ],
)
def test_record_with_bad_start_end_or_side_is_dropped(catalog, bad):
    good0, good1 = stride(start=0.0, end=1.0), stride(start=2.0, end=3.0)
    u = unpack(result(bout(good0, stride(**bad), good1)), catalog)
    assert [(r["idx"], r["v"]) for r in u.rows] == [(0, good0), (2, good1)]  # 순번은 배열 안 그대로
    assert (u.n_records, u.n_dropped_records) == (2, 1)


def test_record_missing_start_or_not_an_object_is_dropped(catalog):
    rec = stride()
    del rec["start_s"]
    u = unpack(result(bout(rec, [1, 2], "x", stride())), catalog)
    assert [r["idx"] for r in u.rows] == [3] and u.n_dropped_records == 3


def test_end_equal_to_start_and_missing_side_are_kept(catalog):
    rec = stride(start=1.0, end=1.0)
    del rec["side"]
    (row,) = unpack(result(bout(rec)), catalog).rows
    assert row["side"] is None and row["start_at"] == row["end_at"]


@pytest.mark.parametrize(("field", "value"), [("length_m", -1.0), ("length_m", "long"), ("stance_ratio", 1.5)])
def test_bad_value_is_stored_and_counted(catalog, field, value):
    rec = stride(**{field: value})
    u = unpack(result(bout(rec, stride())), catalog)
    assert u.rows[0]["v"] == rec and u.n_bad_values == 1 and u.n_dropped_records == 0


def test_non_finite_value_is_stored_as_null_and_counted(catalog):
    """JSON의 NaN·Infinity는 jsonb에 넣을 수 없으므로 null(MC-2: 계산하지 못함)로 두고 틀린 값으로 셈."""
    rec = stride(length_m=math.nan, extra={"deep": [math.inf, 1.0]})
    (row,) = unpack(result(bout(rec)), catalog).rows
    assert row["v"]["length_m"] is None and row["v"]["extra"] == {"deep": [None, 1.0]}
    json.dumps(row["v"], allow_nan=False)
    assert unpack(result(bout(rec)), catalog).n_bad_values == 1


# --- MC-4 표 -------------------------------------------------------------------------


def test_mc4_extra_field_is_stored_and_named(catalog):
    rec = stride(min_clearance_m=0.017)
    u = unpack(result(bout(rec, stride(lateral_dev_m=0.02, min_clearance_m=0.01))), catalog)
    assert u.rows[0]["v"] == rec and u.n_records == 2
    assert u.unknown_fields == ["strides.lateral_dev_m", "strides.min_clearance_m"]  # 겹치지 않고 정렬


@pytest.mark.parametrize("how", ["missing", "null"])
def test_mc4_missing_or_null_field_is_stored_without_complaint(catalog, how):
    rec = stride()
    if how == "missing":
        del rec["uwb_support"]
    else:
        rec["uwb_support"] = None
    u = unpack(result(bout(rec)), catalog)
    assert u.rows[0]["v"] == rec and (u.n_bad_values, u.unknown_fields) == (0, [])


def test_mc4_renamed_field_is_stored_and_new_name_reported(catalog):
    rec = {k: v for k, v in stride().items() if k != "length_m"} | {"stride_length_m": 1.29}
    u = unpack(result(bout(rec)), catalog)
    assert u.rows[0]["v"] == rec and u.unknown_fields == ["strides.stride_length_m"]


def test_mc4_new_format_version_is_stored_and_declared(catalog):
    rec = {k: v for k, v in stride().items() if k != "length_m"} | {"length_cm": 129.0}
    u = unpack(result(bout(rec), summary={"formats": {"strides": "gaitfusion.stride/2"}}), catalog)
    assert u.rows[0]["v"] == rec and u.formats["strides"] == "gaitfusion.stride/2"
    assert "strides.length_cm" in u.unknown_fields  # 목록이 모르는 판이므로 시작·끝·발 말고는 모두 모름
    assert u.n_bad_values == 0


def test_mc4_new_array_is_not_unpacked_but_named(catalog):
    gaze = {"start_s": 1.0, "end_s": 2.0, "angle_deg": 3.0}
    u = unpack(result(bout(stride(), gazes=[gaze]), bout(start_s=40.0, end_s=50.0, gazes=[], foot_marks=[{}])), catalog)
    assert u.unknown_tables == ["foot_marks", "gazes"] and [r["kind"] for r in u.rows] == ["stride"]


def test_mc4_arrays_that_are_not_record_tables_are_not_unknown_tables(catalog):
    """trajectories는 1단계가 푸는 배열. 항목이 객체가 아닌 배열은 줄의 표가 아님."""
    u = unpack(result(bout(stride(), tags=["a", "b"], weights=[0.1, 0.2], mixed=[{}, 1])), catalog)
    assert u.unknown_tables == []


def test_mc4_missing_array_means_no_records_of_that_kind(catalog):
    b = bout(stride())
    del b["turns"], b["windows"]
    u = unpack(result(b, bout(start_s=40.0, end_s=50.0, strides=None)), catalog)
    assert [r["kind"] for r in u.rows] == ["stride"] and u.n_dropped_records == 0


# --- 결과 원문 파일 읽기 ---------------------------------------------------------------


def test_read_result_checks_sha256_of_uncompressed_bytes():
    raw = json.dumps(result(bout(stride()))).encode()
    doc = rules.read_result(gzip.compress(raw), hashlib.sha256(raw).digest())
    assert doc["bouts"][0]["strides"][0]["side"] == "left"
    with pytest.raises(rules.ResultInvalid, match="sha256"):
        rules.read_result(gzip.compress(raw), hashlib.sha256(b"other").digest())
    with pytest.raises(rules.ResultInvalid, match="gzip"):
        rules.read_result(raw, hashlib.sha256(raw).digest())
    bad = b'{"bouts": ['
    with pytest.raises(rules.ResultInvalid, match="json"):
        rules.read_result(gzip.compress(bad), hashlib.sha256(bad).digest())
