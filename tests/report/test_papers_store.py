"""논문 저장소와 도구의 시험 (SPEC M10, RP-5, RP-6, TL-10). DB·네트워크·모델이 필요 없습니다.

도구(tools/papers.py)의 main()을 같은 프로세스에서 부르고, 저장소 뿌리는 tmp 폴더입니다.
"""

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from tools import papers as tool
from uwbserver.report import papers

GAIT = """Gait speed is a simple measure of walking.

Slower gait speed predicts falls in older adults.

Gait speed norms differ by age and sex."""

VAR = """Stride time variability reflects rhythmic control of walking.

Higher stride time variability is linked with falls.\fVariability is measured as the coefficient of variation."""

OTHER = """Bone mineral density declines with age.

Fracture risk depends on bone density."""


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "papers"


@pytest.fixture
def inputs(tmp_path: Path) -> Path:
    d = tmp_path / "in"
    d.mkdir()
    write(d / "gait.txt", GAIT)
    write(d / "var.txt", VAR)
    write(d / "other.txt", OTHER)
    (d / "gait.pdf").write_bytes(b"%PDF-1.4 gait")
    (d / "var.pdf").write_bytes(b"%PDF-1.4 var")
    (d / "other.pdf").write_bytes(b"%PDF-1.4 other")
    return d


def add(root: Path, inputs: Path, key: str, name: str, *extra: str) -> int:
    return tool.main([
        "--root", str(root), "add", "--key", key, "--title", f"Title {key}", "--authors", "Kim A; Lee B",
        "--year", key[-4:], "--doi", f"10.1/{key}", "--use", "quote short passages",
        "--text", str(inputs / f"{name}.txt"), "--source", str(inputs / f"{name}.pdf"), *extra,
    ])  # fmt: skip


@pytest.fixture
def store(root: Path, inputs: Path) -> Path:
    assert add(root, inputs, "studenski2011", "gait") == 0
    assert add(root, inputs, "hausdorff2001", "var") == 0
    assert add(root, inputs, "smith2010", "other") == 0
    assert tool.main(["--root", str(root), "build"]) == 0
    return root


def catalog(tmp_path: Path, metrics: dict[str, list[str]]) -> Path:
    lines = []
    for mid, sources in metrics.items():
        lines += ["[[metric]]", f'id = "{mid}"', f"sources = {json.dumps(sources)}", ""]
    return write(tmp_path / "metrics.toml", "\n".join(lines))


# ---------------------------------------------------------------- M10-1 넣기, 다시 만들기


def test_added_paper_is_in_list_source_and_passages(store: Path, inputs: Path) -> None:
    listed = papers.read_papers(store)
    p = listed["hausdorff2001"]
    assert (p.title, p.authors, p.year, p.doi, p.use) == (
        "Title hausdorff2001", ["Kim A", "Lee B"], 2001, "10.1/hausdorff2001", "quote short passages",
    )  # fmt: skip
    assert p.source == "hausdorff2001.pdf"
    assert (store / "source" / "hausdorff2001.pdf").read_bytes() == b"%PDF-1.4 var"
    # 대목을 다시 만들 수 있도록 나눈 글도 남김
    assert (store / "text" / "hausdorff2001.txt").read_text(encoding="utf-8") == VAR
    lines = (store / "passages" / "hausdorff2001.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    assert rows[0] == {"id": "hausdorff2001#1", "page": 1,
                       "text": "Stride time variability reflects rhythmic control of walking."}  # fmt: skip
    assert [r["id"] for r in rows] == ["hausdorff2001#1", "hausdorff2001#2", "hausdorff2001#3"]
    assert rows[2]["page"] == 2
    assert [x.id for x in papers.read_passages(store, "hausdorff2001")] == [r["id"] for r in rows]
    with (store / "papers.toml").open("rb") as f:
        assert {p["key"] for p in tomllib.load(f)["paper"]} == {"studenski2011", "hausdorff2001", "smith2010"}


def test_papers_version_is_sha256_of_list_file(store: Path) -> None:
    import hashlib

    assert papers.papers_version(store) == hashlib.sha256((store / "papers.toml").read_bytes()).hexdigest()


def test_add_existing_key_fails_unless_replace(store: Path, inputs: Path, capsys) -> None:
    assert add(store, inputs, "smith2010", "gait") == 2
    assert "smith2010" in capsys.readouterr().err
    assert papers.read_passages(store, "smith2010")[0].text.startswith("Bone")
    assert add(store, inputs, "smith2010", "gait", "--replace") == 0
    assert papers.read_passages(store, "smith2010")[0].text.startswith("Gait")
    assert (store / "source" / "smith2010.pdf").read_bytes() == b"%PDF-1.4 gait"
    assert len(papers.read_papers(store)) == 3


def test_add_without_source_and_bad_key(root: Path, inputs: Path) -> None:
    rc = tool.main(["--root", str(root), "add", "--key", "kim2020", "--title", "T", "--authors", "Kim",
                    "--year", "2020", "--text", str(inputs / "gait.txt")])  # fmt: skip
    assert rc == 0
    assert papers.read_papers(root)["kim2020"].source is None
    bad = tool.main(["--root", str(root), "add", "--key", "Kim-2020", "--title", "T", "--authors", "Kim",
                     "--year", "2020", "--text", str(inputs / "gait.txt")])  # fmt: skip
    assert bad == 2


