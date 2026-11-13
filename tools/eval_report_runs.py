"""실제 모델이 쓴 보고서로 서버 검사(RP-8)와 인용을 평가합니다. 하위 명령 넷.

    uv run python tools/eval_report_runs.py run [--repeats 2] [--max-runs N] [--fake --out DIR]
    uv run python tools/eval_report_runs.py summarize [--runs DIR]
    uv run python tools/eval_report_runs.py judge [--sample N] [--fake]
    uv run python tools/eval_report_runs.py make-corpus [--seed 1] [--fake]

run          기간 × 반복마다 tools/ask.py --report와 같은 길(chat.load_context → chat.Chat → Chat.report)로 보고서를
             만들고 run마다 기록 하나(RUN_SCHEMA)를 씀. 결과 파일이 이미 있는 run은 건너뜀(비용이 두 번 들지 않게).
             기간의 기본은 --contexts 안의 파일 이름 day-YYYY-MM-DD(그날 0시–다음날 0시 +09:00),
             week-YYYY-MM-DD(그날부터 7일).
             비용 안전장치: --max-runs, 첫 run 뒤 사용량 출력, run 하나의 입력 토큰(캐시 읽기·쓰기 포함)이
             --max-input-tokens-per-run을 넘으면 그 run을 멈추고 더 돌리지 않음. 병렬은 2개까지.
summarize    run 기록 → 성공·실패 수, 초안 검사(PROBLEM_KINDS 분류), 보고서 한 편당 평균. JSON과 표.
judge        최종 보고서의 문장을 OpenRouter의 판정 모델에 물음: (a) 인용 [pN]마다 그 대목이 문장을 뒷받침하는지,
             (b) baseline이 있는 {vN} 문장이 평소보다 어떻다고 말하는지(서버 방향 검사와 2×2). 같은 물음은 캐시.
make-corpus  성공한 보고서마다 eval_checks의 corpus 항목(uwbserver.eval_checks_corpus/1)과 맥락 파일을 만들고,
             오류 종류마다 변형 하나(규칙 여섯, 작은 Claude 모델 둘)를 넣은 뒤 tools/eval_checks.py로 검출률을 냄.

키는 환경에서 읽습니다(ANTHROPIC_API_KEY는 SDK가, OPENROUTER_API_KEY는 judge가). 키 값은 어디에도 쓰지 않습니다.
OpenRouter 요청에는 Authorization과 Content-Type 머리만 보냅니다(HTTP-Referer, X-Title 없음).
--fake: 보고서는 writer.DemoModel, 판정과 변형은 정해진 대역. 키가 필요 없고 비용이 없습니다(DB와 논문 색인은 씀).

종료 코드: 0 = 정상, 1 = 안전장치로 멈춤·자격 정보 없음, 2 = 입력·DB·논문 저장소를 쓸 수 없음.
"""

import argparse
import hashlib
import json
import os
import random
import re
import statistics
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

try:
    from tools import eval_checks
except ImportError:  # python tools/eval_report_runs.py로 돌리면 tools/가 sys.path[0]
    import eval_checks  # type: ignore[no-redef]

from uwbserver.report import rules

RUN_SCHEMA = "uwbserver.eval_report_run/1"
SUMMARY_SCHEMA = "uwbserver.eval_report_runs_summary/1"
JUDGE_SCHEMA = "uwbserver.eval_report_judge/1"

DEFAULT_CONTEXTS = Path("eval/checks/contexts")
DEFAULT_RUNS = Path("report/data/rag_runs")
DEFAULT_SUMMARY = Path("report/data/rag_runs_summary.json")
DEFAULT_JUDGE = Path("report/data/rag_judge.json")
DEFAULT_CORPUS = Path("eval/checks/corpus_src.json")
DEFAULT_CHECKS = Path("report/data/rag_checks.json")

MAX_PARALLEL = 2
MAX_INPUT_TOKENS_PER_RUN = 1_500_000
KST = timezone(timedelta(hours=9))

# 판정 모델(judge). 2026-11-12에 https://openrouter.ai/api/v1/models 에서 id와 지원 인자를 확인함
JUDGE_MODEL = "google/gemini-4-argon"
JUDGE_REASON = (
    "OpenRouter 모델 목록의 설명이 'Google's flagship reasoning model'이라고 밝힌 Anthropic 밖의 주력 모델. "
    "같은 급의 openai/gpt-6-astra보다 값이 1/5이고(입력 $2, 출력 $10 / 백만 토큰), 목록의 supported_parameters에 "
    "temperature와 response_format이 있어 temperature 0과 JSON 답을 요청할 수 있음."
)
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"

# 변형을 쓰는 작은 모델. 2026-11-12에 Claude 문서의 Models overview에서 확인한 가장 작은 현행 모델
MUTATOR_MODEL = "claude-haiku-5-5"
MUTATOR_EFFORT = "low"


# ================================================================ 기간과 run


PERIOD_RE = re.compile(r"^(day|week)-(\d{4}-\d{2}-\d{2})$")


def period_bounds(name: str) -> tuple[datetime, datetime]:
    """day-YYYY-MM-DD → 그날 0시–다음날 0시(+09:00), week-YYYY-MM-DD → 그날 0시부터 7일."""
    m = PERIOD_RE.match(name)
    if m is None:
        raise ValueError(f"not a period name (day-YYYY-MM-DD or week-YYYY-MM-DD): {name!r}")
    start = datetime.combine(date.fromisoformat(m.group(2)), datetime.min.time(), KST)
    return start, start + timedelta(days=1 if m.group(1) == "day" else 7)


def default_periods(contexts_dir: Path) -> list[str]:
    """맥락 폴더에서 이름이 기간인 파일(day-…/week-….json). make-corpus가 쓰는 <run id>.json은 맞지 않음."""
    return sorted(p.stem for p in contexts_dir.glob("*.json") if PERIOD_RE.match(p.stem))


