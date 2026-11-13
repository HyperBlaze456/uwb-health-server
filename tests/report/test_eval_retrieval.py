"""논문 찾기 평가(tools/eval_retrieval.py)의 순수 계산과, 바깥 API 벡터 함수(OpenRouterEmbedder)의 시험.

DB와 네트워크를 쓰지 않습니다. OpenRouter는 httpx.MockTransport의 가짜 전송으로 대신합니다.
"""

import json

import httpx
import pytest

from tools import eval_retrieval as ev
from uwbserver.report import papers

# ---------------------------------------------------------------- 지표 계산


def test_first_relevant_rank_is_first_passage_of_any_relevant_paper():
    ranked = ["a2020", "b2021", "a2020", "c2019"]
    assert ev.first_relevant_rank(ranked, ["c2019", "b2021"]) == 2
    assert ev.first_relevant_rank(ranked, ["a2020"]) == 1
    assert ev.first_relevant_rank(ranked, ["z2000"]) is None
    assert ev.first_relevant_rank([], ["a2020"]) is None


def test_hit_and_reciprocal_rank_respect_the_cutoff():
    assert ev.hit(1, 1) and ev.hit(5, 5) and not ev.hit(6, 5) and not ev.hit(None, 10)
    assert ev.reciprocal_rank(4, 10) == 0.25
    assert ev.reciprocal_rank(11, 10) == 0.0
    assert ev.reciprocal_rank(None, 10) == 0.0


def test_summarize():
    s = ev.summarize([1, 2, 7, None])
    assert s == {"n": 4, "hit@1": 0.25, "hit@5": 0.5, "hit@10": 0.75, "mrr@10": pytest.approx((1 + 0.5 + 1 / 7) / 4)}
    assert ev.summarize([]) == {"n": 0, "hit@1": None, "hit@5": None, "hit@10": None, "mrr@10": None}


def test_disagreements_count_each_side():
    a = [1, None, 3, 8]
    b = [2, 1, None, None]
    assert ev.disagreements(a, b, 1) == {"k": 1, "a_only": 1, "b_only": 1}
    assert ev.disagreements(a, b, 5) == {"k": 5, "a_only": 1, "b_only": 1}
    assert ev.disagreements(a, b, 10) == {"k": 10, "a_only": 2, "b_only": 1}
    with pytest.raises(ValueError):
        ev.disagreements([1], [], 5)


def test_read_queries_rejects_unknown_keys(tmp_path):
    path = tmp_path / "q.toml"
    path.write_text('[[query]]\nid = "q1"\ngroup = "g"\nquery = "x"\nrelevant = ["a2020"]\nhow = "t"\n')
    assert ev.read_queries(path, ["a2020"]) == [ev.Query("q1", "g", "x", ("a2020",))]
    with pytest.raises(papers.PaperStoreError, match="not in the paper list"):
        ev.read_queries(path, ["b2021"])


# ---------------------------------------------------------------- 질문 쪽 벡터


class _AsymmetricEmbedder:
    """대목은 [1, 0], 질문은 [0, 1]로 바꾸는 대역: Index가 질문에 embed_queries를 쓰는지 봅니다."""

    name = "asym-test"
    dim = 2

    def __call__(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_queries(self, texts):
        return [[0.0, 1.0] for _ in texts]


def test_embed_queries_uses_the_query_side_when_present():
    assert papers.embed_queries(_AsymmetricEmbedder(), ["q"]) == [[0.0, 1.0]]
    e = papers.NgramEmbedder()
    assert papers.embed_queries(e, ["gait speed"]) == e(["gait speed"])


def test_index_search_embeds_the_query_with_embed_queries(tmp_path):
    papers.add_paper(tmp_path, papers.Paper("a2020", "A", ["A"], 2020), "gait speed\n\nstride time\n")
    index = papers.index_in_memory(tmp_path, _AsymmetricEmbedder())
    hits = index.search("anything", 2, method="vector")
    assert [h.score for h in hits] == [0.0, 0.0]  # 질문 [0,1]과 대목 [1,0]은 직교


# ---------------------------------------------------------------- OpenRouter (가짜 전송)


def _fake(status_seq=None, dim=3072):
    """요청마다 글 수만큼 벡터를 돌려주는 가짜 전송. status_seq의 앞 값들은 그 상태 코드로 먼저 답함."""
    calls: list[dict] = []
    statuses = list(status_seq or [])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"body": body, "headers": dict(request.headers)})
        if statuses:
            return httpx.Response(statuses.pop(0), json={"error": {"message": "busy"}})
        data = [{"index": i, "embedding": [float(len(t))] + [0.0] * (dim - 1)} for i, t in enumerate(body["input"])]
        return httpx.Response(200, json={"data": list(reversed(data)), "model": body["model"]})

    return httpx.MockTransport(handler), calls


