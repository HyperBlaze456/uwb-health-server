"""report/rules.py의 시험 (SPEC RP-4, RP-7, RP-8). 순수 계산이라 DB·모델·네트워크가 없습니다."""

from datetime import UTC, date, datetime, timedelta

import pytest

from uwbserver.pipeline import metrics
from uwbserver.pipeline.metrics import Value
from uwbserver.report import rules

FROM = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)  # 한국 11월 3일 00:00
TO = FROM + timedelta(days=2)


@pytest.fixture(scope="module")
def catalog() -> metrics.Catalog:
    return metrics.load()


def value(metric: str, v, *, side=None, unit="m/s", n=40, sufficient=True, reason=None, where=None) -> Value:
    return Value(metric, side, v, unit, n if v is not None else 0, sufficient if v is not None else False,
                 "unverified", where or {}, reason if v is None else None)  # fmt: skip


def context(catalog, values, daily=(), *, from_at=FROM, to_at=TO, coverage=None) -> dict:
    return rules.build_context(
        catalog,
        subject={"code": "S01", "height_m": 1.7, "leg_length_m": 0.9, "paretic_side": None},
        from_at=from_at,
        to_at=to_at,
        values=values,
        daily_rows=list(daily),
        coverage=coverage or {"wear_time_s": 7200.0, "feet": ["left", "right"], "partial_runs": 0,
                              "ambiguous_bouts": 0, "runs_not_unpacked": 0},  # fmt: skip
        runs=[3, 4],
    )


def daily(metric, values, *, side=None, sufficient=True, start=date(2026, 10, 30)):
    return [{"date": start - timedelta(days=i), "metric": metric, "side": side, "value": x, "sufficient": sufficient}
            for i, x in enumerate(values)]  # fmt: skip


# --- 맥락 (RP-7) ------------------------------------------------------------------------


def test_context_keys_and_numbered_values(catalog):
    vals = [
        value("period.wear_time_s", 7200.0, unit="s", n=1),
        value("period.walking_time_s", 1800.0, unit="s", n=1),
        value("period.bout_count", 12, unit="count", n=12),
        value("period.fused_ratio", 0.75, unit="ratio", n=12),
        value("period.speed_median_mps", None, reason="no_data"),
    ]
    ctx = context(catalog, vals)
    assert set(ctx) == {"schema", "catalog_version", "subject", "period", "baseline_comparable", "coverage",
                        "values", "runs"}  # fmt: skip
    assert ctx["schema"] == "uwbserver.report_input/1"
    assert ctx["catalog_version"] == catalog.version
    assert ctx["subject"] == {"code": "S01", "height_m": 1.7, "leg_length_m": 0.9, "paretic_side": None}
    assert ctx["period"] == {"from_at": FROM.isoformat(), "to_at": TO.isoformat()}
    assert ctx["runs"] == [3, 4]
    assert [v["id"] for v in ctx["values"]] == ["v1", "v2", "v3", "v4", "v5"]
    v5 = ctx["values"][4]
    assert v5["value"] is None and v5["reason"] == "no_data"
    m = catalog.metric("period.speed_median_mps")
    assert (v5["label"], v5["meaning"], v5["show"], v5["sources"]) == (m.label, m.meaning, m.show, list(m.sources))
    cov = ctx["coverage"]
    assert cov["wear_time_s"] == 7200.0
    assert cov["walking_time_s"] == 1800.0 and cov["bout_count"] == 12 and cov["fused_ratio"] == 0.75
    assert cov["feet"] == ["left", "right"] and cov["sensor_position"] == "lateral_ankle"
    assert (cov["partial_runs"], cov["ambiguous_bouts"], cov["runs_not_unpacked"]) == (0, 0, 0)


# --- 지난 30일 (RP-4) --------------------------------------------------------------------


def test_baseline_dates_are_the_30_days_before_the_period():
    assert rules.baseline_dates(FROM) == (date(2026, 10, 4), date(2026, 11, 2))
    # 기간이 한국 날짜 중간에 시작하면 그날은 기간과 겹치므로 뺌
    assert rules.baseline_dates(FROM + timedelta(hours=5)) == (date(2026, 10, 4), date(2026, 11, 2))


def test_baseline_median_days_change(catalog):
    rows = daily("period.speed_median_mps", [1.0, 1.1, 1.2, 0.9, 1.0, 1.3, 1.1, 1.05])
    rows += daily("period.speed_median_mps", [5.0, 5.0], sufficient=False)  # 충분하지 않은 날은 안 셈
    ctx = context(catalog, [value("period.speed_median_mps", 1.2)], rows)
    b = ctx["values"][0]["baseline"]
    assert b["days"] == 8
    assert b["median"] == pytest.approx(1.075)
    assert b["change"] == pytest.approx(0.125)


