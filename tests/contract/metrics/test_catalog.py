"""C4 지표 목록 시험: 목록 자체의 검사(SPEC MC-7 1번)와 처음 넣는 지표(MC-8, M7 합격 기준 2번).

목록 파일은 contracts/metrics.toml이고, 읽기·검사 코드는 pipeline/metrics.py입니다.
"""

import tomllib

import pytest

from uwbserver.pipeline import metrics

STRIDE_IDS = {
    "stride.duration_s", "stride.length_m", "stride.speed_mps", "stride.clearance_m", "stride.heading_deg",
    "stride.swing_s", "stride.stance_s", "stride.stance_ratio", "stride.uwb_support",
}  # fmt: skip
PERIOD_IDS = {
    "period.wear_time_s", "period.fused_ratio",
    "period.walking_time_s", "period.bout_count", "period.bout_duration_max_s",
    "period.stride_count", "period.distance_m", "period.bout_duration_median_s", "period.bout_duration_p90_s",
    "period.walking_share_over_60s",
    "period.speed_median_mps", "period.speed_p95_mps", "period.speed_10_30s_median_mps",
    "period.speed_30_60s_median_mps", "period.speed_over_60s_median_mps",
    "period.stride_length_median_m", "period.cadence_median_spm", "period.stride_time_median_s",
    "period.stride_time_cv", "period.stride_time_sd_s", "period.swing_time_cv", "period.stride_length_cv",
    "period.clearance_median_m", "period.stance_ratio_median",
}  # fmt: skip
BASIC = {
    "period.wear_time_s", "period.walking_time_s", "period.bout_count", "period.bout_duration_max_s",
    "period.speed_median_mps", "period.stride_length_median_m", "period.stride_time_cv",
}  # fmt: skip
BY_SIDE = {
    "period.stride_count", "period.stride_length_median_m", "period.stride_time_median_s",
    "period.stride_time_cv", "period.stride_time_sd_s", "period.swing_time_cv", "period.stride_length_cv",
    "period.clearance_median_m", "period.stance_ratio_median",
}  # fmt: skip


@pytest.fixture(scope="module")
def catalog() -> metrics.Catalog:
    return metrics.load()


# --- MC-7 1번: 목록 자체 ------------------------------------------------------------


def test_catalog_passes_its_own_checks(catalog):
    assert metrics.check(catalog) == []


def test_catalog_file_is_version_1(catalog):
    assert catalog.version == 1


def test_tables_are_the_five_record_kinds(catalog):
    got = {(t.field, t.level, t.formats) for t in catalog.tables}
    assert got == {
        ("strides", "stride", ("gaitfusion.stride/1",)), ("steps", "step", ("gaitfusion.step/1",)),
        ("turns", "turn", ("gaitfusion.turn/1",)), ("windows", "window", ("gaitfusion.window/1",)),
        ("events", "event", ("gaitfusion.event/1",)),
    }  # fmt: skip
    for t in catalog.tables:
        assert t.keys == {"start": "start_s", "end": "end_s", "side": "side"}
    assert catalog.table_by_field("strides").level == "stride"
    assert catalog.table_by_level("turn").field == "turns"
    assert catalog.table_by_field("nope") is None


# --- MC-8: 처음 넣는 33개 ------------------------------------------------------------


def test_catalog_has_exactly_the_33_metrics_of_mc8(catalog):
    ids = [m.id for m in catalog.metrics]
    assert len(ids) == 33
    assert set(ids) == STRIDE_IDS | PERIOD_IDS
    assert {m.id for m in catalog.metrics_of("stride")} == STRIDE_IDS
    assert {m.id for m in catalog.metrics_of("period")} == PERIOD_IDS
    assert catalog.metric("stride.length_m").unit == "m"
    assert catalog.metric("nope.x") is None


def test_every_metric_has_label_meaning_trust_show(catalog):
    for m in catalog.metrics:
        assert m.label.strip() and m.meaning.strip(), m.id
        assert m.trust in metrics.TRUST and m.show in metrics.SHOW, m.id


def test_shown_by_default_are_the_seven_basic(catalog):
    assert {m.id for m in catalog.metrics if m.show == "basic"} == BASIC


def test_producer_and_by_side_follow_mc8(catalog):
    for m in catalog.metrics:
        if m.level == "stride":
            assert m.producer == "gaitfusion" and m.from_ is None and m.rule is None and m.by_side is None
        else:
            assert m.producer == "server" and m.by_side == (m.id in BY_SIDE), m.id


def test_period_rules_follow_rp3(catalog):
    """RP-3: 줄의 값에서 계산하는 지표는 10초 이상 걷기 구간만, 변동은 30초 이상."""
    for m in catalog.metrics_of("period"):
        if m.rule == "given":
            assert m.from_ is None
        elif m.from_ == "bout.duration_s":
            assert m.rule in {"count", "sum", "max", "median", "p90", "ratio"}, m.id
        else:
            assert m.where.get("bout_min_s", 0) >= (30 if m.rule in {"cv", "sd"} else 10), m.id
        assert m.min_n >= 1


def test_mc6_stride_definition_and_uwb_support_null(catalog):
    assert "연속한 두 정지의 가운데 사이" in catalog.metric("stride.duration_s").meaning
    assert "null" in catalog.metric("stride.uwb_support").meaning