def test_openrouter_needs_the_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    transport, calls = _fake()
    e = papers.OpenRouterEmbedder(transport=transport)
    with pytest.raises(papers.PaperStoreError, match="OPENROUTER_API_KEY"):
        e(["x"])
    assert calls == []


def test_openrouter_batches_orders_and_sends_no_optional_headers(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    transport, calls = _fake()
    e = papers.OpenRouterEmbedder(transport=transport)
    texts = [f"t{i}" * (i + 1) for i in range(70)]
    out = e(texts)
    assert [v[0] for v in out] == [float(len(t)) for t in texts]  # index 순으로 되돌림
    assert [len(c["body"]["input"]) for c in calls] == [64, 6]
    assert calls[0]["body"]["model"] == "openai/text-embedding-3-large"
    h = calls[0]["headers"]
    assert h["authorization"] == "Bearer test-key"
    assert "http-referer" not in h and "x-title" not in h


def test_openrouter_retries_429_and_5xx_then_gives_up(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    transport, calls = _fake([429, 503])
    e = papers.OpenRouterEmbedder(transport=transport, retry_wait_s=0)
    assert len(e(["x"])) == 1 and len(calls) == 3
    transport, calls = _fake([500] * 10)
    e = papers.OpenRouterEmbedder(transport=transport, retry_wait_s=0)
    with pytest.raises(papers.PaperStoreError, match="gave up"):
        e(["x"])
    assert len(calls) == papers.OPENROUTER_RETRIES + 1


def test_openrouter_does_not_retry_client_errors(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    transport, calls = _fake([401])
    e = papers.OpenRouterEmbedder(transport=transport, retry_wait_s=0)
    with pytest.raises(papers.PaperStoreError, match="HTTP 401"):
        e(["x"])
    assert len(calls) == 1


def test_openrouter_cache_sends_each_text_once(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    transport, calls = _fake(dim=1536)
    e = papers.OpenRouterEmbedder("openai/text-embedding-3-small", cache_dir=tmp_path, transport=transport)
    first = e(["aa", "b"])
    assert e(["b", "aa"]) == [first[1], first[0]] and len(calls) == 1
    # 새 프로세스처럼 캐시를 파일에서 다시 읽어도 보내지 않고, 새 글만 보냄
    again = papers.OpenRouterEmbedder("openai/text-embedding-3-small", cache_dir=tmp_path, transport=transport)
    assert again(["aa", "ccc"])[0] == first[0]
    assert [c["body"]["input"] for c in calls] == [["aa", "b"], ["ccc"]]


def test_vector_cache_ignores_a_torn_tail(tmp_path):
    c = papers.VectorCache(tmp_path, "m", 2)
    c.put(["a"], [[1.0, 2.0]])
    vec = next(tmp_path.glob("*.f32"))
    vec.write_bytes(vec.read_bytes() + b"\x00" * 4)  # 키 없이 반쯤 쓰인 행
    c2 = papers.VectorCache(tmp_path, "m", 2)
    assert c2.get("a") == [1.0, 2.0] and c2.get("b") is None
    c2.put(["b"], [[3.0, 4.0]])
    c3 = papers.VectorCache(tmp_path, "m", 2)
    assert c3.get("b") == [3.0, 4.0] and c3.get("a") == [1.0, 2.0]


def test_vector_cache_keeps_rows_aligned_with_repeated_texts(tmp_path):
    """같은 묶음에 같은 글이 두 번 있어도(대목에 같은 제목 줄이 여럿) 뒤의 글들의 벡터가 밀리지 않음."""
    c = papers.VectorCache(tmp_path, "m", 1)
    c.put(["a", "dup", "dup", "b"], [[1.0], [2.0], [2.0], [3.0]])
    c.put(["c", "dup"], [[4.0], [2.0]])
    for cache in (c, papers.VectorCache(tmp_path, "m", 1)):
        assert [cache.get(t) for t in ["a", "dup", "b", "c"]] == [[1.0], [2.0], [3.0], [4.0]]
    assert len(next(tmp_path.glob("*.keys")).read_text().split()) == 4


def test_embedder_by_name_knows_openrouter():
    e = papers.embedder_by_name("openrouter:openai/text-embedding-3-large")
    assert (e.name, e.dim) == ("openrouter:openai/text-embedding-3-large", 3072)
    with pytest.raises(papers.PaperStoreError, match="unknown OpenRouter"):
        papers.embedder_by_name("openrouter:nope/model")