def test_baseline_needs_seven_days_and_matches_side(catalog):
    left = daily("period.stride_time_cv", [0.03] * 6, side="left")
    right = daily("period.stride_time_cv", [0.04] * 7, side="right")
    vals = [value("period.stride_time_cv", 0.031, side="left", unit="ratio"),
            value("period.stride_time_cv", 0.05, side="right", unit="ratio")]  # fmt: skip
    ctx = context(catalog, vals, left + right)
    assert "baseline" not in ctx["values"][0]
    assert ctx["values"][1]["baseline"] == {"median": 0.04, "days": 7, "change": pytest.approx(0.01)}


def test_no_baseline_for_a_null_value(catalog):
    rows = daily("period.speed_median_mps", [1.0] * 10)
    ctx = context(catalog, [value("period.speed_median_mps", None, reason="no_data")], rows)
    assert "baseline" not in ctx["values"][0]


def test_sub_day_period_is_not_comparable(catalog):
    assert context(catalog, [])["baseline_comparable"] is True
    assert context(catalog, [], to_at=FROM + timedelta(days=1))["baseline_comparable"] is True
    assert context(catalog, [], to_at=FROM + timedelta(hours=3))["baseline_comparable"] is False


# --- 값의 글 꼴 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "unit", "text"),
    [
        (1.0567, "m/s", "1.06 m/s"),
        (1.2, "m", "1.20 m"),
        (0.0712, "m", "0.0712 m"),
        (0.031, "ratio", "3.10%"),
        (0.5, "ratio", "50.0%"),
        (1.0, "ratio", "100%"),
        (12, "count", "12"),
        (1234.0, "count", "1,234"),
        (12.34, "s", "12.3초"),
        (95.0, "s", "1.58분"),
        (5400.0, "s", "1.50시간"),
        (104.7, "steps/min", "105걸음/분"),
        (12.0, "deg", "12.0°"),
        (0.0, "m", "0 m"),
        (2345.6, "m", "2,346 m"),
    ],
)
def test_format_value(x, unit, text):
    assert rules.format_value(x, unit) == text


def test_format_change_has_sign_and_points_for_ratio():
    assert rules.format_change(0.004, "ratio") == "+0.400%p"
    assert rules.format_change(-0.125, "m/s") == "-0.125 m/s"


# --- 자리 표시 (RP-8) --------------------------------------------------------------------


def passage(n: int, key="hausdorff2001", i=3) -> rules.PassageRef:
    return rules.PassageRef(n, f"{key}#{i}", key, None, "text", f"Title {key}", ("Hausdorff JM", "Rios DA"),
                            2001, f"10.1/{key}")  # fmt: skip


def ctx3(catalog) -> dict:
    return context(catalog, [value("period.wear_time_s", 7200.0, unit="s", n=1),
                             value("period.speed_median_mps", 1.06),
                             value("period.stride_time_cv", 0.031, side="left", unit="ratio",
                                   where={"bout_min_s": 30}),
                             value("period.speed_p95_mps", 1.4)])  # fmt: skip


def test_check_accepts_known_placeholders(catalog):
    text = "걷기 속도는 {v2}였다 [p1]. 변동은 {v3}로 보고된 범위와 비슷하다[p2]."
    assert rules.check_text(text, ctx3(catalog), {1: passage(1), 2: passage(2, i=4)}) == []


def test_check_reports_unknown_value_and_passage(catalog):
    problems = rules.check_text("속도 {v9}, 근거 [p7].", ctx3(catalog), {1: passage(1)})
    assert any("{v9}" in p for p in problems)
    assert any("[p7]" in p for p in problems)


@pytest.mark.parametrize(
    "text",
    ["걷기 속도는 1.06 m/s였다.", "변동이 3.1%였다.", "하루 45분 걸었다.", "보폭은 1.2m였다.", "분당 105걸음/분",
     "속도 0.9 m/s로 느렸다."],
)  # fmt: skip
def test_typed_measured_number_is_a_problem(catalog, text):
    problems = rules.check_text(text, ctx3(catalog), {})
    assert problems and "숫자" in problems[0]


@pytest.mark.parametrize(
    "text",
    [
        "지난 30일과 비교했다.",  # 일은 측정 단위가 아님
        "Hausdorff 등(2001)의 연구에서 보고되었다 [p1].",  # 연도
        "30초 이상 걷기에서 계산한 변동이다.",  # where의 문턱 값은 정의이지 측정값이 아님
        "문헌에서는 1.0 m/s 아래의 속도가 낙상과 함께 보고되었다 [p1].",  # 인용한 문장의 문헌 값
        "걷기 횟수는 {v1}회였다.",
        "3명 중 2명이 그랬다.",
    ],
)
def test_ordinary_numbers_pass(catalog, text):
    assert rules.check_text(text, ctx3(catalog), {1: passage(1)}) == []


def test_numbers_inside_metric_labels_pass(catalog):
    ctx = context(catalog, [value("period.speed_10_30s_median_mps", 1.0, where={"bout_min_s": 10, "bout_max_s": 30})])
    assert rules.check_text("걷기 속도(10–30초 걷기)는 {v1}였다.", ctx, {}) == []
    assert rules.check_text("걷기 속도(10–30초 걷기)는 1.0 m/s였다.", ctx, {}) != []


