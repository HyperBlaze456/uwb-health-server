"""tools/eval_report_runs.py의 순수 계산과, 대역(가짜 판정·가짜 변형)으로 judge → make-corpus를 도는 시험.
DB·모델·네트워크가 없습니다."""

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import eval_checks
from tools import eval_report_runs as er
from uwbserver.report import rules

KST = timezone(timedelta(hours=9))

CTX = {
    "schema": "uwbserver.report_input/1",
    "values": [
        {"id": "v1", "metric": "period.speed_median_mps", "side": None, "value": 1.0, "unit": "m/s",
         "label": "걷기 속도", "show": "basic", "baseline": {"median": 1.2, "days": 8, "change": -0.2}},
        {"id": "v2", "metric": "period.speed_p95_mps", "side": None, "value": 1.4, "unit": "m/s",
         "label": "빠른 걷기 속도(95백분위)", "show": "detail"},
        {"id": "v3", "metric": "period.bout_count", "side": None, "value": 4, "unit": "count",
         "label": "걷기 횟수", "show": "basic", "baseline": {"median": 6, "days": 8, "change": -2}},
    ],
}  # fmt: skip

RAW = """## 요약
걷기 속도는 {v1}로 지난 30일보다 느렸습니다. 걷기 횟수는 {v3}회였습니다.

## 활동량
걷기 횟수 {v3}회는 평소보다 적었습니다.

## 속도와 보폭
빠른 걷기 속도는 {v2}였습니다 [p1].

## 종합 풀이
느린 속도는 문헌에서 낙상과 함께 보고되었습니다 [p2]. 이 양상은 다른 연구에서도 보고되었습니다 [p1].
"""


def record(run_id="day-2026-11-10-r1", raw=RAW, error=None, checks=((),), usage=None) -> dict:
    events = [{"type": "user", "text": "x"}, {"type": "search", "query": "q"}]
    events += [{"type": "check", "problems": list(c)} for c in checks]
    if error is None:
        events.append({"type": "report", "raw": raw})
    u = er.Usage(calls=3, input_tokens=100, cache_read_input_tokens=50, output_tokens=10).as_dict()
    return {"schema": er.RUN_SCHEMA, "run_id": run_id, "error": error, "elapsed_s": 2.0, "usage": usage or u,
            "markdown": None if error else "md",
            "trace": {"context": CTX, "passages": {"p1": "a2001#1", "p2": "b2002#3", "p3": "c2003#2"},
                      "events": events}}  # fmt: skip


def test_period_bounds_and_default_periods(tmp_path: Path):
    day = er.period_bounds("day-2026-11-10")
    assert day == (datetime(2026, 11, 10, tzinfo=KST), datetime(2026, 11, 11, tzinfo=KST))
    assert er.period_bounds("week-2026-11-04")[1] == datetime(2026, 11, 11, tzinfo=KST)
    with pytest.raises(ValueError):
        er.period_bounds("day-2026-11-10-r1")
    for name in ("day-2026-11-10.json", "week-2026-11-04.json", "day-2026-11-10-r1.json", "x.json"):
        (tmp_path / name).write_text("{}")
    assert er.default_periods(tmp_path) == ["day-2026-11-10", "week-2026-11-04"]
    assert er.run_ids(["day-2026-11-10"], 2) == ["day-2026-11-10-r1", "day-2026-11-10-r2"]


def test_metered_client_adds_usage_and_stops_over_the_budget():
    resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=600, cache_read_input_tokens=300,
                                                 cache_creation_input_tokens=200, output_tokens=7))  # fmt: skip
    inner = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp)))
    usage = er.Usage()
    client = er.MeteredClient(inner, usage, max_input=1000)
    client.beta.messages.create(model="m")
    assert usage.as_dict()["input_total"] == 1100 and usage.output_tokens == 7
    with pytest.raises(er.BudgetExceeded):
        client.beta.messages.create(model="m")


def test_summarize_runs():
    runs = [
        record("a-r1"),
        record("a-r2", checks=(["요약: 맥락에 없는 값 번호 {v9}"], [])),
        record("a-r3", error={"type": "ReportError", "problems": ["x"]},
               checks=(["요약: 측정값을 숫자로 직접 씀: '1 m'. 자리 표시 {vN}를 써야 함"], ["x"])),
        record("a-r4", error={"type": "RefusalError", "message": "no"}, checks=()),
    ]  # fmt: skip
    s = er.summarize_runs(runs)
    assert (s["runs"], s["succeeded"], s["failed_checks_twice"], s["refused"]) == (4, 2, 1, 1)
    assert s["drafted"] == 3 and s["first_draft_flagged"] == 2
    assert s["first_draft_problem_kinds"] == {"value_id_unknown": 1, "measured_number": 1}
    assert s["passed_after_rewrite_rate"] == 0.5
    m = s["mean_per_report"]
    assert m["values"] == 4 and m["citations"] == 3 and m["distinct_passages"] == 2 and m["searches"] == 1
    assert m["input_total"] == 150
    assert "runs 4" in er.summary_table(s)


def test_judge_items_and_comparison():
    ctx = rules.fill_directions(CTX)
    a = er.citation_items("r", RAW, ctx, {1: "a2001#1", 2: "b2002#3"})
    assert [(i["p"], i["passage_id"]) for i in a] == [(1, "a2001#1"), (2, "b2002#3"), (1, "a2001#1")]
    assert "1.40 m/s" in a[0]["filled"] and "[p1]" in a[0]["filled"]
    b = er.direction_items("r", RAW, ctx)
    assert [len(i["values"]) for i in b] == [1, 1, 1]  # v2는 baseline이 없어 빠짐
    assert b[0]["values"][0] | {} == {"id": "v1", "label": "걷기 속도", "side": "", "value": "1.00 m/s",
                                      "usual_median": "1.20 m/s", "direction": "lower"}  # fmt: skip
    assert not any(i["server_flagged"] for i in b)
    assert er.compare("higher", "lower") == "mismatch" and er.compare("lower", "lower") == "match"
    assert er.compare("no_claim", "lower") == "no_claim"
    assert er.sentence_category(["match", "mismatch"]) == "mismatch"
    assert er.sentence_category(["no_claim", "match"]) == "match"


