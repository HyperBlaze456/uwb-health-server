"""대화 코어(report/chat.py)의 시험 (SPEC RP-0, RP-6, RP-7, RP-8). 가짜 모델과 tmp 논문 저장소. DB·네트워크 없음."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from uwbserver.pipeline import metrics
from uwbserver.pipeline.metrics import Value
from uwbserver.report import chat, papers, rules, writer
from uwbserver.report.writer import ToolCall, Turn

FROM = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)

TEXTS = {
    "hausdorff2001": "Stride time variability reflects rhythmic control of gait.\n\n"
    "Higher stride time variability is reported together with falls in older adults.",
    "schlachetzki2017": "Gait speed was lower in Parkinson's disease.\n\nStride length was shorter in patients.",
    "shah2020": "Swing time variability was higher in people with Parkinson's disease.",
}


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("papers")
    for i, (key, text) in enumerate(TEXTS.items()):
        papers.add_paper(root, papers.Paper(key, f"Title {key}", [f"Author{i} A"], int(key[-4:]), f"10.1/{key}"), text)
    papers.build_index(root, papers.NgramEmbedder())
    return root


@pytest.fixture(scope="module")
def context() -> dict:
    cat = metrics.load()

    def v(metric, x, unit, side=None, n=40):
        return Value(metric, side, x, unit, n, True, "unverified", {})

    return rules.build_context(
        cat,
        subject={"code": "S01"},
        from_at=FROM,
        to_at=FROM + timedelta(days=1),
        values=[v("period.wear_time_s", 7200.0, "s", n=1), v("period.speed_median_mps", 1.06, "m/s"),
                v("period.stride_time_cv", 0.031, "ratio", "left"), v("period.speed_p95_mps", 1.4, "m/s")],
        daily_rows=[],
        coverage={"wear_time_s": 7200.0, "feet": ["left"], "partial_runs": 0, "ambiguous_bouts": 0,
                  "runs_not_unpacked": 0},
        runs=[1],
    )  # fmt: skip


SOURCES = {
    "period.stride_time_cv": ("hausdorff2001", "missing2020"),  # missing2020은 논문 목록에 없음
    "period.speed_median_mps": ("schlachetzki2017",),
    "period.wear_time_s": (),
}


class Script:
    """정해 둔 단계대로 답하는 가짜 모델. 단계는 Turn이거나, 받은 대화(messages) → Turn 함수."""

    name = "script"

    def __init__(self, *steps: Turn | Callable[[list[dict]], Turn]) -> None:
        self.steps = list(steps)
        self.seen: list[list[dict]] = []

    def turn(self, system, tools, messages):
        assert system == writer.SYSTEM_PROMPT and tools == [writer.SEARCH_TOOL]
        self.seen.append(json.loads(json.dumps(messages, default=str)))
        step = self.steps.pop(0)
        return step(messages) if callable(step) else step


_ids = iter(range(1, 10_000))


def search(*inputs: dict) -> Turn:
    calls = tuple(ToolCall(f"tu{next(_ids)}", "search_papers", i) for i in inputs)
    return Turn("tool_use", "", calls, [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls])


def say(text: str) -> Turn:
    return Turn("end_turn", text, (), [{"type": "text", "text": text}])


def results(messages: list[dict]) -> list[dict]:
    """마지막 user 메시지의 tool_result들(내용은 JSON 글)."""
    return [{**r, "content": json.loads(r["content"])} for r in messages[-1]["content"]]


def make(store, context, model, **kw) -> chat.Chat:
    return chat.Chat(context, papers.load_index(store, None), model, papers=papers.read_papers(store),
                     sources=SOURCES, papers_version="pv", **kw)  # fmt: skip


def test_two_searches_then_answer(store, context):
    model = Script(search({"query": "gait speed"}),
                   search({"query": "stride time variability falls", "metrics": ["period.stride_time_cv"], "k": 2}),
                   lambda m: say(f"속도는 {{v2}}였다 [p1]. 변동 {{v3}}은 낙상과 함께 보고되었다 "
                                 f"[{results(m)[0]['content']['results'][0]['p']}]."))  # fmt: skip
    c = make(store, context, model)
    a = c.ask("이 기간을 풀이해 줘")
    assert a.problems == []
    assert "{v" not in a.text and "[p" not in a.text
    assert "속도는 1.06 m/s였다 [1]" in a.text and "3.10%" in a.text
    assert "근거 문헌" in a.text and "Title" in a.text
    # 첫 메시지: 맥락 + 질문. 이어서 assistant(tool_use) → user(tool_result)
    first = model.seen[0][0]
    assert first["role"] == "user" and "uwbserver.report_input/1" in first["content"] and "이 기간을" in first["content"]
    second = model.seen[2]
    hits = results(second)[0]["content"]["results"]
    assert len(hits) <= 2 and all(h["paper"] == "hausdorff2001" for h in hits)  # metrics → 그 지표의 sources만
    # 기록
    events = c.trace()["events"]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "user" and kinds.count("search") == 2 and kinds[-1] == "answer"
    s2 = [e for e in events if e["type"] == "search"][1]
    assert s2["query"] == "stride time variability falls" and s2["metrics"] == ["period.stride_time_cv"]
    assert s2["k"] == 2 and s2["method"] == "hybrid" and s2["keys"] == ["hausdorff2001"]
    assert any("missing2020" in w for w in s2["warnings"])  # 목록에 없는 key는 빼고 나머지에서 찾음
    assert [r["rank"] for r in s2["results"]] == list(range(1, len(s2["results"]) + 1))
    assert all(r["passage"].startswith("hausdorff2001#") and r["p"].startswith("p") for r in s2["results"])
    model_events = [e for e in events if e["type"] == "model"]
    assert model_events[-1]["text"].startswith("속도는 {v2}")
    trace = c.trace()
    assert trace["context"] == context and trace["prompt_version"] == writer.PROMPT_VERSION
    assert trace["papers_version"] == "pv" and trace["model"] == "script"
    json.dumps(trace)  # 저장할 수 있음


def test_numbering_continues_across_questions(store, context):
    model = Script(search({"query": "gait speed", "k": 2}), say("첫 답."),
                   search({"query": "gait speed", "k": 3}), say("둘째 답."))  # fmt: skip
    c = make(store, context, model)
    c.ask("하나")
    first = {r["passage"]: r["p"] for r in c.trace()["events"] if r["type"] == "search" for r in r["results"]}
    c.ask("둘")
    second = [e for e in c.trace()["events"] if e["type"] == "search"][1]["results"]
    for r in second:
        if r["passage"] in first:
            assert r["p"] == first[r["passage"]]  # 두 번 돌려준 대목은 처음 번호 그대로
    new = [int(r["p"][1:]) for r in second if r["passage"] not in first]
    assert new == list(range(len(first) + 1, len(first) + 1 + len(new)))
    # 둘째 질문은 맥락 없이 질문만, 대화는 뒤에 붙이기만 함
    assert model.seen[2][-1] == {"role": "user", "content": "둘"}
    assert model.seen[2][: len(model.seen[1])] == model.seen[1][: len(model.seen[1])]


def test_parallel_calls_answered_in_one_message(store, context):
    model = Script(search({"query": "gait speed"}, {"query": "variability"}), say("끝."))
    make(store, context, model).ask("q")
    last = model.seen[1][-1]
    assert last["role"] == "user" and [r["type"] for r in last["content"]] == ["tool_result", "tool_result"]


def test_typed_number_is_reported(store, context):
    a = make(store, context, Script(say("걷기 속도는 1.06 m/s였다."))).ask("속도는?")
    assert any("1.06 m/s" in p for p in a.problems)


def test_search_bound(store, context):
    model = Script(search({"query": "a"}, {"query": "b"}, {"query": "c"}), say("끝."))
    c = make(store, context, model, max_searches=2)
    c.ask("q")
    rs = results(model.seen[1])
    assert [bool(r.get("is_error")) for r in rs] == [False, False, True]
    assert "limit" in rs[2]["content"]["error"]
    assert len([e for e in c.trace()["events"] if e["type"] == "search"]) == 3  # 거절한 것도 기록


def test_unknown_metric_is_an_error_result(store, context):
    model = Script(search({"query": "x", "metrics": ["period.nope"]}), say("끝."))
    make(store, context, model).ask("q")
    r = results(model.seen[1])[0]
    assert r["is_error"] is True and "period.nope" in r["content"]["error"]


def test_bad_tool_input_is_an_error_result(store, context):
    model = Script(search({"k": 3}), say("끝."))
    make(store, context, model).ask("q")
    assert results(model.seen[1])[0]["is_error"] is True


def test_refusal_is_a_clear_error(store, context):
    model = Script(Turn("refusal", "", (), [], refusal="bio: declined"))
    with pytest.raises(chat.RefusalError, match="bio: declined"):
        make(store, context, model).ask("q")


def report_text(cite: str) -> str:
    return (f"## 요약\n자료가 있던 시간은 {{v1}}이었다.\n\n## 활동량\n고르게 걸었다.\n\n"
            f"## 속도와 보폭\n속도는 {{v2}}였다.\n\n## 종합 풀이\n변동 {{v3}}은 낙상과 함께 보고되었다 {cite}.\n")  # fmt: skip


def test_report_assembles_eight_parts(store, context):
    model = Script(search({"query": "variability"}), say(report_text("[p1]")))
    a = make(store, context, model).report()
    assert writer.REPORT_INSTRUCTION in model.seen[0][0]["content"]
    heads = [line for line in a.text.splitlines() if line.startswith("#")]
    assert heads == ["# 보행 보고서", "## 요약", "## 자료 범위", "## 활동량", "## 속도와 보폭", "## 종합 풀이",
                     "## 나머지 값", "## 한계와 근거 문헌"]  # fmt: skip
    assert "[1]" in a.text and "{v" not in a.text and rules.DISCLAIMER in a.text
    assert a.raw == report_text("[p1]")


def test_report_retries_once_with_the_problems(store, context):
    model = Script(say(report_text("[p9]")), search({"query": "variability"}), say(report_text("[p1]")))
    a = make(store, context, model).report()
    retry = model.seen[1][-1]
    assert retry["role"] == "user" and "[p9]" in retry["content"]
    assert "[1]" in a.text


def test_report_fails_after_second_bad_answer(store, context):
    model = Script(say(report_text("[p9]")), say(report_text("[p9]")))
    with pytest.raises(chat.ReportError) as e:
        make(store, context, model).report()
    assert any("[p9]" in p for p in e.value.problems)


def test_report_missing_part_is_retried(store, context):
    model = Script(say("## 요약\n짧다.\n"), say(report_text("")))
    a = make(store, context, model).report()
    assert "## 종합 풀이" in a.text
    assert "활동량" in model.seen[1][-1]["content"]
