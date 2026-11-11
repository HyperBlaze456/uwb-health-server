"""응답 견본으로 지표 목록을 확인(SPEC MC-7 2번).

samples/real_result.json은 진짜 gaitfusion이 낸 C2 `result`이고, 만든 커밋과 방법은 samples/samples.json과
samples/make_samples.py에 있습니다. samples/fake_result.json은 가짜 gaitfusion(tools/fake_gaitfusion.py)의 것입니다.

- 목록의 모든 gaitfusion 지표가 진짜 견본의 줄에 실제로 있고, 형과 범위가 맞음.
- 견본의 줄에 목록이 모르는 필드가 있으면 그 이름을 보이며 실패(운영에서는 경고일 뿐).
- 걷기 구간 안의 배열은 1단계가 푸는 trajectories 말고는 모두 목록의 [[table]]에 있음.
"""

import json
from pathlib import Path

import pytest

from uwbserver.pipeline import metrics

SAMPLES = Path(__file__).parent / "samples"
PHASE1_ARRAYS = {"trajectories"}  # 7.2절이 푸는 배열. 줄의 종류가 아님


@pytest.fixture(scope="module")
def catalog() -> metrics.Catalog:
    return metrics.load()


def load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


def records(result: dict, catalog: metrics.Catalog):
    """(level, 형식, 줄)을 차례로."""
    formats = metrics.formats_of(result.get("summary"), catalog)
    for bout in result["bouts"]:
        for field, rows in bout.items():
            table = catalog.table_by_field(field)
            if table is not None:
                for row in rows:
                    yield table.level, formats[field], row


def test_real_sample_is_recorded_with_its_gaitfusion_commit():
    meta = json.loads((SAMPLES / "samples.json").read_text())["real_result.json"]
    assert len(meta["gaitfusion_commit"]) >= 40 and not meta["gaitfusion_commit"].endswith("-dirty")
    assert load("real_result.json")["gaitfusion_version"].endswith("+g" + meta["gaitfusion_commit"][:7])


@pytest.mark.parametrize("name", ["real_result.json", "fake_result.json"])
def test_sample_has_no_field_or_table_the_catalog_does_not_know(catalog, name):
    result = load(name)
    unknown, bad = set(), 0
    for level, fmt, row in records(result, catalog):
        n_bad, names = metrics.inspect(catalog, level, fmt, row)
        bad += n_bad
        unknown |= {f"{level}.{n}" for n in names}
    assert not unknown, f"지표 목록에 없는 필드: {sorted(unknown)}"
    assert bad == 0
    arrays = {k for b in result["bouts"] for k, v in b.items() if isinstance(v, list)}
    tables = {t.field for t in catalog.tables}
    assert arrays - PHASE1_ARRAYS <= tables, f"지표 목록에 없는 표: {sorted(arrays - PHASE1_ARRAYS - tables)}"


def test_every_gaitfusion_metric_is_in_the_real_sample(catalog):
    result = load("real_result.json")
    seen: dict[str, int] = {}
    n_rows = 0
    for level, fmt, row in records(result, catalog):
        n_rows += 1
        for mid in metrics.normalize(catalog, level, fmt, row):
            seen[mid] = seen.get(mid, 0) + 1
    assert n_rows > 0
    produced = {m.id for m in catalog.metrics if m.producer == "gaitfusion"}
    assert produced <= seen.keys(), f"진짜 견본에 없는 지표: {sorted(produced - seen.keys())}"
    # 융합 걷기만 있는 견본이므로 모든 줄에 모든 값이 있음(uwb_support도 null이 아님)
    assert all(seen[m] == n_rows for m in produced), {m: seen[m] for m in produced}
