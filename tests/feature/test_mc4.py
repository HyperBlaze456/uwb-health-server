"""M8-2 (MC-4 표, MC-7 3번): gaitfusion의 형식이 바뀐 응답 → 풀기가 실패하지 않고 표대로. 숫자와 이름이 feature.unpack에
있음. run과 걷기 구간은 그대로. 이름이 바뀐 필드는 지표 목록에 names 한 줄을 더하면 다시 풀지 않고 읽힘."""

import tomllib

import pytest

from tests.feature.conftest import patched_core
from tests.support import closed_segment, run_jobs
from uwbserver.feature import routes as feature_routes
from uwbserver.pipeline import metrics

pytestmark = pytest.mark.db


def _strides(doc: dict):
    for b in doc["bouts"]:
        yield from b["strides"]


def extra_field(doc):
    for s in _strides(doc):
        s["min_clearance_m"] = 0.017


def missing_field(doc):
    for s in _strides(doc):
        del s["uwb_support"]


def null_field(doc):
    for s in _strides(doc):
        s["clearance_m"] = None


def renamed_field(doc):
    for s in _strides(doc):
        s["stride_length_m"] = s.pop("length_m")


def new_format(doc):
    doc["summary"]["formats"] = {"strides": "gaitfusion.stride/2"}
    for s in _strides(doc):
        s["length_cm"] = round(s.pop("length_m") * 100, 6)


def new_array(doc):
    for b in doc["bouts"]:
        b["gazes"] = [{"start_s": b["start_s"], "end_s": b["end_s"], "angle_deg": 1.0}]


def missing_array(doc):
    for b in doc["bouts"]:
        del b["strides"]


# 경우 → (응답 바꾸기, 기대 unknown_fields(None = 보지 않음), 기대 unknown_tables, 보폭 줄이 있음)
CASES = {
    "extra_field": (extra_field, ["strides.min_clearance_m"], [], True),
    "missing_field": (missing_field, [], [], True),
    "null_field": (null_field, [], [], True),
    "renamed_field": (renamed_field, ["strides.stride_length_m"], [], True),
    "new_format": (new_format, None, [], True),  # 모르는 판: 시작·끝·발 말고 모두 모름
    "new_array": (new_array, [], ["gazes"], True),
    "missing_array": (missing_array, [], [], False),
}


# 시험 구간은 앵커가 측량되지 않아 IMU 단독이므로 uwb_support는 null(MC-6). 그 밖의 보폭 지표 8개는 값이 있음.
ALL = {m.id for m in metrics.load().metrics_of("stride")} - {"stride.uwb_support"}


def _phase1(env) -> dict:
    return {t: env.q(f"SELECT * FROM {t} ORDER BY 1") for t in ("analysis.run", "analysis.bout", "analysis.trajectory")}


def _first_bout(env, run_id: int) -> int:
    return env.scalar("SELECT id FROM analysis.bout WHERE run_id = %s AND idx = 0", (run_id,))


@pytest.mark.parametrize("case", CASES)
def test_mc4_change_is_accepted(env, device, api, admin, case):
    patch, unknown_fields, unknown_tables, has_records = CASES[case]
    closed_segment(env, device)
    core = patched_core(patch)
    assert run_jobs(env, core, ("analysis",)) == ["done"]
    before = _phase1(env)
    assert run_jobs(env, core, ("feature",)) == ["done"]
    assert _phase1(env) == before

    run_id = before["analysis.run"][0]["id"]
    u = api.get(f"/v1/runs/{run_id}", headers=admin).json()["unpack"]
    assert u["n_dropped_records"] == 0 and u["n_bad_values"] == 0
    assert u["unknown_tables"] == unknown_tables
    if unknown_fields is not None:
        assert u["unknown_fields"] == unknown_fields
    assert (u["n_records"] > 0) == has_records
    assert env.scalar("SELECT count(*) FROM feature.record WHERE kind NOT IN ('stride')") == 0

    recs = api.get(f"/v1/bouts/{_first_bout(env, run_id)}/records", headers=admin).json()["records"]
    assert bool(recs) == has_records
    for r in recs:
        values = r["values"]
        if case == "null_field":
            assert set(values) == ALL - {"stride.clearance_m"}
        elif case == "renamed_field":
            assert set(values) == ALL - {"stride.length_m"}
        elif case == "new_format":
            assert values == {}  # 목록의 formats에 없는 판은 풀이하지 않음
        else:
            assert set(values) == ALL


def _catalog_with(change) -> metrics.Catalog:
    doc = tomllib.loads(metrics.default_path().read_text())
    change(doc)
    catalog = metrics.parse(doc)
    assert metrics.check(catalog) == []
    return catalog


@pytest.mark.parametrize(
    ("case", "fmt", "names"),
    [
        ("renamed_field", "gaitfusion.stride/1", {"name": "stride_length_m"}),
        ("new_format", "gaitfusion.stride/2", {"name": "length_cm", "scale": 0.01}),
    ],
)
def test_names_line_makes_value_readable_without_unpacking_again(env, device, api, admin, case, fmt, names):
    """MC-4 "필드 이름이 바뀜"·"판이 오름": 사람이 지표 목록에 names(와 formats) 한 줄을 더하면 저장된 줄이 그대로 읽힘."""
    closed_segment(env, device)
    core = patched_core(CASES[case][0])
    run_jobs(env, core, ("analysis", "feature"))
    run_id = env.scalar("SELECT id FROM analysis.run")
    stored = env.q("SELECT id, v FROM feature.record ORDER BY id")
    unpacked_at = env.scalar("SELECT unpacked_at FROM feature.unpack")
    bout = _first_bout(env, run_id)

    def change(doc):
        for t in doc["table"]:
            if t["field"] == "strides" and fmt not in t["formats"]:
                t["formats"].append(fmt)
        for m in doc["metric"]:
            if m["id"] == "stride.length_m":
                m["names"] = {fmt: names}

    api.app.dependency_overrides[feature_routes.catalog] = lambda: _catalog_with(change)
    try:
        recs = api.get(f"/v1/bouts/{bout}/records", headers=admin).json()["records"]
    finally:
        api.app.dependency_overrides.clear()
    assert recs and all(set(r["values"]) == ALL for r in recs)
    by_idx = {r["idx"]: r for r in recs}
    first = env.one("SELECT v FROM feature.record WHERE bout_id = %s AND idx = 0", (bout,))["v"]
    raw = first.get("stride_length_m", first.get("length_cm", 0) * 0.01)
    assert by_idx[0]["values"]["stride.length_m"] == pytest.approx(raw)
    # 다시 풀지 않았음: 같은 행, 같은 풀기 기록
    assert env.q("SELECT id, v FROM feature.record ORDER BY id") == stored
    assert env.scalar("SELECT unpacked_at FROM feature.unpack") == unpacked_at