def test_render_substitutes_values_and_numbers_citations_in_first_use_order(catalog):
    cites = rules.Citations()
    passages = {1: passage(1), 2: passage(2, key="shah2020", i=1)}
    out = rules.render("변동 {v3} [p2]. 속도 {v2} [p1]. 다시 [p2].", ctx3(catalog), passages, cites)
    assert out == "변동 3.10% [1]. 속도 1.06 m/s [2]. 다시 [1]."
    refs = rules.references(cites, passages)
    assert refs.splitlines()[0].startswith("1. ")
    assert "shah2020#1" in refs.splitlines()[0] and "Title shah2020" in refs.splitlines()[0]
    assert "hausdorff2001#3" in refs.splitlines()[1] and "10.1/hausdorff2001" in refs.splitlines()[1]


def test_render_null_value(catalog):
    ctx = context(catalog, [value("period.speed_median_mps", None, reason="no_data")])
    assert rules.render("{v1}", ctx, {}, rules.Citations()) == "값 없음"


# --- 보고서의 부분 (RP-8) -------------------------------------------------------------------

GOOD = """## 요약
오늘은 {v1} 동안 자료가 있었다.

## 활동량
걷기는 고르게 나뉘었다.

## 속도와 보폭
속도는 {v2}였다 [p1].

## 종합 풀이
변동 {v3}은 문헌에서 이런 양상과 함께 보고되었다 [p1].
"""


def test_parse_parts_in_order():
    parts, problems = rules.parse_parts(GOOD)
    assert problems == []
    assert list(parts) == ["요약", "활동량", "속도와 보폭", "종합 풀이"]
    assert parts["속도와 보폭"] == "속도는 {v2}였다 [p1]."


def test_parse_parts_reports_missing_and_unknown():
    _, problems = rules.parse_parts("## 요약\n짧다.\n\n## 기타\n뭔가.\n\n## 종합 풀이\n\n")
    text = " ".join(problems)
    assert "활동량" in text and "속도와 보폭" in text and "기타" in text and "종합 풀이" in text


def test_check_report_summary_uses_basic_values_only(catalog):
    parts, _ = rules.parse_parts(GOOD.replace("오늘은 {v1}", "오늘은 {v4}"))  # v4 = speed_p95 (detail)
    problems = rules.check_report(parts, ctx3(catalog), {1: passage(1)})
    assert any("요약" in p and "{v4}" in p for p in problems)
    parts, _ = rules.parse_parts(GOOD)
    assert rules.check_report(parts, ctx3(catalog), {1: passage(1)}) == []


def test_assemble_report_has_eight_parts_in_order(catalog):
    ctx = context(catalog, [
        value("period.wear_time_s", 7200.0, unit="s", n=1),
        value("period.walking_time_s", 1800.0, unit="s", n=1),
        value("period.speed_median_mps", 1.06, n=10, sufficient=False),
        value("period.stride_time_cv", 0.031, side="left", unit="ratio"),
    ], to_at=FROM + timedelta(hours=3))  # fmt: skip
    parts, _ = rules.parse_parts(GOOD.replace("{v3}", "{v4}").replace("{v2}", "{v3}"))
    md = rules.assemble_report(ctx, parts, {1: passage(1)},
                               versions={"papers": "abc", "prompt": "p/1", "model": "m"})  # fmt: skip
    heads = [line for line in md.splitlines() if line.startswith("#")]
    assert heads == ["# 보행 보고서", "## 요약", "## 자료 범위", "## 활동량", "## 속도와 보폭", "## 종합 풀이",
                     "## 나머지 값", "## 한계와 근거 문헌"]  # fmt: skip
    assert rules.DISCLAIMER in md and rules.LIMITATIONS in md and rules.NOT_COMPARABLE_NOTE in md
    assert "S01" in md and "abc" in md and "p/1" in md
    sections = md.split("\n## ")
    activity = next(s for s in sections if s.startswith("활동량"))
    speed = next(s for s in sections if s.startswith("속도와 보폭"))
    rest = next(s for s in sections if s.startswith("나머지 값"))
    assert "| v2 " in activity and "걷기 시간" in activity
    assert "| v3 " in speed and rules.INSUFFICIENT in speed.split("| v3 ")[1].splitlines()[0]
    assert "| v4 " in rest and "| v1 " in rest
    assert "| v3 " not in rest and "| v2 " not in rest
    assert "속도는 1.06 m/s였다 [1]." in speed  # 모델 글의 자리 표시를 풀고 인용 번호를 붙임
    assert "hausdorff2001#3" in md.split("## 한계와 근거 문헌")[1]


def test_table_groups_partition_known_metrics(catalog):
    period = {m.id for m in catalog.metrics_of("period")}
    assert set(rules.ACTIVITY) <= period and set(rules.SPEED_STRIDE) <= period
    assert not set(rules.ACTIVITY) & set(rules.SPEED_STRIDE)


def test_correction_request_quotes_problems():
    text = rules.correction_request(["[p9]는 없는 대목", "요약이 비었음"])
    assert "[p9]는 없는 대목" in text and "요약이 비었음" in text
