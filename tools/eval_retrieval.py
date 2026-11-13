"""논문 찾기의 정량 평가 (SPEC RP-6). DB를 쓰지 않습니다.

    set -a; source .env; set +a      # OPENROUTER_API_KEY
    uv run --group embed python tools/eval_retrieval.py --papers-root data/papers \\
        --queries eval/retrieval/queries.toml --out report/data/rag_retrieval.json

질의 묶음(queries.toml)의 질문마다 모든 논문에서 대목 10개(SEARCH_K_MAX)를 찾고, 정답 논문의 대목이 처음 나온
순위로 Hit@1, Hit@5, Hit@10, MRR@10을 묶음별과 전체로 냅니다. 논문 단위입니다: 상위 k개 대목 가운데 정답 논문의
대목이 하나라도 있으면 적중입니다.

방식: bm25, vector·hybrid(글자 n-gram 대역, 참고), 그리고 --embedder마다 vector·hybrid. 기본 --embedder는
openrouter:openai/text-embedding-3-large(키 OPENROUTER_API_KEY 필요)와 fastembed:BAAI/bge-small-en-v1.5(참고, 선택 의존성
묶음 embed 필요)입니다. 키나 패키지가 없으면 그 두 방식은 건너뛰고 이유를 출력합니다.
색인은 방식마다 메모리에서 만들고 디스크의 색인(<root>/index)은 읽지도 쓰지도 않습니다. API로 센 대목·질문의 벡터는
--cache-dir(기본 report/data/cache)에 남겨 다시 돌릴 때 보내지 않습니다.

종료 코드: 0 = 정상, 2 = 인자 오류나 저장소·질의 묶음을 쓸 수 없음.
"""

import argparse
import hashlib
import json
import sys
import time
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

from uwbserver.report import papers

FORMAT = "uwbserver.rag_retrieval/1"
K = 10  # writer.SEARCH_K_MAX와 같은 값. 평가가 writer를 import하지 않도록 따로 둡니다.
CUTOFFS = (1, 5, 10)
DEFAULT_EMBEDDERS = ["openrouter:openai/text-embedding-3-large", "fastembed:BAAI/bge-small-en-v1.5"]
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "report" / "data" / "cache"


# ================================================================ 순수 계산


def first_relevant_rank(ranked_keys: Sequence[str], relevant: Sequence[str]) -> int | None:
    """찾은 대목들의 논문 key(순위 순)에서 정답 논문이 처음 나온 순위(1부터). 없으면 None."""
    wanted = set(relevant)
    for r, key in enumerate(ranked_keys, 1):
        if key in wanted:
            return r
    return None


def hit(rank: int | None, k: int) -> bool:
    return rank is not None and rank <= k


def reciprocal_rank(rank: int | None, k: int = K) -> float:
    """MRR@k의 한 항: 순위가 k 안이면 1/순위, 아니면 0."""
    return 1.0 / rank if hit(rank, k) else 0.0


def summarize(ranks: Sequence[int | None], cutoffs: Sequence[int] = CUTOFFS, k: int = K) -> dict:
    """질의들의 첫 정답 순위 → {n, hit@c…, mrr@k}. 질의가 없으면 비율은 None."""
    n = len(ranks)
    out: dict = {"n": n}
    for c in cutoffs:
        out[f"hit@{c}"] = sum(hit(r, c) for r in ranks) / n if n else None
    out[f"mrr@{k}"] = sum(reciprocal_rank(r, k) for r in ranks) / n if n else None
    return out


def disagreements(ranks_a: Sequence[int | None], ranks_b: Sequence[int | None], k: int) -> dict:
    """두 방식을 같은 질의 순서로 견줌: A는 상위 k에 맞고 B는 틀린 질의 수(a_only)와 그 반대(b_only)."""
    if len(ranks_a) != len(ranks_b):
        raise ValueError("rank lists differ in length")
    a_only = sum(hit(a, k) and not hit(b, k) for a, b in zip(ranks_a, ranks_b, strict=True))
    b_only = sum(hit(b, k) and not hit(a, k) for a, b in zip(ranks_a, ranks_b, strict=True))
    return {"k": k, "a_only": a_only, "b_only": b_only}


# ================================================================ 질의 묶음과 실행


@dataclass(frozen=True, slots=True)
class Query:
    id: str
    group: str
    query: str
    relevant: tuple[str, ...]


def read_queries(path: Path, known: Sequence[str]) -> list[Query]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    out: list[Query] = []
    seen: set[str] = set()
    for q in data.get("query", []):
        if q["id"] in seen:
            raise papers.PaperStoreError(f"duplicate query id {q['id']!r}")
        seen.add(q["id"])
        relevant = tuple(q["relevant"])
        unknown = [k for k in relevant if k not in known]
        if not relevant or unknown:
            raise papers.PaperStoreError(f"{q['id']}: relevant keys missing or not in the paper list: {unknown}")
        out.append(Query(q["id"], q["group"], q["query"], relevant))
    if not out:
        raise papers.PaperStoreError(f"no [[query]] entries in {path}")
    return out


@dataclass(frozen=True, slots=True)
class Method:
    id: str
    method: str  # papers.METHODS
    index: papers.Index


