"""논문 저장소 도구 (SPEC TL-10, RP-5, RP-6). DB를 쓰지 않습니다.

    uv run python tools/papers.py add --key K --title T --authors "Kim A; Lee B" --year 2017 \\
        [--doi D] [--use "..."] --text FILE.txt [--source FILE.pdf] [--replace]
    uv run python tools/papers.py build [--embedder NAME] [--embed-cache DIR]
    uv run python tools/papers.py check [--catalog contracts/metrics.toml]
    uv run python tools/papers.py search --query "..." [--keys a2017,b2020] [--method hybrid|bm25|vector] [-k 3]

add:    논문 하나를 목록에 넣고, 원문(--source)을 source/<key>.<확장자>로 복사하고, 평문(--text)을 대목으로 나눕니다.
        PDF에서 글을 뽑는 일은 이 도구가 하지 않습니다. 사람이 평문 파일(UTF-8)을 따로 만들어 --text로 줍니다
        (예: pdftotext가 낸 파일. 쪽 나눔 문자 \\f가 있으면 대목에 쪽 번호가 붙음). 대목은 빈 줄로 나뉜 문단입니다.
        이미 있는 key는 --replace 없이는 거절합니다. key 꼴: <첫 저자 성 소문자><연도>[글자], 예 schlachetzki2017.
build:  모든 대목으로 색인을 다시 만듭니다. 대목을 바꾼 뒤(add) 꼭 돌립니다. 기본 벡터 함수는
        글자 n-gram 해시 대역(뜻을 아는 모델이 아님)입니다. --embedder openrouter:<모델>은 OpenRouter의
        임베딩 API(환경 변수 OPENROUTER_API_KEY 필요)이고, 그 색인으로 찾을 때도 질문마다 API를 부릅니다.
        --embed-cache를 주면 이미 센 대목을 다시 보내지 않습니다(OpenRouter만).
check:  지표 목록의 모든 sources key가 논문 목록에 있는지, 논문마다 대목과 원문 파일이 있는지,
        색인이 있고 지금 대목과 맞는지 봅니다.
search: 질문에 맞는 대목을 보입니다. --keys를 주면 그 논문 안에서만, 주지 않으면 모든 논문에서 찾습니다.
        --method: hybrid(기본, 단어 맞춤 BM25 순위와 벡터 순위를 순위 역수의 합으로 섞음), bm25, vector.
        벡터는 색인을 만든 벡터 함수로 셉니다. 줄마다 점수, 대목 id, 쪽, 두 순위(없으면 -)를 보입니다.

저장소 뿌리는 --root, 없으면 DATA_ROOT/papers(기본 /data/papers)입니다.
종료 코드: 0 = 정상, 1 = check에서 문제가 있음, 2 = 인자 오류나 저장소를 쓸 수 없음.
"""

import argparse
import sys
from pathlib import Path

from uwbserver.report import papers

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPO / "contracts" / "metrics.toml"


def cmd_add(root: Path, a: argparse.Namespace) -> int:
    text = Path(a.text).read_text(encoding="utf-8")
    source = Path(a.source) if a.source else None
    if source is not None and not source.is_file():
        raise papers.PaperStoreError(f"source file not found: {source}")
    authors = [x.strip() for x in a.authors.split(";") if x.strip()]
    paper = papers.Paper(a.key, a.title, authors, a.year, a.doi, None, a.use)
    n = papers.add_paper(root, paper, text, source, replace=a.replace)
    print(f"{a.key}: {n} passages. run 'build' to update the index.")
    return 0


def cmd_build(root: Path, a: argparse.Namespace) -> int:
    e = papers.embedder_by_name(a.embedder, cache_dir=Path(a.embed_cache) if a.embed_cache else None)
    n = papers.build_index(root, e)
    print(f"index built: {n} passages, embedder {e.name}")
    return 0


def cmd_check(root: Path, a: argparse.Namespace) -> int:
    catalog = Path(a.catalog)
    if not catalog.is_file():
        print(f"catalog not found: {catalog}", file=sys.stderr)
        return 2
    found = papers.check(root, catalog)
    for p in found:
        print(f"{p.kind:15} {p.subject:20} {p.detail}")
    if found:
        print(f"{len(found)} problem(s)")
        return 1
    print(f"ok: {len(papers.read_papers(root))} papers, catalog {catalog}")
    return 0


def cmd_search(root: Path, a: argparse.Namespace) -> int:
    keys = None if a.keys is None else [k.strip() for k in a.keys.split(",") if k.strip()]
    hits = papers.search(root, keys, a.query, a.k, None, a.method)

    def show(r: int | None) -> str:
        return "-" if r is None else str(r)

    for h in hits:
        page = "-" if h.passage.page is None else h.passage.page
        print(f"{h.score:.4f}  {h.passage.id}  page {page}  bm25 {show(h.bm25_rank)}  vector {show(h.vector_rank)}"
              f"\n    {h.passage.text}")  # fmt: skip
    if not hits:
        print("no passages")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", help="paper store root (default: DATA_ROOT/papers)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add", help="register a paper from a plain text file (no PDF extraction)")
    s.add_argument("--key", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--authors", required=True, help='separated by ";", e.g. "Kim A; Lee B"')
    s.add_argument("--year", required=True, type=int)
    s.add_argument("--doi")
    s.add_argument("--use", default="", help="permitted use (free text)")
    s.add_argument("--text", required=True, help="plain UTF-8 text of the paper (made by a person, e.g. pdftotext)")
    s.add_argument("--source", help="original file to keep (e.g. the PDF)")
    s.add_argument("--replace", action="store_true", help="replace a paper already in the list")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("build", help="rebuild the search index from all passages")
    s.add_argument("--embedder", help="embedder name (default: built-in ngram stand-in; e.g. openrouter:<model>)")
    s.add_argument("--embed-cache", help="vector cache directory for API embedders (e.g. report/data/cache)")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("check", help="check the store against the metric catalog")
    s.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("search", help="find passages in all papers, or only in the given ones")
    s.add_argument("--keys", help="comma separated paper keys (default: all papers)")
    s.add_argument("--method", default="hybrid", help="hybrid (default), bm25 or vector")
    s.add_argument("--query", required=True)
    s.add_argument("-k", type=int, default=3)
    s.set_defaults(func=cmd_search)

    a = p.parse_args(argv)
    root = Path(a.root) if a.root else papers.default_root()
    try:
        return a.func(root, a)
    except (papers.PaperStoreError, OSError, UnicodeDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