def test_speed_classes_split_by_bout_length(catalog):
    where = {i: catalog.metric(i).where for i in (
        "period.speed_10_30s_median_mps", "period.speed_30_60s_median_mps", "period.speed_over_60s_median_mps")}
    assert where == {
        "period.speed_10_30s_median_mps": {"bout_min_s": 10, "bout_max_s": 30},
        "period.speed_30_60s_median_mps": {"bout_min_s": 30, "bout_max_s": 60},
        "period.speed_over_60s_median_mps": {"bout_min_s": 60},
    }


# --- check()가 잡는 것 ----------------------------------------------------------------

BASE = """
version = 1
[[table]]
field = "strides"
level = "stride"
formats = ["gaitfusion.stride/1"]
keys = { start = "start_s", end = "end_s", side = "side" }

[[metric]]
id = "stride.length_m"
level = "stride"
type = "number"
unit = "m"
range = [0.0, 3.0]
producer = "gaitfusion"
show = "detail"
needs = "one_foot"
trust = "unverified"
label = "보폭 길이"
meaning = "뜻"
sources = ["schlachetzki2017"]

[[metric]]
id = "period.stride_length_median_m"
level = "period"
type = "number"
unit = "m"
producer = "server"
show = "basic"
needs = "one_foot"
trust = "unverified"
label = "보폭 길이"
meaning = "뜻"
sources = []
from = "stride.length_m"
rule = "median"
where = { bout_min_s = 10 }
by_side = true
min_n = 30
"""


def parse(text: str) -> metrics.Catalog:
    return metrics.parse(tomllib.loads(text))


def test_base_example_is_clean():
    assert metrics.check(parse(BASE)) == []


@pytest.mark.parametrize(("old", "new", "want"), [
    ('id = "period.stride_length_median_m"', 'id = "stride.length_m"', "겹침"),
    ('unit = "m"\nrange', 'unit = "s"\nrange', "단위"),
    ('from = "stride.length_m"', 'from = "stride.nope_m"', "from"),
    ('rule = "median"', 'rule = "mean"', "rule"),
    ('where = { bout_min_s = 10 }', 'where = { bout_min_s = 10, speed_min = 1 }', "where"),
    ('where = { bout_min_s = 10 }', 'where = { mode = "uwb" }', "where"),
    ('where = { bout_min_s = 10 }', 'where = { bout_min_s = 5 }', "10초"),
    ('sources = ["schlachetzki2017"]', 'sources = ["Schlachetzki 2017"]', "sources"),
    ('formats = ["gaitfusion.stride/1"]', 'formats = ["gaitfusion.turn/1"]', "formats"),
    ('id = "stride.length_m"', 'id = "turn.length_m"', "level"),
])  # fmt: skip
def test_check_reports_each_broken_rule(old, new, want):
    text = BASE.replace(old, new, 1)
    assert text != BASE
    problems = metrics.check(parse(text))
    assert any(want in p for p in problems), problems


def test_check_reports_names_for_a_format_the_table_does_not_have():
    names = 'names = { "gaitfusion.stride/2" = { name = "len_cm", scale = 0.01 } }'
    text = BASE.replace('sources = ["schlachetzki2017"]', f'sources = ["schlachetzki2017"]\n{names}')
    assert any("names" in p for p in metrics.check(parse(text)))


@pytest.mark.parametrize(("old", "new"), [
    ('label = "보폭 길이"\n', ''),  # label 빠짐
    ('type = "number"\nunit = "m"\nrange', 'type = "float"\nunit = "m"\nrange'),
    ('trust = "unverified"\n', 'trust = "good"\n'),
    ('range = [0.0, 3.0]', 'range = [3.0, 0.0]'),
    ('min_n = 30', 'min_n = 30\ncolour = "red"'),
])  # fmt: skip
def test_malformed_entry_is_a_clear_error(old, new):
    text = BASE.replace(old, new, 1)
    assert text != BASE
    with pytest.raises(metrics.CatalogError, match="stride"):
        parse(text)


# --- trust fact, 발과 걷기 구간 길이 ---------------------------------------------------------


def test_bookkeeping_values_are_fact_and_nothing_else(catalog):
    """fact = 문헌으로 검증할 대상이 아닌 값. 이 셋만."""
    assert "fact" in metrics.TRUST
    assert {m.id for m in catalog.metrics if m.trust == "fact"} == {
        "stride.uwb_support", "period.wear_time_s", "period.fused_ratio"}


def test_fact_is_accepted_by_parse():
    assert parse(BASE.replace('trust = "unverified"\n', 'trust = "fact"\n', 1)).metrics[0].trust == "fact"


def test_check_reports_by_side_on_bout_duration():
    """걷기 구간 길이에는 발이 없으므로 by_side = true는 틀림(period_values는 돌 때 예외를 냄)."""
    text = BASE.replace('from = "stride.length_m"\nrule = "median"\nwhere = { bout_min_s = 10 }',
                        'from = "bout.duration_s"\nrule = "median"', 1)
    assert metrics.check(parse(text.replace("by_side = true", "by_side = false"))) == []
    problems = metrics.check(parse(text))
    assert any("by_side" in p and "bout.duration_s" in p for p in problems), problems


def test_same_author_and_year_sources_have_letters(catalog):
    keys = {s for m in catalog.metrics for s in m.sources}
    assert "deldin2019" not in keys
    assert {"deldin2019a", "deldin2019b"} <= keys