def run_method(m: Method, queries: Sequence[Query]) -> list[dict]:
    """질의마다 {rank, top: [대목 id…]}."""
    out = []
    for q in queries:
        hits = m.index.search(q.query, K, method=m.method)
        out.append({
            "rank": first_relevant_rank([h.passage.key for h in hits], q.relevant),
            "top": [h.passage.id for h in hits],
        })  # fmt: skip
    return out


def build_methods(root: Path, embedders: Sequence[str], cache_dir: Path | None) -> tuple[list[Method], list[dict]]:
    """평가할 방식들과 건너뛴 방식(이유). bm25와 n-gram 대역은 늘, 그 뒤로 embedders마다 vector와 hybrid."""
    skipped: list[dict] = []
    t = time.monotonic()
    ngram = papers.index_in_memory(root, papers.NgramEmbedder())
    print(f"index {ngram.embedder_name}: {len(ngram.passages)} passages, {time.monotonic() - t:.1f} s", file=sys.stderr)
    methods = [
        Method("bm25", "bm25", ngram),
        Method("vector:ngram", "vector", ngram),
        Method("hybrid:ngram", "hybrid", ngram),
    ]
    for name in embedders:
        try:
            t = time.monotonic()
            index = papers.index_in_memory(root, papers.embedder_by_name(name, cache_dir=cache_dir))
            print(f"index {index.embedder_name}: {time.monotonic() - t:.1f} s", file=sys.stderr)
        except papers.PaperStoreError as e:
            reason = str(e)
            print(f"skipped vector:{name} and hybrid:{name}: {reason}", file=sys.stderr)
            skipped += [{"id": f"vector:{name}", "reason": reason}, {"id": f"hybrid:{name}", "reason": reason}]
            continue
        methods += [Method(f"vector:{name}", "vector", index), Method(f"hybrid:{name}", "hybrid", index)]
    return methods, skipped


def evaluate(queries: list[Query], methods: list[Method]) -> dict:
    groups = list(dict.fromkeys(q.group for q in queries))
    results = {}
    for m in methods:
        t = time.monotonic()
        results[m.id] = run_method(m, queries)
        print(f"ran {m.id}: {time.monotonic() - t:.1f} s", file=sys.stderr)

    def ranks(mid: str, group: str | None) -> list[int | None]:
        return [r["rank"] for q, r in zip(queries, results[mid], strict=True) if group is None or q.group == group]

    summary = {
        m.id: {**{g: summarize(ranks(m.id, g)) for g in groups}, "all": summarize(ranks(m.id, None))} for m in methods
    }
    pairs = []
    for a, b in combinations([m.id for m in methods], 2):
        for g in [*groups, None]:
            for c in CUTOFFS:
                pairs.append({"a": a, "b": b, "group": g or "all", **disagreements(ranks(a, g), ranks(b, g), c)})
    per_query = [
        {"id": q.id, "group": q.group, "query": q.query, "relevant": list(q.relevant),
         "rank": {m.id: results[m.id][i]["rank"] for m in methods},
         "top": {m.id: results[m.id][i]["top"] for m in methods}}
        for i, q in enumerate(queries)
    ]  # fmt: skip
    return {"summary": summary, "pairwise": pairs, "per_query": per_query}


def print_table(summary: dict, groups: list[str]) -> None:
    print(f"{'method':42} {'group':10} {'n':>3} {'Hit@1':>6} {'Hit@5':>6} {'Hit@10':>6} {'MRR@10':>6}")
    for mid, by_group in summary.items():
        for g in [*groups, "all"]:
            s = by_group[g]
            print(f"{mid:42} {g:10} {s['n']:>3} {s['hit@1']:>6.3f} {s['hit@5']:>6.3f} {s['hit@10']:>6.3f} "
                  f"{s['mrr@10']:>6.3f}")  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--papers-root", required=True)
    p.add_argument("--queries", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--embedder", action="append", help=f"meaning-aware embedder name, repeatable "
                   f"(default: {', '.join(DEFAULT_EMBEDDERS)})")  # fmt: skip
    p.add_argument("--no-embedder", action="store_true", help="evaluate only bm25 and the ngram stand-in")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE), help="vector cache for API embedders")
    a = p.parse_args(argv)
    root, qpath = Path(a.papers_root), Path(a.queries)
    try:
        listed = papers.read_papers(root)
        if not listed:
            raise papers.PaperStoreError(f"no papers in {root}")
        queries = read_queries(qpath, list(listed))
        names = [] if a.no_embedder else (a.embedder or DEFAULT_EMBEDDERS)
        methods, skipped = build_methods(root, names, Path(a.cache_dir))
        result = evaluate(queries, methods)
    except (papers.PaperStoreError, OSError, tomllib.TOMLDecodeError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    groups = list(dict.fromkeys(q.group for q in queries))
    doc = {
        "format": FORMAT,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "papers_version": papers.papers_version(root),
        "passages": len(methods[0].index.passages),
        "queries_file": str(qpath),
        "queries_sha256": hashlib.sha256(qpath.read_bytes()).hexdigest(),
        "k": K,
        "unit": "paper: a query hits at k if any of the top k passages belongs to a relevant paper",
        "methods": [{"id": m.id, "method": m.method, "embedder": m.index.embedder_name} for m in methods],
        "skipped": skipped,
        **result,
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print_table(result["summary"], groups)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
