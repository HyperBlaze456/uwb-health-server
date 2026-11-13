"""보고서 서버 검사(SPEC RP-8)의 검출률을 잽니다. 모델은 부르지 않고, 사람이 쓴 글과 그 글에 오류 하나씩을 넣은 글을 검사에
돌립니다.

    uv run python tools/eval_checks.py --corpus eval/checks/corpus_src.json --contexts eval/checks/contexts \\
        --out report/data/rag_checks.json

입력(--corpus, 형식 uwbserver.eval_checks_corpus/1): items[]마다
- id, context: --contexts 안의 맥락 파일 이름(uwbserver.report_input/1). baseline.direction이 없으면
  rules.fill_directions로 채움
- passages: 그 글을 쓸 때 찾은 대목 번호 → 대목 id. 검사에는 번호의 집합만 씀
- text: 오류 없이 쓰려고 한 글(clean). 네 제목(## 요약 …)으로 나뉨
- mutations[]: {fault, find, replace, note}. clean에서 find가 정확히 한 번 나와야 하고, 아니면 오류로 적고 건너뜀

하는 일: 글마다 rules.parse_parts + rules.check_report(방향 검사 포함).
- clean: 문제가 하나라도 나온 글의 수와 그 문제 줄 전부(진짜 위반인지 잘못 잡음인지 사람이 가림)
- 오류 글: fault마다 n과 네 비율
  any       문제가 하나라도 나옴
  kind      그 fault에 맞는 종류(FAULT_KINDS)의 문제가 나옴
  new_any   clean에서 이미 나오던 문제 줄을 뺀 새 문제가 나옴
  new_kind  새 문제 가운데 맞는 종류가 있음
문제 줄을 종류로 묶는 규칙은 PROBLEM_KINDS 한 곳입니다. 같은 문장의 문제는 문장 글이 줄에 들어가므로(방향 검사), 오류를
넣은 문장에 원래 있던 문제는 새 문제로 셉니다.

종료 코드: 0 = 정상(건너뛴 항목이 있어도), 2 = 입력을 읽을 수 없음.
"""

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from uwbserver.report import rules

CORPUS_SCHEMA = "uwbserver.eval_checks_corpus/1"
RESULT_SCHEMA = "uwbserver.eval_checks_result/1"

# 문제 줄 → 종류. 위에서부터 처음 맞는 것. 문구는 rules.parse_parts / check_text / check_report가 내는 것
PROBLEM_KINDS: tuple[tuple[str, re.Pattern], ...] = (
    ("structure", re.compile(r"정해진 부분이 아닌 제목|^부분 '")),
    ("value_id_unknown", re.compile(r"맥락에 없는 값 번호")),
    ("citation_not_retrieved", re.compile(r"search_papers가 돌려주지 않은 대목")),
    ("measured_number", re.compile(r"측정값을 숫자로 직접 씀")),
    ("direction", re.compile(r"평소와의 방향이 맥락과 반대")),
    ("summary_detail_value", re.compile(r"show = basic이 아닌 값")),
)
OTHER = "other"

# fault → 그 fault를 잡았다고 보는 문제 종류. 빈 집합은 그 fault를 맡는 검사가 없다는 뜻(kind 비율은 늘 0)
FAULT_KINDS: dict[str, frozenset[str]] = {
    "citation_not_retrieved": frozenset({"citation_not_retrieved"}),
    "value_id_unknown": frozenset({"value_id_unknown"}),
    "number_with_unit": frozenset({"measured_number"}),
    "number_without_unit": frozenset({"measured_number"}),
    "direction_reversed": frozenset({"direction"}),
    "summary_detail_value": frozenset({"summary_detail_value"}),
    "unsupported_citation": frozenset(),
    "overclaim_wording": frozenset(),
}


class CorpusError(Exception):
    pass


def problem_kind(line: str) -> str:
    for kind, pattern in PROBLEM_KINDS:
        if pattern.search(line):
            return kind
    return OTHER


def check(text: str, context: Mapping, passages: Mapping[int, rules.PassageRef]) -> list[str]:
    """서버가 보고서 글에 하는 검사 전부(chat.Chat._check_report와 같음)."""
    parts, problems = rules.parse_parts(text)
    return problems + rules.check_report(parts, context, passages)


def passage_refs(passages: Mapping[str, str]) -> dict[int, rules.PassageRef]:
    """번호 → 대목 id에서 PassageRef를 최소한으로 채움. 검사는 번호만 봄."""
    out = {}
    for n, pid in passages.items():
        out[int(n)] = rules.PassageRef(int(n), pid, pid.split("#")[0], None, "", "", (), None, None)
    return out


def apply_mutation(text: str, find: str, replace: str) -> str:
    """find가 정확히 한 번 나오면 바꾼 글. 아니면 ValueError."""
    count = text.count(find) if find else 0
    if count != 1:
        raise ValueError(f"'find' occurs {count} times in the clean text (must be exactly once)")
    return text.replace(find, replace, 1)


def new_problems(clean: Sequence[str], mutated: Sequence[str]) -> list[str]:
    """mutated에서 clean에 이미 있던 줄을 (개수까지) 뺀 것. 순서는 mutated의 순서."""
    left = Counter(clean)
    out = []
    for p in mutated:
        if left[p] > 0:
            left[p] -= 1
        else:
            out.append(p)
    return out


