"""tools/eval_checks.py의 순수 계산 시험. DB·모델·네트워크가 없습니다."""

import json

import pytest

from tools import eval_checks
from uwbserver.report import rules

CTX = {
    "schema": "uwbserver.report_input/1",
    "values": [
        {"id": "v1", "metric": "period.speed_median_mps", "side": None, "value": 1.0, "unit": "m/s",
         "label": "걷기 속도", "show": "basic", "baseline": {"median": 1.2, "days": 8, "change": -0.2}},
        {"id": "v2", "metric": "period.speed_p95_mps", "side": None, "value": 1.4, "unit": "m/s",
         "label": "빠른 걷기 속도(95백분위)", "show": "detail"},
    ],
}  # fmt: skip

CLEAN = """## 요약
걷기 속도는 {v1}로 지난 30일보다 느렸습니다.

## 활동량
걷기는 고르게 나뉘었습니다.

## 속도와 보폭
빠른 걷기 속도는 {v2}였습니다 [p1].

## 종합 풀이
문헌에서 이런 양상이 보고되었습니다 [p1].
"""


def corpus(mutations) -> dict:
    return {"schema": eval_checks.CORPUS_SCHEMA,
            "items": [{"id": "a", "context": "c.json", "passages": {"1": "hausdorff2001#3"}, "text": CLEAN,
                       "mutations": mutations}]}  # fmt: skip


def test_every_problem_line_of_the_checks_has_a_kind():
    ctx = rules.fill_directions(CTX)
    passages = eval_checks.passage_refs({"1": "x#1"})
    lines = eval_checks.check("## 요약\n{v2}, {v9}, [p4].\n속도는 1.0 m/s였다.\n## 기타\n.\n## 요약\n.\n", ctx, passages)
    lines += eval_checks.check(CLEAN.replace("느렸습니다", "빨랐습니다"), ctx, passages)
    kinds = {eval_checks.problem_kind(p) for p in lines}
    assert kinds == {"structure", "value_id_unknown", "citation_not_retrieved", "measured_number",
                     "summary_detail_value", "direction"}  # fmt: skip


def test_apply_mutation_needs_exactly_one_match():
    assert eval_checks.apply_mutation("a b a", "b", "c") == "a c a"
    for find in ("a", "z", ""):
        with pytest.raises(ValueError):
            eval_checks.apply_mutation("a b a", find, "c")


def test_new_problems_subtracts_clean_lines_by_count():
    assert eval_checks.new_problems(["x", "y"], ["x", "x", "z"]) == ["x", "z"]


def test_evaluate_counts_and_rates():
    muts = [
        {"fault": "direction_reversed", "find": "느렸습니다", "replace": "빨랐습니다"},
        {"fault": "summary_detail_value", "find": "걷기 속도는 {v1}", "replace": "걷기 속도는 {v2}"},
        {"fault": "overclaim_wording", "find": "보고되었습니다", "replace": "진단됩니다"},
        {"fault": "number_with_unit", "find": "없는 글", "replace": "1 m"},  # 건너뜀
        {"fault": "made_up", "find": "느렸습니다", "replace": "x"},  # 건너뜀
    ]
    result = eval_checks.evaluate(corpus(muts), {"c.json": rules.fill_directions(CTX)})
    assert result["clean"] == {"n": 1, "flagged": 0, "flagged_items": []}
    f = result["faults"]
    d = f["direction_reversed"]
    assert (d["n"], d["kind"], d["new_kind"]) == (1, 1.0, 1.0)
    assert f["summary_detail_value"]["kind"] == 1.0
    assert (f["overclaim_wording"]["any"], f["overclaim_wording"]["kind"]) == (0.0, 0.0)
    assert f["number_without_unit"]["n"] == 0 and f["number_without_unit"]["any"] is None
    assert [e["mutation"] for e in result["errors"]] == [3, 4]
    json.dumps(result, ensure_ascii=False)  # JSON으로 쓸 수 있음
    assert "direction_reversed" in eval_checks.table(result)


def test_clean_problems_are_listed_and_not_counted_as_new():
    item_text = CLEAN.replace("고르게 나뉘었습니다", "45분 걸었습니다")  # clean에도 문제가 있음
    c = corpus([{"fault": "number_with_unit", "find": "느렸습니다", "replace": "1.0 m/s로 느렸습니다"}])
    c["items"][0]["text"] = item_text
    result = eval_checks.evaluate(c, {"c.json": rules.fill_directions(CTX)})
    assert result["clean"]["flagged"] == 1 and "45분" in result["clean"]["flagged_items"][0]["problems"][0]
    [m] = result["mutations"]
    assert m["any"] and m["new_kind"] and len(m["new_problems"]) == 1 and "1.0 m/s" in m["new_problems"][0]


def test_missing_context_is_an_error():
    result = eval_checks.evaluate(corpus([]), {})
    assert result["errors"] and result["clean"]["n"] == 0