@pytest.mark.parametrize(
    ("text", "verdict"),
    [('{"verdict": "partial", "reason": "r"}', "partial"), ('```json\n{"verdict": "supported"}\n```', "supported"),
     ('Sure: {"verdict": "not_supported"}', "not_supported"), ("nope", "invalid"), ('{"verdict": "maybe"}', "invalid")],
)  # fmt: skip
def test_parse_answer(text, verdict):
    assert er.verdict_of(er.parse_answer(text)) == verdict


def test_locate_makes_a_unique_find():
    text = "가. 나 {v1}. 가. 다 {v1}."
    start = text.index("다 {v1}") + 2
    find, replace = er.locate(text, start, start + 4, "{v9}")
    assert text.count(find) == 1 and eval_checks.apply_mutation(text, find, replace) == "가. 나 {v1}. 가. 다 {v9}."
    find, replace = er.locate(text, 0, 1, "라")  # '가.'는 두 번 나오므로 넓힘
    assert text.count(find) == 1 and eval_checks.apply_mutation(text, find, replace).startswith("라.")


def test_rule_mutations_follow_the_find_rule_and_are_caught_as_expected():
    ctx = rules.fill_directions(CTX)
    numbers = {1: "a2001#1", 2: "b2002#3", 3: "c2003#2"}
    muts, skipped = er.rule_mutations(RAW, ctx, numbers, random.Random(1))
    faults = {m["fault"] for m in muts}
    assert faults == {"citation_not_retrieved", "unsupported_citation", "value_id_unknown", "number_with_unit",
                      "number_without_unit", "summary_detail_value"} and skipped == []  # fmt: skip
    passages = eval_checks.passage_refs({str(k): v for k, v in numbers.items()})
    assert eval_checks.check(RAW, ctx, passages) == []
    for m in muts:
        text = eval_checks.apply_mutation(RAW, m["find"], m["replace"])
        kinds = {eval_checks.problem_kind(p) for p in eval_checks.check(text, ctx, passages)}
        if m["fault"] in ("citation_not_retrieved", "value_id_unknown", "number_with_unit", "summary_detail_value"):
            assert kinds & eval_checks.FAULT_KINDS[m["fault"]], m


def test_number_without_unit_strips_only_the_unit():
    ctx = rules.fill_directions(CTX)
    muts, _ = er.rule_mutations(RAW, ctx, {1: "a"}, random.Random(3))
    by = {m["fault"]: m for m in muts}
    assert "1.00 m/s" in by["number_with_unit"]["replace"] or "1.40 m/s" in by["number_with_unit"]["replace"]
    assert " m/s" not in by["number_without_unit"]["replace"]
    assert "unsupported_citation" not in by  # 다른 대목 번호가 없음


def test_model_mutation_keeps_placeholders():
    s = "걷기 속도는 {v1}로 지난 30일보다 느렸습니다."
    m = er.model_mutation(RAW, "direction_reversed", s, "걷기 속도는 {v1}로 지난 30일보다 빨랐습니다.")
    assert eval_checks.apply_mutation(RAW, m["find"], m["replace"]).count("빨랐습니다") == 1
    with pytest.raises(ValueError):
        er.model_mutation(RAW, "direction_reversed", s, "걷기 속도는 지난 30일보다 빨랐습니다.")
    with pytest.raises(ValueError):
        er.model_mutation(RAW, "direction_reversed", s, s)


def test_fake_judge_then_make_corpus_end_to_end(tmp_path: Path, capsys):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "day-2026-11-10-r1.json").write_text(json.dumps(record(), ensure_ascii=False))
    (runs / "day-2026-11-10-r2.json").write_text(json.dumps(record("day-2026-11-10-r2", error={"type": "ReportError"})))
    judge = tmp_path / "judge.json"
    assert er.main(["judge", "--fake", "--runs", str(runs), "--out", str(judge), "--papers-root", str(tmp_path),
                    "--sample", "4"]) == 0  # fmt: skip
    j = json.loads(judge.read_text())
    assert j["runs"] == ["day-2026-11-10-r1"]
    assert j["summary"]["direction_values"]["match"] == 2  # 가짜 판정이 '느렸', '적었'을 lower로 읽음
    assert j["summary"]["citations"]["error"] == 3  # 대목 글이 없는 저장소
    assert (tmp_path / "judge_cache.jsonl").exists()
    corpus = tmp_path / "corpus.json"
    rc = er.main(["make-corpus", "--fake", "--runs", str(runs), "--judge", str(judge), "--corpus-out", str(corpus),
                  "--contexts-out", str(tmp_path / "ctx"), "--checks-out", str(tmp_path / "checks.json")])  # fmt: skip
    assert rc == 0
    c = json.loads(corpus.read_text())
    [item] = c["items"]
    assert item["context"] == "day-2026-11-10-r1.json" and (tmp_path / "ctx" / item["context"]).exists()
    assert item["passages"] == {"1": "a2001#1", "2": "b2002#3", "3": "c2003#2"}
    assert {m["fault"] for m in item["mutations"]} == set(eval_checks.FAULT_KINDS)
    result = json.loads((tmp_path / "checks.json").read_text())
    assert result["faults"]["direction_reversed"]["n"] == 1 and not result["errors"]