def test_rebuilding_index_gives_same_results(store: Path) -> None:
    keys = ["studenski2011", "hausdorff2001", "smith2010"]
    e = papers.NgramEmbedder()
    before = [(h.passage.id, h.score) for h in papers.search(store, keys, "falls in older adults", 5, e)]
    shutil.rmtree(store / "index")
    with pytest.raises(papers.IndexProblem):
        papers.load_index(store, e)
    assert tool.main(["--root", str(store), "build"]) == 0
    after = [(h.passage.id, h.score) for h in papers.search(store, keys, "falls in older adults", 5, e)]
    assert before == after
    assert len(before) == 5


# ---------------------------------------------------------------- M10-2 고른 논문 안에서만


def test_search_only_returns_passages_of_given_keys(store: Path) -> None:
    e = papers.NgramEmbedder()
    # 질문은 다른 논문(hausdorff2001)에 더 가깝지만, 고른 논문에서만 나옴
    hits = papers.search(store, ["studenski2011"], "stride time variability", 3, e)
    assert len(hits) == 3
    assert {h.passage.key for h in hits} == {"studenski2011"}
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    two = papers.search(store, ["studenski2011", "hausdorff2001"], "stride time variability", 10, e)
    assert {h.passage.key for h in two} == {"studenski2011", "hausdorff2001"}
    assert two[0].passage.key == "hausdorff2001"


def test_search_with_no_keys_gives_nothing(store: Path) -> None:
    assert papers.search(store, [], "gait speed", 3, papers.NgramEmbedder()) == []


def test_search_unknown_key_is_an_error(store: Path) -> None:
    with pytest.raises(papers.PaperStoreError, match="nobody2000"):
        papers.search(store, ["studenski2011", "nobody2000"], "gait", 3, papers.NgramEmbedder())


def test_search_refuses_stale_index(store: Path, inputs: Path) -> None:
    assert add(store, inputs, "smith2010", "gait", "--replace") == 0
    with pytest.raises(papers.IndexProblem, match="stale"):
        papers.search(store, ["smith2010"], "gait", 3, papers.NgramEmbedder())


def test_search_refuses_index_of_other_embedder(store: Path) -> None:
    with pytest.raises(papers.IndexProblem, match="embedder"):
        papers.load_index(store, papers.NgramEmbedder(dim=64))


def test_search_tool_prints_passages(store: Path, capsys) -> None:
    rc = tool.main(["--root", str(store), "search", "--keys", "hausdorff2001", "--query", "variability", "-k", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("hausdorff2001#") == 2
    assert "studenski2011" not in out


# ---------------------------------------------------------------- M10-3 check


def test_check_passes_on_consistent_store(store: Path, tmp_path: Path, capsys) -> None:
    cat = catalog(tmp_path, {"period.speed_median_mps": ["studenski2011"],
                             "period.stride_time_cv": ["hausdorff2001", "studenski2011"],
                             "period.bout_count": []})  # fmt: skip
    assert tool.main(["--root", str(store), "check", "--catalog", str(cat)]) == 0
    assert "ok" in capsys.readouterr().out


def problems(store: Path, cat: Path) -> set[tuple[str, str]]:
    return {(p.kind, p.subject) for p in papers.check(store, cat, papers.NgramEmbedder())}


def test_check_reports_missing_paper_for_catalog_key(store: Path, tmp_path: Path, capsys) -> None:
    cat = catalog(tmp_path, {"period.speed_median_mps": ["studenski2011", "nobody2000"]})
    assert problems(store, cat) == {("missing_paper", "nobody2000")}
    assert tool.main(["--root", str(store), "check", "--catalog", str(cat)]) == 1
    out = capsys.readouterr().out
    assert "missing_paper" in out and "nobody2000" in out and "period.speed_median_mps" in out


def test_check_reports_missing_passages_and_source(store: Path, tmp_path: Path) -> None:
    cat = catalog(tmp_path, {"m.a": ["hausdorff2001"]})
    (store / "passages" / "hausdorff2001.jsonl").unlink()
    (store / "source" / "smith2010.pdf").unlink()
    found = problems(store, cat)
    assert ("no_passages", "hausdorff2001") in found
    assert ("missing_source", "smith2010") in found


def test_check_reports_paper_without_source(root: Path, inputs: Path, tmp_path: Path) -> None:
    tool.main(["--root", str(root), "add", "--key", "kim2020", "--title", "T", "--authors", "Kim",
               "--year", "2020", "--text", str(inputs / "gait.txt")])  # fmt: skip
    tool.main(["--root", str(root), "build"])
    assert problems(root, catalog(tmp_path, {})) == {("no_source", "kim2020")}


def test_check_reports_missing_and_stale_index(store: Path, inputs: Path, tmp_path: Path) -> None:
    cat = catalog(tmp_path, {})
    assert problems(store, cat) == set()
    assert add(store, inputs, "smith2010", "gait", "--replace") == 0
    assert problems(store, cat) == {("stale_index", "index")}
    shutil.rmtree(store / "index")
    assert problems(store, cat) == {("no_index", "index")}


def test_check_missing_catalog_is_argument_error(store: Path, tmp_path: Path) -> None:
    assert tool.main(["--root", str(store), "check", "--catalog", str(tmp_path / "none.toml")]) == 2
