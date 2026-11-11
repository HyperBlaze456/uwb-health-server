"""논문 저장소의 순수 계산 단위 시험 (SPEC RP-5, RP-6): 대목 나누기, 대신 쓰는 벡터 함수, 순위 매기기."""

import math

import pytest

from uwbserver.report import papers


# ---------------------------------------------------------------- 대목 나누기


def test_split_by_blank_lines_and_collapse_spaces() -> None:
    text = "First  paragraph\nstill first.\n\n\nSecond paragraph.\n  \nThird."
    assert papers.split_passages(text) == [
        (None, "First paragraph still first."),
        (None, "Second paragraph."),
        (None, "Third."),
    ]


def test_split_form_feed_gives_page_numbers() -> None:
    text = "Page one text.\n\nMore on one.\fPage two text.\f\fPage four."
    assert papers.split_passages(text) == [
        (1, "Page one text."),
        (1, "More on one."),
        (2, "Page two text."),
        (4, "Page four."),
    ]


def test_split_long_paragraph_at_spaces_within_limit() -> None:
    words = " ".join(f"w{i:03d}" for i in range(100))  # 499자
    parts = papers.split_passages(words, max_chars=60)
    assert all(len(t) <= 60 for _, t in parts)
    assert " ".join(t for _, t in parts) == words


def test_split_word_longer_than_limit_is_cut() -> None:
    parts = papers.split_passages("x" * 25, max_chars=10)
    assert [t for _, t in parts] == ["x" * 10, "x" * 10, "x" * 5]


def test_split_is_deterministic_and_empty_text_gives_nothing() -> None:
    text = "a b c\n\nd e f"
    assert papers.split_passages(text) == papers.split_passages(text)
    assert papers.split_passages("  \n\n \f ") == []


# ---------------------------------------------------------------- 대신 쓰는 벡터 함수


def test_ngram_embedder_is_deterministic_normalized_and_sized() -> None:
    e = papers.NgramEmbedder(dim=128)
    assert e.dim == 128
    assert "ngram" in e.name and "128" in e.name
    a, b, empty = e(["Gait speed declines with age.", "Gait speed declines with age.", ""])
    assert a == b
    assert len(a) == 128
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-9)
    assert all(x == 0.0 for x in empty)


def test_ngram_embedder_close_texts_score_higher() -> None:
    e = papers.NgramEmbedder()
    q, near, far = e(["stride time variability", "variability of stride time in older adults", "bone density"])
    assert papers.cosine(q, near) > papers.cosine(q, far)


def test_embedder_lookup_by_name() -> None:
    e = papers.NgramEmbedder(dim=64)
    assert papers.embedder_by_name(e.name).name == e.name
    assert papers.embedder_by_name(None).name == papers.NgramEmbedder().name
    with pytest.raises(papers.PaperStoreError):
        papers.embedder_by_name("no-such-model")


# ---------------------------------------------------------------- 순위


def test_cosine_basic_and_zero_vector() -> None:
    assert papers.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert papers.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert papers.cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_rank_orders_by_score_and_breaks_ties_by_position() -> None:
    q = [1.0, 0.0]
    vectors = [[0.0, 1.0], [1.0, 0.0], [1.0, 1.0], [2.0, 0.0]]
    # 1번과 3번이 같은 점수(1.0)이면 앞의 것이 먼저
    assert [i for i, _ in papers.rank(q, vectors, 3)] == [1, 3, 2]
    assert papers.rank(q, vectors, 0) == []
    assert len(papers.rank(q, vectors, 10)) == 4
    assert papers.rank(q, [], 3) == []


# ---------------------------------------------------------------- 단어 맞춤(BM25)과 순위 합치기 (RP-6)


def test_tokenize_lowercases_splits_on_non_alphanumeric_and_keeps_digits() -> None:
    assert papers.tokenize("Stride-time CV (%) was 2.5, n_steps=40") == [
        "stride", "time", "cv", "was", "2", "5", "n", "steps", "40",
    ]  # fmt: skip
    assert papers.tokenize("Gangbild Ärzte") == ["gangbild", "ärzte"]
    assert papers.tokenize(" ,.- ") == []


def test_bm25_scores_match_hand_arithmetic() -> None:
    # 대목 0 "gait speed"(길이 2), 1 "gait gait falls"(길이 3), 2 "bone density"(길이 2)
    # N = 3, avgdl = 7/3, df(gait) = 2 → idf = ln(1 + (3 - 2 + 0.5) / (2 + 0.5)) = ln(1.6)
    # 점수 = idf · tf·(k1+1) / (tf + k1·(1 - b + b·len/avgdl)),  k1 = 1.5, b = 0.75
    #   0: ln1.6 · 1·2.5 / (1 + 1.5·(0.25 + 0.75·2/(7/3))) = ln1.6 · 2.5 / 2.339286 = ln1.6 · 1.068702
    #   1: ln1.6 · 2·2.5 / (2 + 1.5·(0.25 + 0.75·3/(7/3))) = ln1.6 · 5.0 / 3.821429 = ln1.6 · 1.308411
    #   2: "gait"가 없으므로 순위에 들지 않음
    bm = papers.Bm25.build(["gait speed", "gait gait falls", "bone density"])
    ranked = bm.rank("Gait", [0, 1, 2])
    assert [i for i, _ in ranked] == [1, 0]
    assert ranked[0][1] == pytest.approx(math.log(1.6) * 5.0 / (2 + 1.5 * (0.25 + 0.75 * 3 / (7 / 3))))
    assert ranked[1][1] == pytest.approx(math.log(1.6) * 2.5 / (1 + 1.5 * (0.25 + 0.75 * 2 / (7 / 3))))
    assert ranked[0][1] == pytest.approx(math.log(1.6) * 1.308411, rel=1e-6)


def test_bm25_rank_only_inside_given_positions_and_ties_by_position() -> None:
    bm = papers.Bm25.build(["gait speed", "gait gait falls", "gait speed", "bone density"])
    # 0과 2는 글이 같아 점수가 같음 → 앞의 것이 먼저
    assert [i for i, _ in bm.rank("gait speed", [3, 2, 0])] == [0, 2]
    assert bm.rank("unknownword", [0, 1, 2, 3]) == []
    assert bm.rank("", [0, 1]) == []
    # 질문에 같은 낱말이 두 번 있어도 한 번으로 셈
    assert bm.rank("gait gait", [0, 1]) == bm.rank("gait", [0, 1])


def test_fuse_sums_reciprocal_ranks() -> None:
    # 2: 1/61 + 1/62,  1: 1/63 + 1/61,  0: 1/62 (둘째 순위에 없으므로 그쪽 몫은 없음)
    fused = papers.fuse([[2, 0, 1], [1, 2]])
    assert [i for i, _ in fused] == [2, 1, 0]
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[1][1] == pytest.approx(1 / 63 + 1 / 61)
    assert fused[2][1] == pytest.approx(1 / 62)


def test_fuse_breaks_ties_by_position_and_handles_empty() -> None:
    assert [i for i, _ in papers.fuse([[1], [0]])] == [0, 1]
    assert [i for i, _ in papers.fuse([[5, 3], [3, 5]])] == [3, 5]
    assert papers.fuse([[], []]) == []
    assert [i for i, _ in papers.fuse([[], [4, 2]])] == [4, 2]