def run_ids(periods: Sequence[str], repeats: int) -> list[str]:
    return [f"{p}-r{n}" for p in periods for n in range(1, repeats + 1)]


@dataclass
class Usage:
    """run 하나의 모델 사용량. 토큰은 SDK 응답의 usage를 더한 것."""

    calls: int = 0
    input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    output_tokens: int = 0

    def add(self, u: object) -> None:
        for name in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"):
            setattr(self, name, getattr(self, name) + (getattr(u, name, None) or 0))

    @property
    def input_total(self) -> int:
        """입력 토큰의 합(캐시 읽기·쓰기 포함). 안전장치가 보는 수."""
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens

    def as_dict(self) -> dict:
        return asdict(self) | {"input_total": self.input_total}


class BudgetExceeded(Exception):
    pass


class MeteredClient:
    """anthropic 클라이언트를 얇게 감쌈: beta.messages.create의 usage를 더하고, 보내기 전에 입력 토큰 합이 한도를
    넘었으면 BudgetExceeded. 이 도구는 anthropic을 import하지 않음(writer.ClaudeModel이 만든 클라이언트를 감쌈)."""

    def __init__(self, client: object, usage: Usage, max_input: int) -> None:
        self._client, self.usage, self.max_input = client, usage, max_input
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if self.usage.input_total > self.max_input:
            raise BudgetExceeded(f"input tokens of this run {self.usage.input_total:,} > {self.max_input:,}")
        r = self._client.beta.messages.create(**kwargs)
        self.usage.add(getattr(r, "usage", None))
        return r


class CountingModel:
    """writer.Model을 감싸 부른 횟수를 셈(가짜 모델에도 같은 수가 남게)."""

    def __init__(self, model, usage: Usage) -> None:
        self._model, self.usage = model, usage

    @property
    def name(self) -> str:
        return self._model.name

    def turn(self, system, tools, messages):
        self.usage.calls += 1
        return self._model.turn(system, tools, messages)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_one(run_id: str, ctx: Mapping, env: Mapping, model_factory: Callable[[Usage], object]) -> dict:
    """보고서 하나(ask.py --report와 같은 조립). 예외는 기록에 적고, 자격 정보가 없을 때만 밖으로 냄."""
    from uwbserver.report import chat, papers, writer

    period = run_id.rsplit("-r", 1)[0]
    usage = Usage()
    model = CountingModel(model_factory(usage), usage)
    c = chat.Chat(ctx, env["index"], model, papers=env["papers"], sources=env["sources"],
                  papers_version=env["papers_version"], max_searches=env["max_searches"])  # fmt: skip
    started = datetime.now(KST)
    t0 = time.monotonic()
    markdown, error = None, None
    try:
        markdown = c.report().text
    except writer.MissingCredentials:
        raise
    except chat.ReportError as e:
        error = {"type": "ReportError", "message": str(e), "problems": e.problems}
    except (chat.ChatError, writer.WriterError, BudgetExceeded, papers.PaperStoreError) as e:
        error = {"type": type(e).__name__, "message": str(e)}
    from_at, to_at = period_bounds(period)
    return {
        "schema": RUN_SCHEMA,
        "run_id": run_id,
        "period": {"name": period, "from_at": from_at.isoformat(), "to_at": to_at.isoformat()},
        "subject": env["subject"],
        "model": model.name,
        "effort": env["effort"],
        "fake": env["fake"],
        "started_at": started.isoformat(),
        "elapsed_s": round(time.monotonic() - t0, 3),
        "usage": usage.as_dict(),
        "error": error,
        "markdown": markdown,
        "trace": c.trace(),
    }