def outcome(fault: str, clean: Sequence[str], mutated: Sequence[str]) -> dict:
    kinds = FAULT_KINDS[fault]
    new = new_problems(clean, mutated)
    return {
        "any": bool(mutated),
        "kind": any(problem_kind(p) in kinds for p in mutated),
        "new_any": bool(new),
        "new_kind": any(problem_kind(p) in kinds for p in new),
        "new_problems": new,
    }


RATES = ("any", "kind", "new_any", "new_kind")


def summarize(results: Sequence[Mapping]) -> dict[str, dict]:
    """오류 글의 결과 → fault마다 {n, any, kind, new_any, new_kind(비율), 각 수}. FAULT_KINDS 순서, n = 0인 fault도 냄."""
    out = {}
    for fault in FAULT_KINDS:
        rs = [r for r in results if r["fault"] == fault]
        d: dict = {"n": len(rs), "matching_kinds": sorted(FAULT_KINDS[fault])}
        for key in RATES:
            hit = sum(1 for r in rs if r[key])
            d[f"{key}_count"] = hit
            d[key] = hit / len(rs) if rs else None
        out[fault] = d
    return out


def evaluate(corpus: Mapping, contexts: Mapping[str, Mapping]) -> dict:
    """순수 계산. contexts: 파일 이름 → 맥락(direction을 채운 것)."""
    clean_out, mutated_out, errors = [], [], []
    for item in corpus["items"]:
        iid = item.get("id", "?")
        ctx = contexts.get(item.get("context"))
        if ctx is None:
            errors.append({"item": iid, "error": f"context {item.get('context')!r} not found"})
            continue
        if not isinstance(item.get("text"), str):
            errors.append({"item": iid, "error": "no 'text'"})
            continue
        passages = passage_refs(item.get("passages", {}))
        clean = check(item["text"], ctx, passages)
        clean_out.append({"id": iid, "problems": clean})
        for i, m in enumerate(item.get("mutations", [])):
            where = {"item": iid, "mutation": i, "fault": m.get("fault")}
            if m.get("fault") not in FAULT_KINDS:
                errors.append({**where, "error": f"unknown fault {m.get('fault')!r}"})
                continue
            try:
                text = apply_mutation(item["text"], m["find"], m["replace"])
            except (KeyError, ValueError) as e:
                errors.append({**where, "error": str(e)})
                continue
            problems = check(text, ctx, passages)
            mutated_out.append({**where, "note": m.get("note", ""), "problems": problems,
                                **outcome(m["fault"], clean, problems)})  # fmt: skip
    flagged = [c for c in clean_out if c["problems"]]
    return {
        "schema": RESULT_SCHEMA,
        "problem_kinds": {kind: p.pattern for kind, p in PROBLEM_KINDS},
        "fault_kinds": {f: sorted(k) for f, k in FAULT_KINDS.items()},
        "clean": {"n": len(clean_out), "flagged": len(flagged), "flagged_items": flagged},
        "faults": summarize(mutated_out),
        "mutations": mutated_out,
        "errors": errors,
    }


def table(result: Mapping) -> str:
    c = result["clean"]
    lines = [f"clean texts: {c['flagged']}/{c['n']} with at least one problem"]
    for item in c["flagged_items"]:
        lines.append(f"  {item['id']}")
        lines += [f"    - {p}" for p in item["problems"]]
    lines.append("")
    head = f"{'fault':<24}{'n':>4}" + "".join(f"{k:>10}" for k in RATES)
    lines += [head, "-" * len(head)]
    for fault, d in result["faults"].items():
        cells = "".join(f"{'—':>10}" if d[k] is None else f"{d[k]:>10.2f}" for k in RATES)
        lines.append(f"{fault:<24}{d['n']:>4}{cells}")
    if result["errors"]:
        lines.append("")
        lines.append(f"skipped: {len(result['errors'])}")
        lines += [f"  - {json.dumps(e, ensure_ascii=False)}" for e in result["errors"]]
    return "\n".join(lines)


def load(corpus_path: Path, contexts_dir: Path) -> tuple[dict, dict[str, dict]]:
    try:
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise CorpusError(f"cannot read corpus {corpus_path}: {e}") from e
    if corpus.get("schema") != CORPUS_SCHEMA:
        raise CorpusError(f"corpus schema is {corpus.get('schema')!r}, expected {CORPUS_SCHEMA!r}")
    contexts = {}
    for name in sorted({item.get("context") for item in corpus.get("items", [])} - {None}):
        path = contexts_dir / name
        if not path.is_file():
            continue  # evaluate가 항목마다 오류로 적음
        try:
            contexts[name] = rules.fill_directions(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError) as e:
            raise CorpusError(f"cannot read context {path}: {e}") from e
    return corpus, contexts


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", required=True, help="corpus JSON (uwbserver.eval_checks_corpus/1)")
    p.add_argument("--contexts", required=True, help="directory of context JSON files")
    p.add_argument("--out", required=True, help="result JSON path")
    args = p.parse_args(argv)
    try:
        corpus, contexts = load(Path(args.corpus), Path(args.contexts))
    except CorpusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    result = evaluate(corpus, contexts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
