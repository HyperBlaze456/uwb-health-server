"""대화 코어 (SPEC RP-0, RP-6, RP-7, RP-8).

착용자 한 명·기간 하나의 맥락(uwbserver.report_input/1)과 읽어 둔 논문 색인으로 Chat을 만들고, 모델과 묻고 답합니다.
모델은 도구 search_papers로 논문 대목을 스스로 찾고, 서버는 돌려준 대목에 대화 전체에서 이어지는 번호 p1, p2, …를
붙입니다. 보고서(report)는 이 코어에 정해진 지시를 주는 한 가지 쓰임입니다.

- load_context: DB에서 맥락을 모음(store → metrics.period_values → rules.build_context).
- Chat.ask: 질문 하나. 도구 고리를 돌고 원문, 자리 표시를 푼 글, 검사에 걸린 문제를 돌려줌.
- Chat.report: 네 부분을 받아 검사하고, 걸리면 문제를 보여 주며 한 번 다시 쓰게 하고, 여덟 부분의 Markdown으로 엮음.
- Chat.trace: 맥락, 주고받은 말, 찾은 기록(질문, 조건, 돌려준 대목 id와 순위)을 한 덩어리로(RP-7).

대화는 뒤에 붙이기만 합니다(앞의 메시지를 고치거나 지우지 않음). 모델의 응답 블록은 받은 그대로 되돌리고, 한 번의
응답에서 부른 도구들의 결과는 user 메시지 하나로 돌려줍니다.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import psycopg

from uwbserver.pipeline import metrics
from uwbserver.report import papers, rules, store, writer
from uwbserver.report.writer import Model, ToolCall

TRACE_SCHEMA = "uwbserver.report_trace/1"
MAX_TURNS = 40  # 질문 하나에서 모델을 부르는 최대 횟수(도구 고리가 끝나지 않을 때 멈춤)
SEARCH_METHOD = "hybrid"


class ChatError(Exception):
    """질문을 끝내지 못함. 메시지는 사람이 읽고 고칠 수 있게 씁니다."""


class RefusalError(ChatError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"the model refused to answer ({reason})")
        self.reason = reason


class ReportError(ChatError):
    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("the report failed the server checks twice:\n" + "\n".join(f"- {p}" for p in problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class Answer:
    raw: str  # 모델이 쓴 그대로(자리 표시 포함)
    text: str  # 자리 표시를 풀고 문헌 목록을 붙인 글(보고서는 여덟 부분의 Markdown)
    problems: list[str]  # 서버 검사에 걸린 것(보고서는 통과해야 돌려주므로 늘 빈 목록)


# --- DB에서 맥락 모으기 -------------------------------------------------------------------------


def load_context(
    conn: psycopg.Connection, catalog: metrics.Catalog, subject_id: int, from_at: datetime, to_at: datetime
) -> dict:
    """RP-2·RP-3·RP-4·RP-7: 착용자와 기간 → 맥락. 착용자가 없으면 LookupError. 걷기가 없어도 맥락은 만듦(값은 no_data)."""
    who = store.subject(conn, subject_id)
    if who is None:
        raise LookupError(f"unknown subject {subject_id}")
    bouts = store.period_bouts(conn, subject_id, from_at, to_at)
    records = store.records_of_bouts(conn, [b["id"] for b in bouts]) if bouts else []
    formats = {b["run_id"]: b["formats"] for b in bouts}
    data = metrics.bouts_from_rows(catalog, bouts, records, formats)
    wear_s = store.wear_seconds(conn, subject_id, from_at, to_at)
    values = metrics.period_values(catalog, data, wear_time_s=wear_s)
    first, last = rules.baseline_dates(from_at)
    runs = sorted(formats)
    feet = {s for b in bouts for s in (("left", "right") if b["sides"] == "both" else (b["sides"],))}
    coverage = {
        "wear_time_s": wear_s,
        "feet": [s for s in ("left", "right") if s in feet],
        "partial_runs": len({b["run_id"] for b in bouts if b["result_status"] == "partial"}),
        "ambiguous_bouts": store.ambiguous_bouts(conn, subject_id, from_at, to_at),
        "runs_not_unpacked": sum(1 for f in formats.values() if f is None),
    }  # fmt: skip
    return rules.build_context(
        catalog, subject=who, from_at=from_at, to_at=to_at, values=values,
        daily_rows=store.daily_values(conn, subject_id, first, last), coverage=coverage, runs=runs,
    )  # fmt: skip


# --- 대화 ---------------------------------------------------------------------------------------


class Chat:
    def __init__(
        self,
        context: Mapping,
        index: papers.Index,
        model: Model,
        *,
        papers: Mapping[str, papers.Paper],
        sources: Mapping[str, Sequence[str]],
        papers_version: str = "",
        max_searches: int = 12,
    ) -> None:
        """papers: 논문 목록(서지), sources: 지표 id → 근거 논문 key(rules.metric_sources), max_searches: 질문 하나의
        search_papers 한도(RP-6). 한도를 넘는 부름에는 모델이 읽을 수 있는 오류 결과를 돌려줌."""
        self.context = context
        self.index = index
        self.model = model
        self.papers = papers
        self.sources = sources
        self.papers_version = papers_version
        self.max_searches = max_searches
        self.messages: list[dict] = []
        self.passages: dict[int, rules.PassageRef] = {}
        self._numbers: dict[str, int] = {}  # 대목 id → 번호
        self._events: list[dict] = []

    # ---- 바깥에 보이는 것

    def ask(self, text: str) -> Answer:
        raw = self._run(text)
        problems = rules.check_text(raw, self.context, self.passages)
        cites = rules.Citations()
        rendered = rules.render(raw, self.context, self.passages, cites)
        if cites.order:
            rendered += "\n\n근거 문헌\n" + rules.references(cites, self.passages)
        self._events.append({"type": "answer", "raw": raw, "problems": problems})
        return Answer(raw, rendered, problems)

    def report(self) -> Answer:
        raw = self._run(writer.REPORT_INSTRUCTION)
        parts, problems = self._check_report(raw)
        if problems:
            raw = self._run(rules.correction_request(problems))
            parts, problems = self._check_report(raw)
            if problems:
                raise ReportError(problems)
        versions = {"papers": self.papers_version, "prompt": writer.PROMPT_VERSION, "model": self.model.name}
        text = rules.assemble_report(self.context, parts, self.passages, versions=versions)
        self._events.append({"type": "report", "raw": raw})
        return Answer(raw, text, [])

    def trace(self) -> dict:
        """RP-7의 기록: 맥락, 주고받은 말(보이는 글), 찾은 기록. JSON으로 저장할 수 있음."""
        return {
            "schema": TRACE_SCHEMA,
            "prompt_version": writer.PROMPT_VERSION,
            "model": self.model.name,
            "papers_version": self.papers_version,
            "context": self.context,
            "passages": {f"p{n}": p.passage_id for n, p in sorted(self.passages.items())},
            "events": list(self._events),
        }

    # ---- 안쪽

    def _check_report(self, raw: str) -> tuple[dict[str, str], list[str]]:
        parts, problems = rules.parse_parts(raw)
        problems += rules.check_report(parts, self.context, self.passages)
        self._events.append({"type": "check", "problems": problems})
        return parts, problems

    def _run(self, text: str) -> str:
        """질문 하나의 도구 고리. 모델의 마지막 글을 돌려줌."""
        content = text if self.messages else f"{writer.context_message(self.context)}\n\n{text}"
        self.messages.append({"role": "user", "content": content})
        self._events.append({"type": "user", "text": text})
        searches = 0
        for _ in range(MAX_TURNS):
            turn = self.model.turn(writer.SYSTEM_PROMPT, [writer.SEARCH_TOOL], self.messages)
            self._events.append({"type": "model", "model": turn.model or self.model.name,
                                 "stop_reason": turn.stop_reason, "text": turn.text,
                                 "tool_calls": [{"name": c.name, "input": c.input} for c in turn.tool_calls]})  # fmt: skip
            if turn.stop_reason == "refusal":
                # 거절한 응답은 대화에 넣지 않음. 다음 질문은 이어 붙는 user 메시지가 됨(API가 한 차례로 합침)
                raise RefusalError(turn.refusal or "no details")
            if turn.stop_reason == "max_tokens":
                raise ChatError("the model hit max_tokens before finishing; ask a narrower question")
            self.messages.append({"role": "assistant", "content": turn.content})
            if turn.tool_calls:
                results = []
                for call in turn.tool_calls:
                    if call.name != writer.SEARCH_TOOL["name"]:
                        results.append(self._error(call, f"unknown tool {call.name!r}"))
                    elif searches >= self.max_searches:
                        self._events.append({"type": "search", "input": call.input, "error": "limit"})
                        results.append(self._error(call, f"search limit reached ({self.max_searches} per question); "
                                                         "answer with the passages you already have"))  # fmt: skip
                    else:
                        searches += 1
                        results.append(self._search(call))
                self.messages.append({"role": "user", "content": results})
                continue
            if turn.stop_reason == "pause_turn":  # 서버 쪽 도구가 멈춤. 그대로 다시 보내면 이어짐
                continue
            if turn.stop_reason in ("end_turn", "stop_sequence"):
                return turn.text
            raise ChatError(f"unexpected stop_reason {turn.stop_reason!r}")
        raise ChatError(f"the model did not finish within {MAX_TURNS} turns")

    @staticmethod
    def _error(call: ToolCall, message: str) -> dict:
        return {"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                "content": json.dumps({"error": message}, ensure_ascii=False)}  # fmt: skip

    def _search(self, call: ToolCall) -> dict:
        """search_papers 한 번(RP-6). 틀린 입력과 모르는 지표는 모델이 읽을 수 있는 오류 결과."""
        args = call.input if isinstance(call.input, dict) else {}
        query, wanted, k = args.get("query"), args.get("metrics"), args.get("k")
        event: dict = {"type": "search", "query": query, "metrics": wanted, "k": k, "method": SEARCH_METHOD,
                       "keys": None, "warnings": [], "results": []}  # fmt: skip
        self._events.append(event)
        problem = None
        if not isinstance(query, str) or not query.strip():
            problem = "query must be a non-empty string"
        elif wanted is not None and not (isinstance(wanted, list) and all(isinstance(m, str) for m in wanted)):
            problem = "metrics must be a list of metric ids"
        elif k is not None and (type(k) is not int):
            problem = "k must be an integer"
        elif wanted and (unknown := [m for m in wanted if m not in self.sources]):
            problem = f"unknown metric id(s): {', '.join(unknown)}; use the metric field of the context values"
        if problem is not None:
            event["error"] = problem
            return self._error(call, problem)

        k = min(max(k if k is not None else writer.SEARCH_K_DEFAULT, 1), writer.SEARCH_K_MAX)
        event["k"] = k
        keys = None
        if wanted:
            listed = list(dict.fromkeys(key for m in wanted for key in self.sources[m]))
            keys = [key for key in listed if key in self.index.keys]
            missing = [key for key in listed if key not in self.index.keys]
            if missing:
                event["warnings"].append(f"source papers not in the store, skipped: {', '.join(missing)}")
            event["keys"] = keys
        hits = self.index.search(query, k, keys=keys, method=SEARCH_METHOD)
        out = []
        for rank, hit in enumerate(hits, 1):
            n = self._number(hit.passage)
            event["results"].append({"p": f"p{n}", "passage": hit.passage.id, "rank": rank, "score": hit.score,
                                     "bm25_rank": hit.bm25_rank, "vector_rank": hit.vector_rank})  # fmt: skip
            ref = self.passages[n]
            out.append({"p": f"p{n}", "paper": ref.key, "title": ref.title, "authors": list(ref.authors),
                        "year": ref.year, "page": ref.page, "text": ref.text})  # fmt: skip
        body: dict = {"results": out}
        if wanted and not keys:
            body["note"] = "these metrics have no source papers in the store; search without metrics to look in all papers"
        if event["warnings"]:
            body["warnings"] = event["warnings"]
        return {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(body, ensure_ascii=False)}

    def _number(self, passage: papers.Passage) -> int:
        """대목의 번호. 처음 돌려줄 때 다음 번호를 주고, 다시 돌려주면 처음 번호 그대로."""
        if passage.id in self._numbers:
            return self._numbers[passage.id]
        n = len(self._numbers) + 1
        self._numbers[passage.id] = n
        paper = self.papers.get(passage.key)
        self.passages[n] = rules.PassageRef(
            n, passage.id, passage.key, passage.page, passage.text,
            paper.title if paper else passage.key, tuple(paper.authors) if paper else (),
            paper.year if paper else None, paper.doi if paper else None,
        )  # fmt: skip
        return n