def cmd_run(args) -> int:
    import psycopg

    from uwbserver.pipeline import db, log, metrics, settings
    from uwbserver.report import chat, papers, writer

    log.setup("eval_report_runs")
    periods = args.periods or default_periods(Path(args.contexts))
    try:
        for p in periods:
            period_bounds(p)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = Path(args.out)
    ids = run_ids(periods, args.repeats)
    pending = [i for i in ids if not (out / f"{i}.json").exists()]
    print(f"runs: {len(ids)} planned, {len(ids) - len(pending)} already done, {len(pending)} to run", file=sys.stderr)
    if args.max_runs is not None:
        pending = pending[: args.max_runs]
    if not pending:
        return 0

    catalog = metrics.load()
    contexts = {}
    try:
        with db.connect("api") as conn:
            for p in dict.fromkeys(i.rsplit("-r", 1)[0] for i in pending):
                contexts[p] = chat.load_context(conn, catalog, args.subject, *period_bounds(p))
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (psycopg.Error, OSError) as e:
        print(f"error: cannot read the database: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    root = Path(args.papers_root) if args.papers_root else papers.default_root()
    try:
        env = {"index": papers.load_index(root, None), "papers": papers.read_papers(root),
               "papers_version": papers.papers_version(root)}  # fmt: skip
    except (papers.PaperStoreError, OSError) as e:
        print(f"error: paper store {root}: {e}", file=sys.stderr)
        return 2
    s = settings.get()
    env |= {"sources": rules.metric_sources(catalog), "max_searches": s.report_max_searches,
            "subject": args.subject, "effort": None if args.fake else s.report_effort, "fake": args.fake}  # fmt: skip

    def factory(usage: Usage):
        if args.fake:
            return writer.DemoModel()
        m = writer.ClaudeModel(s.report_model, s.report_effort)
        m.client = MeteredClient(m.client, usage, args.max_input_tokens_per_run)
        return m

    def job(run_id: str) -> dict:
        return run_one(run_id, contexts[run_id.rsplit("-r", 1)[0]], env, factory)

    def finish(rec: dict) -> bool:
        """기록을 쓰고 한 줄을 찍음. 더 돌려도 되면 True."""
        _write_json(out / f"{rec['run_id']}.json", rec)
        u = rec["usage"]
        status = "ok" if rec["error"] is None else rec["error"]["type"]
        print(f"{rec['run_id']}: {status}, {rec['elapsed_s']:.0f}s, calls {u['calls']}, input {u['input_tokens']:,} "
              f"+ cache read {u['cache_read_input_tokens']:,} + cache write {u['cache_creation_input_tokens']:,}, "
              f"output {u['output_tokens']:,}")  # fmt: skip
        if u["input_total"] > args.max_input_tokens_per_run:
            print(f"stop: input tokens of one run {u['input_total']:,} > --max-input-tokens-per-run "
                  f"{args.max_input_tokens_per_run:,}", file=sys.stderr)  # fmt: skip
            return False
        return True

    try:
        if not finish(job(pending[0])):
            return 1
        rest = pending[1:]
        go = True
        with ThreadPoolExecutor(max_workers=min(args.parallel, MAX_PARALLEL)) as ex:
            running: set = set()
            while (rest and go) or running:
                while rest and go and len(running) < min(args.parallel, MAX_PARALLEL):
                    running.add(ex.submit(job, rest.pop(0)))
                done, running = wait(running, return_when=FIRST_COMPLETED)
                for f in done:
                    go = finish(f.result()) and go
        return 0 if go else 1
    except writer.MissingCredentials as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


# ================================================================ 기록 읽기와 집계


def load_runs(runs_dir: Path) -> list[dict]:
    out = []
    for p in sorted(runs_dir.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if rec.get("schema") == RUN_SCHEMA:
            out.append(rec)
    return out


def report_raw(rec: Mapping) -> str | None:
    """성공한 run의 최종 원문(기록의 report 이벤트). 없으면 None."""
    if rec.get("error") is not None:
        return None
    return next((e["raw"] for e in reversed(rec["trace"]["events"]) if e.get("type") == "report"), None)


def passage_numbers(trace: Mapping) -> dict[int, str]:
    """기록의 passages {"p3": id} → {3: id}."""
    return {int(k[1:]): v for k, v in trace.get("passages", {}).items()}


def _mean(xs: Sequence[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def summarize_runs(runs: Sequence[Mapping]) -> dict:
    """run 기록 → 집계(순수 계산)."""
    errors = Counter(r["error"]["type"] for r in runs if r.get("error"))
    ok = [r for r in runs if r.get("error") is None]
    first_kinds, second_kinds = Counter(), Counter()
    flagged_first, passed_after = 0, 0
    for r in runs:
        checks = [e["problems"] for e in r["trace"]["events"] if e.get("type") == "check"]
        if not checks:
            continue
        first_kinds.update(eval_checks.problem_kind(p) for p in checks[0])
        if checks[0]:
            flagged_first += 1
            if len(checks) > 1:
                second_kinds.update(eval_checks.problem_kind(p) for p in checks[1])
                passed_after += not checks[1]
    drafted = sum(1 for r in runs if any(e.get("type") == "check" for e in r["trace"]["events"]))
    per = []
    for r in ok:
        raw = report_raw(r) or ""
        cites = [int(x) for x in rules.P_RE.findall(raw)]
        per.append({
            "values": len(rules.V_RE.findall(raw)), "citations": len(cites), "distinct_passages": len(set(cites)),
            "searches": sum(1 for e in r["trace"]["events"] if e.get("type") == "search"),
            "model_calls": r["usage"]["calls"], "input_total": r["usage"]["input_total"],
            **{k: r["usage"][k] for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                                          "output_tokens")},
            "elapsed_s": r["elapsed_s"],
        })  # fmt: skip
    return {
        "schema": SUMMARY_SCHEMA,
        "runs": len(runs),
        "succeeded": len(ok),
        "failed_checks_twice": errors.get("ReportError", 0),
        "refused": errors.get("RefusalError", 0),
        "other_errors": {k: v for k, v in errors.items() if k not in ("ReportError", "RefusalError")},
        "drafted": drafted,
        "first_draft_flagged": flagged_first,
        "first_draft_flagged_rate": flagged_first / drafted if drafted else None,
        "first_draft_problem_kinds": dict(first_kinds),
        "second_draft_problem_kinds": dict(second_kinds),
        "passed_after_rewrite_rate": passed_after / flagged_first if flagged_first else None,
        "mean_per_report": {k: _mean([p[k] for p in per]) for k in (per[0] if per else {})},
        "total_usage": {k: sum(r["usage"][k] for r in runs) for k in Usage().as_dict()},
    }


def summary_table(s: Mapping) -> str:
    def rate(x):
        return "—" if x is None else f"{x:.2f}"

    lines = [
        f"runs {s['runs']}, succeeded {s['succeeded']}, failed checks twice {s['failed_checks_twice']}, "
        f"refused {s['refused']}, other errors {sum(s['other_errors'].values())} {s['other_errors'] or ''}",
        f"first draft flagged: {s['first_draft_flagged']}/{s['drafted']} ({rate(s['first_draft_flagged_rate'])}), "
        f"passed after rewrite: {rate(s['passed_after_rewrite_rate'])}",
        f"first draft problems by kind: {s['first_draft_problem_kinds']}",
        f"second draft problems by kind: {s['second_draft_problem_kinds']}",
        "mean per final report:",
    ]
    lines += [f"  {k:<28}{v:>14,.1f}" for k, v in s["mean_per_report"].items() if v is not None]
    lines.append(f"total usage: {s['total_usage']}")
    return "\n".join(lines)


def cmd_summarize(args) -> int:
    runs = load_runs(Path(args.runs))
    s = summarize_runs(runs)
    _write_json(Path(args.out), s)
    print(summary_table(s))
    return 0


# ================================================================ 판정 (judge)


def sentences(raw: str) -> list[tuple[str, str]]:
    """(부분 이름, 문장). 문장 나누기는 서버 검사와 같은 규칙(rules의 문장 끝)."""
    parts, _ = rules.parse_parts(raw)
    out = []
    for name, text in parts.items():
        for s in rules._SENTENCE_END.split(text):  # noqa: SLF001 - 서버 검사와 같은 문장 단위를 쓰려고
            if s.strip():
                out.append((name, s.strip()))
    return out


def fill_values(sentence: str, ctx: Mapping) -> str:
    """{vN}만 값의 글로 풂. [pN]은 그대로."""
    known = rules.values_by_number(ctx)
    return rules.V_RE.sub(lambda m: rules.value_text(known[int(m.group(1))]) if int(m.group(1)) in known
                          else m.group(0), sentence)  # fmt: skip


CITATION_SYSTEM = (
    "You check whether a cited passage from a medical paper supports a sentence of a gait report written in Korean. "
    "Judge only what the sentence attributes to the cited literature, not the person's own measured values. "
    'Answer with JSON only: {"verdict": "supported" | "partial" | "not_supported", "reason": "<one short line>"}. '
    "supported: the passage states or directly implies it. partial: the passage supports only part of it or a "
    "weaker version. not_supported: the passage does not say it or is unrelated."
)
DIRECTION_SYSTEM = (
    "You read one sentence of a gait report written in Korean. {vN} marks are measured values listed below. For "
    "each listed value, decide what the sentence says about it compared with the person's usual value (the median "
    "of the previous 30 days). "
    'Answer with JSON only: {"claims": {"<id>": "higher" | "lower" | "same" | "no_claim"}, "reason": "<one short '
    'line>"}. higher / lower / same: the sentence says the value was above / below / about the same as usual. '
    "no_claim: the sentence makes no comparison of that value with the usual value (for example it only describes "
    "the value, or compares it with the literature)."
)
VERDICTS = ("supported", "partial", "not_supported")
CLAIMS = ("higher", "lower", "same", "no_claim")


def citation_items(run_id: str, raw: str, ctx: Mapping, numbers: Mapping[int, str]) -> list[dict]:
    """(a) 인용이 붙은 문장의 인용마다 한 항목."""
    out = []
    for part, s in sentences(raw):
        for n in dict.fromkeys(int(x) for x in rules.P_RE.findall(s)):
            out.append({"run_id": run_id, "part": part, "sentence": s, "filled": fill_values(s, ctx),
                        "p": n, "passage_id": numbers.get(n)})  # fmt: skip
    return out


def direction_items(run_id: str, raw: str, ctx: Mapping) -> list[dict]:
    """(b) {vN}이 있고 그 값 가운데 baseline이 있는 것이 있는 문장. values는 baseline이 있는 값만."""
    known = rules.values_by_number(ctx)
    out = []
    for part, s in sentences(raw):
        vals = []
        for n in dict.fromkeys(int(x) for x in rules.V_RE.findall(s)):
            v = known.get(n)
            if v is None or not v.get("baseline"):
                continue
            side = {"left": "left foot", "right": "right foot"}.get(v.get("side"), "")
            median = rules.format_value(v["baseline"]["median"], v["unit"])
            vals.append({"id": f"v{n}", "label": v.get("label", v["metric"]), "side": side,
                         "value": rules.value_text(v), "usual_median": median,
                         "direction": v["baseline"]["direction"]})  # fmt: skip
        if vals:
            out.append({"run_id": run_id, "part": part, "sentence": s, "values": vals,
                        "server_flagged": bool(rules.direction_problems(s, ctx))})  # fmt: skip
    return out


def citation_prompt(item: Mapping, passage_text: str) -> str:
    return json.dumps({"sentence": item["filled"], "citation": f"[p{item['p']}]", "passage": passage_text},
                      ensure_ascii=False)  # fmt: skip


def direction_prompt(item: Mapping) -> str:
    vals = [{k: v[k] for k in ("id", "label", "side", "value", "usual_median")} for v in item["values"]]
    return json.dumps({"sentence": item["sentence"], "values": vals}, ensure_ascii=False)


def parse_answer(text: str) -> dict | None:
    """모델의 JSON 답. 코드 울타리(```json)가 있으면 벗김. 읽을 수 없으면 None."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t)
    try:
        d = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return d if isinstance(d, dict) else None


def verdict_of(answer: Mapping | None) -> str:
    v = (answer or {}).get("verdict")
    return v if v in VERDICTS else "invalid"


def claims_of(answer: Mapping | None, ids: Iterable[str]) -> dict[str, str]:
    got = (answer or {}).get("claims")
    got = got if isinstance(got, dict) else {}
    return {i: got[i] if got.get(i) in CLAIMS else "invalid" for i in ids}


def compare(claim: str, direction: str) -> str:
    """판정 모델이 읽은 주장과 맥락의 방향 → match | mismatch | no_claim | invalid."""
    if claim in ("no_claim", "invalid"):
        return claim
    return "match" if claim == direction else "mismatch"


def sentence_category(outcomes: Iterable[str]) -> str:
    """문장 하나의 값별 결과 → mismatch(하나라도) | match(하나라도) | no_claim | invalid."""
    xs = set(outcomes)
    for c in ("mismatch", "match", "no_claim"):
        if c in xs:
            return c
    return "invalid"


def judge_summary(a_items: Sequence[Mapping], b_items: Sequence[Mapping]) -> dict:
    verdicts = Counter(i["verdict"] for i in a_items)
    per_value = Counter(v["outcome"] for i in b_items for v in i["values"])
    table = Counter((i["category"] == "mismatch", i["server_flagged"]) for i in b_items)
    return {
        "citations": {"n": len(a_items), **{k: verdicts.get(k, 0) for k in (*VERDICTS, "invalid", "error")}},
        "direction_values": {"n": sum(per_value.values()),
                             **{k: per_value.get(k, 0) for k in ("match", "mismatch", "no_claim", "invalid")}},
        "direction_sentences": {
            "n": len(b_items),
            "judge_mismatch_server_flagged": table[(True, True)],
            "judge_mismatch_server_passed": table[(True, False)],
            "judge_ok_server_flagged": table[(False, True)],
            "judge_ok_server_passed": table[(False, False)],
        },
    }  # fmt: skip


class OpenRouterJudge:
    """OpenRouter chat completions. 처음에 모델 목록에서 id가 있는지와 지원 인자를 확인하고, 받는 인자만 보냄
    (temperature 0, JSON 답). 머리는 Authorization과 Content-Type만."""

    def __init__(self, model: str, *, timeout_s: float = 300.0) -> None:
        import httpx

        self._httpx = httpx
        key = os.environ.get(OPENROUTER_KEY_ENV)
        if not key:
            raise RuntimeError(f"{OPENROUTER_KEY_ENV} is not set")
        self._headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self.model = model
        r = httpx.get(OPENROUTER_MODELS_URL, timeout=60)
        r.raise_for_status()
        found = next((m for m in r.json()["data"] if m["id"] == model), None)
        if found is None:
            raise RuntimeError(f"model {model!r} is not in the OpenRouter model list")
        supported = set(found.get("supported_parameters") or ())
        self.params: dict = {}
        if "temperature" in supported:
            self.params["temperature"] = 0
        if "response_format" in supported:
            self.params["response_format"] = {"type": "json_object"}
        self._client = httpx.Client(timeout=timeout_s)

    def __call__(self, system: str, user: str) -> tuple[str, dict]:
        body = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": user}], **self.params}  # fmt: skip
        for attempt in range(4):
            try:
                r = self._client.post(OPENROUTER_CHAT_URL, headers=self._headers, json=body)
            except self._httpx.TransportError as e:
                if attempt == 3:
                    raise RuntimeError(f"connection to OpenRouter failed: {type(e).__name__}") from None
                time.sleep(2**attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 3:
                    raise RuntimeError(f"OpenRouter HTTP {r.status_code}")
                time.sleep(2**attempt)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}")
            d = r.json()
            text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            return text, d.get("usage") or {}
        raise RuntimeError("unreachable")


class FakeJudge:
    """비용 없는 대역. (a)는 늘 supported, (b)는 몇 낱말로 대충 정함. 결과에 뜻은 없음(길이 도는지만 봄)."""

    model = "fake-judge"
    params: dict = {}

    def __call__(self, system: str, user: str) -> tuple[str, dict]:
        if system == CITATION_SYSTEM:
            return json.dumps({"verdict": "supported", "reason": "fake"}), {}
        d = json.loads(user)
        s = d["sentence"]
        claim = ("lower" if re.search("느렸|줄었|적었|낮았|짧았", s)
                 else "higher" if re.search("빨랐|늘었|많았|높았|길었", s) else "no_claim")  # fmt: skip
        return json.dumps({"claims": {v["id"]: claim for v in d["values"]}, "reason": "fake"}), {}


class JudgeCache:
    """(모델, 지시, 물음) → 답. jsonl 파일에 덧붙임. 같은 물음은 다시 보내지 않음."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.data: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.data[r["key"]] = r

    @staticmethod
    def key(model: str, system: str, user: str) -> str:
        return hashlib.sha256(json.dumps([model, system, user], ensure_ascii=False).encode()).hexdigest()

    def put(self, record: dict) -> None:
        with self._lock:
            self.data[record["key"]] = record
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


def passage_texts(root: Path, ids: Iterable[str]) -> dict[str, str]:
    from uwbserver.report import papers

    out: dict[str, str] = {}
    for key in sorted({i.split("#")[0] for i in ids}):
        try:
            for p in papers.read_passages(root, key):
                out[p.id] = p.text
        except papers.PaperStoreError:
            continue
    return out


def sample_text(result: Mapping, n: int, seed: int) -> str:
    """판정 결과 n건을 종류(인용 verdict, 방향 category)마다 고르게 골라 읽기 쉬운 글로."""
    groups: dict[str, list] = {}
    for i in result["citations"]:
        groups.setdefault(f"citation:{i['verdict']}", []).append(i)
    for i in result["directions"]:
        groups.setdefault(f"direction:{i['category']}", []).append(i)
    rng = random.Random(seed)
    for g in groups.values():
        rng.shuffle(g)
    picked: list[tuple[str, dict]] = []
    names = sorted(groups)
    while len(picked) < n and any(groups[g] for g in names):
        for g in names:
            if groups[g] and len(picked) < n:
                picked.append((g, groups[g].pop()))
    blocks = []
    for g, i in picked:
        if g.startswith("citation"):
            blocks.append(f"[{g}] {i['run_id']} / {i['part']}\n문장: {i['filled']}\n대목 [p{i['p']}] {i['passage_id']}:\n"
                          f"{(i.get('passage_text') or '(대목 글 없음)')[:1200]}\n이유: {i.get('reason', '')}")  # fmt: skip
        else:
            vals = "; ".join(f"{v['id']} {v['label']} {v['side']} 값 {v['value']}, 평소 {v['usual_median']}, "
                             f"맥락 {v['direction']}, 판정 {v['claim']} → {v['outcome']}" for v in i["values"])  # fmt: skip
            blocks.append(f"[{g}] {i['run_id']} / {i['part']} (서버 방향 검사: {'잡음' if i['server_flagged'] else '넘김'})\n"
                          f"문장: {i['sentence']}\n값: {vals}\n이유: {i.get('reason', '')}")  # fmt: skip
    return "\n\n".join(blocks)


def cmd_judge(args) -> int:
    runs = [r for r in load_runs(Path(args.runs)) if report_raw(r)]
    if not runs:
        print("error: no successful runs", file=sys.stderr)
        return 2
    from uwbserver.report import papers

    root = Path(args.papers_root) if args.papers_root else papers.default_root()
    a_items, b_items = [], []
    for r in runs:
        ctx = rules.fill_directions(r["trace"]["context"])
        raw = report_raw(r)
        a_items += citation_items(r["run_id"], raw, ctx, passage_numbers(r["trace"]))
        b_items += direction_items(r["run_id"], raw, ctx)
    texts = passage_texts(root, [i["passage_id"] for i in a_items if i["passage_id"]])
    try:
        judge = FakeJudge() if args.fake else OpenRouterJudge(args.judge_model)
    except Exception as e:  # noqa: BLE001 - 키 없음, 목록에 없는 모델, 연결 실패를 한 줄로
        print(f"error: judge: {e}", file=sys.stderr)
        return 1
    cache = JudgeCache(Path(args.cache))
    stats = Counter()

    def ask(system: str, user: str) -> dict:
        k = JudgeCache.key(judge.model, system, user)
        if k in cache.data:
            stats["cached"] += 1
            return cache.data[k]
        try:
            text, usage = judge(system, user)
            rec = {"key": k, "text": text, "usage": usage}
        except Exception as e:  # noqa: BLE001 - 한 물음의 실패는 그 항목의 error로
            stats["errors"] += 1
            return {"key": k, "error": str(e)}
        stats["calls"] += 1
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            stats[name] += int(usage.get(name) or 0)
        cache.put(rec)
        return rec

    def do_a(i: dict) -> None:
        i["passage_text"] = texts.get(i["passage_id"] or "")
        if i["passage_text"] is None:
            i |= {"verdict": "error", "reason": "passage text not found in the paper store"}
            return
        rec = ask(CITATION_SYSTEM, citation_prompt(i, i["passage_text"]))
        ans = parse_answer(rec.get("text", "")) if "error" not in rec else None
        i |= {"verdict": "error" if "error" in rec else verdict_of(ans),
              "reason": rec.get("error") or (ans or {}).get("reason", rec.get("text", "")[:300])}  # fmt: skip

    def do_b(i: dict) -> None:
        rec = ask(DIRECTION_SYSTEM, direction_prompt(i))
        ans = parse_answer(rec.get("text", "")) if "error" not in rec else None
        claims = claims_of(ans, [v["id"] for v in i["values"]])
        for v in i["values"]:
            v["claim"] = claims[v["id"]]
            v["outcome"] = compare(v["claim"], v["direction"])
        i["category"] = sentence_category(v["outcome"] for v in i["values"])
        i["reason"] = rec.get("error") or (ans or {}).get("reason", rec.get("text", "")[:300])

    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
        list(ex.map(do_a, a_items))
        list(ex.map(do_b, b_items))
    result = {
        "schema": JUDGE_SCHEMA,
        "judge_model": judge.model,
        "judge_reason": JUDGE_REASON if not args.fake else "fake judge (no API call)",
        "judge_params": judge.params,
        "runs": [r["run_id"] for r in runs],
        "calls": dict(stats),
        "summary": judge_summary(a_items, b_items),
        "citations": a_items,
        "directions": b_items,
    }
    _write_json(Path(args.out), result)
    s = result["summary"]
    print(f"judge {judge.model}: calls {stats['calls']}, cached {stats['cached']}, errors {stats['errors']}, "
          f"tokens in {stats['prompt_tokens']:,} out {stats['completion_tokens']:,}")  # fmt: skip
    print(f"citations: {s['citations']}")
    print(f"direction values: {s['direction_values']}")
    print(f"direction sentences (judge mismatch × server flagged): {s['direction_sentences']}")
    if args.sample:
        print()
        print(sample_text(result, args.sample, args.seed))
    return 0


# ================================================================ corpus 만들기 (make-corpus)

_BOUND = re.compile(r"(?<=[.!?])\s+|\n+")


def sentence_span(text: str, start: int, end: int) -> tuple[int, int]:
    """[start, end)를 품은 문장의 범위(서버 검사의 문장 끝과 같은 규칙)."""
    lo = 0
    for m in _BOUND.finditer(text):
        if m.end() <= start:
            lo = m.end()
        elif m.start() >= end:
            return lo, m.start()
    return lo, len(text)


def locate(text: str, start: int, end: int, new: str) -> tuple[str, str]:
    """text[start:end]를 new로 바꾸는 (find, replace). find는 그 자리를 품은 문장이고, 글에 여러 번 나오면 한 번만
    나올 때까지 양쪽으로 한 글자씩 넓힘."""
    lo, hi = sentence_span(text, start, end)
    while text.count(text[lo:hi]) != 1:
        if lo == 0 and hi == len(text):
            raise ValueError("cannot make a unique 'find'")
        lo, hi = max(0, lo - 1), min(len(text), hi + 1)
    return text[lo:hi], text[lo:start] + new + text[end:hi]


def placeholders(s: str) -> tuple[list[str], list[str]]:
    return sorted(rules.V_RE.findall(s)), sorted(rules.P_RE.findall(s))


_NUMBER_PREFIX = re.compile(r"^-?\d[\d,]*(?:\.\d+)?")


def rule_mutations(
    raw: str, ctx: Mapping, numbers: Mapping[int, str], rng: random.Random
) -> tuple[list[dict], list[str]]:
    """규칙으로 만드는 변형 여섯(순수 계산). (변형 목록, 건너뛴 이유)."""
    known = rules.values_by_number(ctx)
    muts, skipped = [], []

    def add(fault: str, start: int, end: int, new: str, note: str) -> None:
        try:
            find, replace = locate(raw, start, end, new)
        except ValueError as e:
            skipped.append(f"{fault}: {e}")
            return
        muts.append({"fault": fault, "find": find, "replace": replace, "note": note})

    cites = list(rules.P_RE.finditer(raw))
    vals = list(rules.V_RE.finditer(raw))
    # citation_not_retrieved: 인용 하나를 이 대화에서 찾지 않은 번호로
    if cites:
        m = rng.choice(cites)
        n = max([*numbers, 0]) + 1
        add("citation_not_retrieved", m.start(), m.end(), f"[p{n}]", f"[p{m.group(1)}] → [p{n}] (not retrieved)")
    else:
        skipped.append("citation_not_retrieved: no citation")
    # unsupported_citation: 인용 하나를 이 대화에서 찾은 다른 대목 번호로
    if cites:
        m = rng.choice(cites)
        others = sorted(set(numbers) - {int(m.group(1))})
        if others:
            n = rng.choice(others)
            add("unsupported_citation", m.start(), m.end(), f"[p{n}]",
                f"[p{m.group(1)}] → [p{n}] (retrieved, other passage)")  # fmt: skip
        else:
            skipped.append("unsupported_citation: no other retrieved passage")
    else:
        skipped.append("unsupported_citation: no citation")
    # value_id_unknown: {vN} 하나를 맥락에 없는 번호로
    if vals:
        m = rng.choice(vals)
        n = max([*known, 0]) + 1
        add("value_id_unknown", m.start(), m.end(), f"{{v{n}}}", f"{{v{m.group(1)}}} → {{v{n}}}")
    else:
        skipped.append("value_id_unknown: no value placeholder")
    # number_with_unit / number_without_unit: 단위가 붙는 값(개수가 아닌 값) 하나의 자리 표시를 숫자로
    unit_vals = [m for m in vals if int(m.group(1)) in known and known[int(m.group(1))]["value"] is not None
                 and known[int(m.group(1))]["unit"] != "count"]  # fmt: skip
    if unit_vals:
        m = rng.choice(unit_vals)
        text = rules.value_text(known[int(m.group(1))])
        add("number_with_unit", m.start(), m.end(), text, f"{{v{m.group(1)}}} → '{text}'")
        bare = _NUMBER_PREFIX.match(text)
        if bare and bare.group(0) != text:
            add("number_without_unit", m.start(), m.end(), bare.group(0), f"{{v{m.group(1)}}} → '{bare.group(0)}'")
        else:
            skipped.append(f"number_without_unit: cannot strip the unit from '{text}'")
    else:
        skipped.append("number_with_unit, number_without_unit: no placeholder of a value with a unit")
    # summary_detail_value: 요약 끝에 detail 값을 쓰는 문장 하나
    parts, _ = rules.parse_parts(raw)
    detail = [v for v in ctx["values"] if v.get("show") == "detail" and v["value"] is not None]
    summary = parts.get(rules.SUMMARY, "")
    pos = raw.find(summary) if summary else -1
    if detail and pos >= 0:
        v = rng.choice(detail)
        end = pos + len(summary)
        add("summary_detail_value", end, end, f" 참고로 {v.get('label', v['metric'])} 값은 {{{v['id']}}}였습니다.",
            f"summary + detail value {{{v['id']}}}")  # fmt: skip
    else:
        skipped.append("summary_detail_value: no detail value or no summary")
    return muts, skipped


DIRECTION_INSTRUCTION = (
    "아래는 보행 보고서의 한 문장입니다. 이 문장이 측정값을 착용자의 평소 값(지난 30일)과 견주어 말한 방향만 반대로 "
    "바꾸어 다시 쓰세요. 나머지 내용은 그대로 두고, {v숫자}와 [p숫자] 표시는 하나도 빼거나 바꾸지 마세요. 다시 쓴 "
    "문장 하나만 출력하세요."
)
OVERCLAIM_INSTRUCTION = (
    "아래는 보행 보고서의 한 문장입니다. 이 문장을 진단, 위험도, 또는 치료·운동 권고를 단정하는 말로 바꾸어 다시 "
    "쓰세요. {v숫자}와 [p숫자] 표시는 하나도 빼거나 바꾸지 마세요. 다시 쓴 문장 하나만 출력하세요."
)


class ClaudeMutator:
    """작은 Claude 모델로 문장 하나를 다시 씀. 문장과 지시만 줌(서버 검사 규칙이나 낱말 목록은 주지 않음)."""

    def __init__(self, model: str = MUTATOR_MODEL) -> None:
        from uwbserver.report import writer

        self.usage = Usage()
        self._m = writer.ClaudeModel(model, MUTATOR_EFFORT, max_tokens=4000)
        self._m.client = MeteredClient(self._m.client, self.usage, 10**9)
        self.model = model

    def __call__(self, instruction: str, sentence: str) -> str:
        self.usage.calls += 1
        t = self._m.turn("당신은 문장을 지시대로 고쳐 쓰는 편집자입니다.", [],
                         [{"role": "user", "content": f"{instruction}\n\n문장: {sentence}"}])  # fmt: skip
        return t.text.strip()


class FakeMutator:
    model = "fake-mutator"

    def __init__(self) -> None:
        self.usage = Usage()

    def __call__(self, instruction: str, sentence: str) -> str:
        self.usage.calls += 1
        tag = "가짜 방향 변형" if instruction == DIRECTION_INSTRUCTION else "가짜 과장 변형"
        return f"{sentence} ({tag})"


def model_mutation(raw: str, fault: str, sentence: str, rewritten: str) -> dict:
    """모델이 다시 쓴 문장 → 변형. 자리 표시가 그대로이고 글이 바뀌었어야 함(아니면 ValueError)."""
    if not rewritten or rewritten == sentence:
        raise ValueError("the rewrite is empty or unchanged")
    if placeholders(rewritten) != placeholders(sentence):
        raise ValueError(f"the rewrite changed placeholders: {rewritten!r}")
    start = raw.find(sentence)
    if start < 0:
        raise ValueError("sentence not found in the text")
    find, replace = locate(raw, start, start + len(sentence), rewritten)
    return {"fault": fault, "find": find, "replace": replace}


def cmd_make_corpus(args) -> int:
    runs = [r for r in load_runs(Path(args.runs)) if report_raw(r)]
    if not runs:
        print("error: no successful runs", file=sys.stderr)
        return 2
    judged: dict[str, list[dict]] = {}
    jpath = Path(args.judge)
    if jpath.exists():
        for i in json.loads(jpath.read_text(encoding="utf-8")).get("directions", []):
            if i.get("category") == "match":
                judged.setdefault(i["run_id"], []).append(i)
    else:
        print(f"note: {jpath} not found; direction_reversed is skipped (run judge first)", file=sys.stderr)
    try:
        mutator = FakeMutator() if args.fake else ClaudeMutator(args.mutator_model)
    except Exception as e:  # noqa: BLE001
        print(f"error: mutator: {e}", file=sys.stderr)
        return 1
    contexts_out = Path(args.contexts_out)
    items, report = [], []
    for r in runs:
        rid = r["run_id"]
        raw = report_raw(r)
        ctx = rules.fill_directions(r["trace"]["context"])
        numbers = passage_numbers(r["trace"])
        rng = random.Random(f"{args.seed}:{rid}")
        muts, skipped = rule_mutations(raw, ctx, numbers, rng)
        # 모델로 만드는 둘
        overall = [s for part, s in sentences(raw) if part == rules.OVERALL]
        for fault, pool, instruction in (
            ("direction_reversed", [i["sentence"] for i in judged.get(rid, [])], DIRECTION_INSTRUCTION),
            ("overclaim_wording", overall, OVERCLAIM_INSTRUCTION),
        ):
            if not pool:
                skipped.append(f"{fault}: no candidate sentence")
                continue
            sentence = rng.choice(pool)
            try:
                rewritten = mutator(instruction, sentence)
                m = model_mutation(raw, fault, sentence, rewritten)
            except Exception as e:  # noqa: BLE001 - 모델 실패와 규칙 위반을 건너뛴 이유로
                skipped.append(f"{fault}: {e}")
                continue
            muts.append(m | {"note": f"rewritten by {mutator.model}"})
        for m in muts:  # find 한 번 규칙
            eval_checks.apply_mutation(raw, m["find"], m["replace"])
        _write_json(contexts_out / f"{rid}.json", r["trace"]["context"])
        passages = {str(n): pid for n, pid in sorted(numbers.items())}
        items.append({"id": rid, "context": f"{rid}.json", "passages": passages, "text": raw, "mutations": muts})
        report.append((rid, len(muts), skipped))
    corpus = {"schema": eval_checks.CORPUS_SCHEMA,
              "generated": {"seed": args.seed, "mutator_model": mutator.model, "judge": str(jpath),
                            "mutator_usage": mutator.usage.as_dict(), "at": datetime.now(KST).isoformat()},
              "items": items}  # fmt: skip
    _write_json(Path(args.corpus_out), corpus)
    for rid, n, skipped in report:
        print(f"{rid}: {n} mutations" + "".join(f"\n  skipped {s}" for s in skipped))
    print(f"mutator {mutator.model}: {mutator.usage.as_dict()}")
    if args.no_eval:
        return 0
    print()
    return eval_checks.main(["--corpus", str(args.corpus_out), "--contexts", str(contexts_out),
                             "--out", str(args.checks_out)])  # fmt: skip


# ================================================================ 명령줄


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="write reports with the real path (or --fake)")
    r.add_argument("--subject", type=int, default=1)
    r.add_argument("--contexts", default=str(DEFAULT_CONTEXTS), help="period names come from the file names here")
    r.add_argument("--periods", nargs="*", help="period names instead (day-YYYY-MM-DD, week-YYYY-MM-DD)")
    r.add_argument("--repeats", type=int, default=2)
    r.add_argument("--out", default=str(DEFAULT_RUNS))
    r.add_argument("--papers-root", help="paper store root (default: DATA_ROOT/papers)")
    r.add_argument("--max-runs", type=int, help="run at most this many new runs")
    r.add_argument("--max-input-tokens-per-run", type=int, default=MAX_INPUT_TOKENS_PER_RUN)
    r.add_argument("--parallel", type=int, default=1, help=f"runs at once after the first (at most {MAX_PARALLEL})")
    r.add_argument("--fake", action="store_true", help="writer.DemoModel instead of the real model")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("summarize", help="aggregate run records")
    s.add_argument("--runs", default=str(DEFAULT_RUNS))
    s.add_argument("--out", default=str(DEFAULT_SUMMARY))
    s.set_defaults(func=cmd_summarize)

    j = sub.add_parser("judge", help="judge citations and direction statements with an OpenRouter model")
    j.add_argument("--runs", default=str(DEFAULT_RUNS))
    j.add_argument("--out", default=str(DEFAULT_JUDGE))
    j.add_argument("--cache", help="answer cache (jsonl). default: <out without .json>_cache.jsonl")
    j.add_argument("--papers-root", help="paper store root (default: DATA_ROOT/papers)")
    j.add_argument("--judge-model", default=JUDGE_MODEL)
    j.add_argument("--parallel", type=int, default=4)
    j.add_argument("--sample", type=int, default=0, help="print N judged items, spread over the result kinds")
    j.add_argument("--seed", type=int, default=1)
    j.add_argument("--fake", action="store_true", help="scripted judge, no API call")
    j.set_defaults(func=cmd_judge)

    c = sub.add_parser("make-corpus", help="build the eval_checks corpus from successful runs and evaluate it")
    c.add_argument("--runs", default=str(DEFAULT_RUNS))
    c.add_argument("--judge", default=str(DEFAULT_JUDGE), help="judge result (direction_reversed picks from it)")
    c.add_argument("--corpus-out", default=str(DEFAULT_CORPUS))
    c.add_argument("--contexts-out", default=str(DEFAULT_CONTEXTS))
    c.add_argument("--checks-out", default=str(DEFAULT_CHECKS))
    c.add_argument("--mutator-model", default=MUTATOR_MODEL)
    c.add_argument("--seed", type=int, default=1)
    c.add_argument("--no-eval", action="store_true", help="only write the corpus")
    c.add_argument("--fake", action="store_true", help="scripted rewrites, no API call")
    c.set_defaults(func=cmd_make_corpus)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "judge" and args.cache is None:
        out = Path(args.out)
        args.cache = str(out.with_name(out.stem + "_cache.jsonl"))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
