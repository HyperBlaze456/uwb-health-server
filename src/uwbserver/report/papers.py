"""논문 저장소와 근거 찾기 (SPEC RP-5, RP-6).

DB가 아니라 파일입니다. 뿌리(<data root>/papers/) 아래:

    papers.toml          목록: key, title, authors, year, doi, source(원문 파일 이름), use(쓸 수 있는 범위)
    source/<key>.<ext>   원문. 사람이 넣음. 영구 원본
    text/<key>.txt       대목을 나눈 글(사람이 넣은 평문). 이것에서 passages를 다시 만들 수 있음
    passages/<key>.jsonl 대목. 줄마다 {"id": "<key>#<순번>", "page": 정수 또는 null, "text": ...}
    index/               검색 색인(캐시). meta.json + vectors.f32. 지우고 다시 만들면 같은 결과

순수 계산(대목 나누기, 대신 쓰는 벡터 함수, 유사도와 순위, 단어 맞춤 BM25, 순위 합치기)과 파일 읽고 쓰기를 한 파일에 두되
나눠 둡니다. 순수 부분은 파일을 모르므로 따로 단위 시험합니다.

대목을 수치 벡터로 바꾸는 함수(Embedder)는 바꿔 끼웁니다. 기본은 네트워크 없이 도는 글자 n-gram 해시
(NgramEmbedder)입니다. 뜻을 아는 모델이 아니라 시험과 오프라인용 대역입니다. 뜻을 아는 모델은 선택 사항으로 둘이
있습니다: OpenRouterEmbedder(OpenRouter의 임베딩 API, 키 필요)와 FastEmbedEmbedder(이 PC에서 도는 작은 모델,
선택 의존성 묶음 embed가 있을 때만).
"""

import hashlib
import json
import math
import os
import re
import sys
import time
import tomllib
from array import array
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from uwbserver.pipeline import files, settings

LIST_FILE = "papers.toml"
INDEX_FORMAT = "uwbserver.papers_index/1"
MAX_PASSAGE_CHARS = 1200
EMBED_BATCH = 64
# SPEC 14.4 MC-7: <첫 저자 성 소문자><연도>, 같은 저자·연도가 겹치면 끝에 글자 하나
KEY_RE = re.compile(r"^[a-z]+[0-9]{4}[a-z]?$")


class PaperStoreError(Exception):
    """저장소가 쓸 수 없는 상태이거나 요청이 틀림. 메시지는 사람이 읽고 고칠 수 있게 씁니다."""


class IndexProblem(PaperStoreError):
    """색인이 없거나(no_index), 지금 대목·벡터 함수와 맞지 않거나(stale_index), 깨짐(bad_index)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# ================================================================ 순수 계산


def _cut(flat: str, max_chars: int) -> list[str]:
    """한 문단을 max_chars 이하로 자릅니다. 되도록 빈칸에서 자르고, 빈칸이 없으면 글자 수로 자릅니다."""
    out: list[str] = []
    while len(flat) > max_chars:
        at = flat.rfind(" ", 0, max_chars + 1)
        if at > 0:
            out.append(flat[:at])
            flat = flat[at + 1 :]
        else:
            out.append(flat[:max_chars])
            flat = flat[max_chars:]
    if flat:
        out.append(flat)
    return out


def split_passages(text: str, max_chars: int = MAX_PASSAGE_CHARS) -> list[tuple[int | None, str]]:
    """글을 대목 (page, text) 목록으로 나눕니다.

    빈 줄로 나뉜 문단 하나가 대목 하나이고, 문단 안의 줄바꿈·빈칸은 빈칸 하나로 줄입니다. max_chars보다 긴 문단은
    빈칸에서 잘라 여럿으로 만듭니다. 글에 쪽 나눔 문자(\\f, pdftotext가 쪽마다 넣음)가 있으면 쪽 번호(1부터)를,
    없으면 None을 붙입니다.
    """
    pages = text.split("\f")
    numbered = len(pages) > 1
    out: list[tuple[int | None, str]] = []
    for page_no, page in enumerate(pages, 1):
        for para in re.split(r"\n\s*\n", page):
            flat = " ".join(para.split())
            for piece in _cut(flat, max_chars):
                out.append((page_no if numbered else None, piece))
    return out


class Embedder(Protocol):
    """글 목록을 같은 길이(dim)의 벡터 목록으로 바꾸는 함수. name이 다르면 다른 벡터 공간으로 봅니다."""

    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def __call__(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class NgramEmbedder:
    """대신 쓰는 벡터 함수: 글자 n-gram을 해시로 dim 칸에 세고 길이 1로 맞춥니다.

    뜻을 아는 모델이 아닙니다. 같은 글자 조각이 많이 겹치는 글끼리 가깝게 나올 뿐입니다.
    네트워크와 의존성이 없고 결과가 늘 같아서 시험과 오프라인 사용에 씁니다.
    """

    dim: int = 256
    n: int = 3

    @property
    def name(self) -> str:
        return f"ngram{self.n}-hash{self.dim}-v1"

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        t = f" {' '.join(text.lower().split())} "
        for i in range(len(t) - self.n + 1):
            h = int.from_bytes(hashlib.blake2b(t[i : i + self.n].encode(), digest_size=8).digest(), "little")
            v[h % self.dim] += -1.0 if (h >> 63) & 1 else 1.0
        norm = sum(x * x for x in v) ** 0.5
        return [x / norm for x in v] if norm else v

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) if t.strip() else [0.0] * self.dim for t in texts]


def embed_queries(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    """질문 쪽 벡터. 벡터 함수에 embed_queries가 있으면 그것을, 없으면 대목과 같은 함수를 씁니다.

    질문과 대목을 다르게 바꾸는 모델(예: bge는 질문 앞에 지시문을 붙임)은 embed_queries를 둡니다.
    """
    f = getattr(embedder, "embed_queries", None)
    return f(texts) if f is not None else embedder(texts)


EMBED_GROUP_HINT = "install the optional group: uv sync --group embed (or run with: uv run --group embed ...)"
# 모델 → (dim, 질문 앞에 붙이는 지시문). 지시문은 모델 카드가 정한 글 그대로입니다.
# bge v1.5 카드: 짧은 질문으로 대목을 찾을 때는 질문에만 지시문을 붙이고 대목에는 붙이지 않음.
# fastembed의 query_embed는 bge에 지시문을 붙이지 않으므로(0.9.1 확인) 여기서 붙입니다.
FASTEMBED_MODELS: dict[str, tuple[int, str]] = {
    "BAAI/bge-small-en-v1.5": (384, "Represent this sentence for searching relevant passages: "),
}


class FastEmbedEmbedder:
    """뜻을 아는 벡터 함수(선택 사항): fastembed로 이 PC에서 도는 문장 임베딩 모델.

    fastembed는 선택 의존성 묶음 embed에만 있고, 처음 벡터를 셀 때 import합니다. 없으면 PaperStoreError.
    모델 파일은 처음 한 번 내려받아 fastembed의 캐시(FASTEMBED_CACHE_PATH, 없으면 임시 폴더)에 둡니다.
    대목은 그대로, 질문은 모델의 지시문을 붙여 바꿉니다(embed_queries).
    """

    __slots__ = ("model", "dim", "query_prefix", "_model")

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        if model not in FASTEMBED_MODELS:
            raise PaperStoreError(f"unknown fastembed model {model!r} (known: {', '.join(FASTEMBED_MODELS)})")
        self.model = model
        self.dim, self.query_prefix = FASTEMBED_MODELS[model]
        self._model = None

    @property
    def name(self) -> str:
        return f"fastembed:{self.model}"

    def _load(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError:
                raise PaperStoreError(f"embedder {self.name} needs the fastembed package; {EMBED_GROUP_HINT}") from None
            self._model = TextEmbedding(self.model)
        return self._model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [[float(x) for x in v] for v in self._load().embed(texts)]

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self.query_prefix + t for t in texts])


class VectorCache:
    """보낸 글 → 벡터의 디스크 캐시. 같은 글을 바깥 API로 두 번 보내지 않으려고 둡니다.

    키 = sha256(모델 이름 + NUL + 보낸 글 그대로). 폴더 아래 모델마다 두 파일을 덧붙여 씁니다:
    <모델>.f32(행마다 dim개의 float32)와 <모델>.keys(행마다 키 한 줄). 키 파일의 n번째 줄이 벡터 파일의 n번째 행입니다.
    벡터를 먼저, 키를 나중에 쓰므로 중간에 끊기면 키가 없는 꼬리 행이 남을 수 있습니다. 읽을 때 두 파일의 행 수가
    다르면 짧은 쪽에 맞춰 두 파일을 자릅니다. 캐시이므로 지워도 다시 셀 수 있습니다.
    """

    def __init__(self, directory: Path, model: str, dim: int) -> None:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", model)
        self.model, self.dim = model, dim
        self._vec_path = directory / f"{safe}.f32"
        self._key_path = directory / f"{safe}.keys"
        self._rows: dict[str, int] = {}  # 키 → 행 번호
        self._n = 0  # 파일의 행 수
        self._data = array("f")
        if self._key_path.exists() and self._vec_path.exists():
            keys = self._key_path.read_text(encoding="ascii").split()
            data = _unpack(self._vec_path.read_bytes())
            n = min(len(keys), len(data) // dim)
            if len(keys) != n or len(data) != n * dim:
                with self._vec_path.open("r+b") as f:
                    f.truncate(n * dim * 4)
                files.write_atomic_file(self._key_path, "".join(f"{k}\n" for k in keys[:n]).encode("ascii"))
            self._rows = {k: i for i, k in enumerate(keys[:n])}
            self._n = n
            self._data = data[: n * dim]

    def key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\x00{text}".encode()).hexdigest()

    def get(self, text: str) -> list[float] | None:
        i = self._rows.get(self.key(text))
        return None if i is None else list(self._data[i * self.dim : (i + 1) * self.dim])

    def put(self, texts: Sequence[str], vectors: Sequence[Sequence[float]]) -> None:
        new: dict[str, Sequence[float]] = {}  # 같은 묶음 안에 같은 글이 두 번 있어도 한 행만
        for t, v in zip(texts, vectors, strict=True):
            k = self.key(t)
            if k not in self._rows and k not in new:
                new[k] = v
        if not new:
            return
        self._vec_path.parent.mkdir(parents=True, exist_ok=True)
        # 지난번에 키를 쓰기 전에 끊겼으면 키 없는 꼬리 행을 잘라 낸 뒤 덧붙입니다.
        with self._vec_path.open("ab") as f:
            f.truncate(self._n * self.dim * 4)
            f.write(_pack([list(v) for v in new.values()]))
        with self._key_path.open("a", encoding="ascii") as f:
            f.write("".join(f"{k}\n" for k in new))
        for k, v in new.items():
            self._rows[k] = self._n
            self._n += 1
            self._data.extend(v)


OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
# 모델 id → dim. OpenRouter 목록(GET /api/v1/embeddings/models)에서 2026-11-12에 확인한 것만 둡니다.
# OpenAI 임베딩은 질문과 문서를 구분하지 않으므로 질문도 같은 함수로 바꿉니다(embed_queries 없음).
OPENROUTER_MODELS: dict[str, int] = {"openai/text-embedding-3-large": 3072, "openai/text-embedding-3-small": 1536}
OPENROUTER_BATCH = 64
OPENROUTER_RETRIES = 3  # 429·5xx·연결 실패에서 다시 보내는 횟수(1, 2, 4초 기다림)


class OpenRouterEmbedder:
    """뜻을 아는 벡터 함수(바깥 API): OpenRouter의 임베딩 엔드포인트(POST /api/v1/embeddings).

    키는 처음 보낼 때 환경 변수 OPENROUTER_API_KEY에서 읽고, 없으면 PaperStoreError. 한 요청에 글 여러 개(64개까지)를
    보냅니다. 429와 5xx, 연결 실패는 짧게 몇 번 다시 보내고, 그래도 안 되면 PaperStoreError.
    보내는 것은 논문 대목과 검색 질문뿐이고, 선택 헤더(HTTP-Referer, X-Title)는 보내지 않습니다.
    cache_dir을 주면 VectorCache로 이미 센 글을 다시 보내지 않습니다. transport는 시험에서 가짜 전송을 끼우는 자리입니다.
    """

    def __init__(
        self,
        model: str = "openai/text-embedding-3-large",
        *,
        cache_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 60.0,
        retry_wait_s: float = 1.0,
    ) -> None:
        if model not in OPENROUTER_MODELS:
            raise PaperStoreError(
                f"unknown OpenRouter embedding model {model!r} (known: {', '.join(OPENROUTER_MODELS)})"
            )
        self.model = model
        self.dim = OPENROUTER_MODELS[model]
        self.cache = VectorCache(cache_dir, self.name, self.dim) if cache_dir is not None else None
        self._transport = transport
        self._timeout_s = timeout_s
        self._retry_wait_s = retry_wait_s
        self.requests = 0  # 실제로 보낸 요청 수(재시도 포함). 캐시가 듣는지 볼 때 씀

    @property
    def name(self) -> str:
        return f"openrouter:{self.model}"

    def _post(self, client: httpx.Client, texts: list[str]) -> list[list[float]]:
        last = ""
        for attempt in range(OPENROUTER_RETRIES + 1):
            if attempt:
                time.sleep(self._retry_wait_s * 2 ** (attempt - 1))
            self.requests += 1
            try:
                r = client.post(OPENROUTER_URL, json={"model": self.model, "input": texts, "encoding_format": "float"})
            except httpx.TransportError as e:
                last = f"{type(e).__name__}"
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                continue
            if r.status_code != 200:
                raise PaperStoreError(f"embedder {self.name}: HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            if "error" in body:
                raise PaperStoreError(f"embedder {self.name}: {str(body['error'])[:300]}")
            rows = sorted(body["data"], key=lambda d: d["index"])
            vectors = [list(map(float, d["embedding"])) for d in rows]
            if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
                raise PaperStoreError(f"embedder {self.name} returned the wrong shape")
            return vectors
        raise PaperStoreError(f"embedder {self.name}: gave up after {OPENROUTER_RETRIES + 1} tries ({last})")

    def __call__(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float] | None] = [self.cache.get(t) if self.cache else None for t in texts]
        todo = [i for i, v in enumerate(out) if v is None]
        if not todo:
            return out  # type: ignore[return-value]
        key = os.environ.get(OPENROUTER_KEY_ENV)
        if not key:
            raise PaperStoreError(f"embedder {self.name} needs the environment variable {OPENROUTER_KEY_ENV}")
        headers = {"Authorization": f"Bearer {key}"}
        with httpx.Client(headers=headers, timeout=self._timeout_s, transport=self._transport) as client:
            for s in range(0, len(todo), OPENROUTER_BATCH):
                part = todo[s : s + OPENROUTER_BATCH]
                vectors = self._post(client, [texts[i] for i in part])
                if self.cache:
                    self.cache.put([texts[i] for i in part], vectors)
                for i, v in zip(part, vectors, strict=True):
                    out[i] = v
        return out  # type: ignore[return-value]


_NGRAM_NAME = re.compile(r"^ngram(\d+)-hash(\d+)-v1$")
_FASTEMBED_PREFIX = "fastembed:"
_OPENROUTER_PREFIX = "openrouter:"


def embedder_by_name(name: str | None, *, cache_dir: Path | None = None) -> Embedder:
    """이름으로 벡터 함수를 고릅니다. None이면 기본(NgramEmbedder()).

    ngram<n>-hash<dim>-v1 = 글자 n-gram 대역, fastembed:<모델> = FastEmbedEmbedder(선택 사항, 이 PC),
    openrouter:<모델> = OpenRouterEmbedder(바깥 API, 키 필요). cache_dir은 OpenRouter에만 씁니다.
    """
    if name is None:
        return NgramEmbedder()
    m = _NGRAM_NAME.match(name)
    if m:
        return NgramEmbedder(dim=int(m.group(2)), n=int(m.group(1)))
    if name.startswith(_FASTEMBED_PREFIX):
        return FastEmbedEmbedder(name.removeprefix(_FASTEMBED_PREFIX))
    if name.startswith(_OPENROUTER_PREFIX):
        return OpenRouterEmbedder(name.removeprefix(_OPENROUTER_PREFIX), cache_dir=cache_dir)
    raise PaperStoreError(
        f"unknown embedder {name!r} (ngram<n>-hash<dim>-v1, fastembed:<model> with one of {', '.join(FASTEMBED_MODELS)}, "
        f"or openrouter:<model> with one of {', '.join(OPENROUTER_MODELS)})"
    )


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """코사인 유사도. 어느 한쪽이 0 벡터이면 0."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def rank(query: Sequence[float], vectors: Sequence[Sequence[float]], k: int) -> list[tuple[int, float]]:
    """query에 가까운 순으로 (vectors의 위치, 점수)를 k개까지. 점수가 같으면 앞의 것이 먼저."""
    if k <= 0:
        return []
    scores = [cosine(query, v) for v in vectors]
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    return [(i, scores[i]) for i in order[:k]]


_TOKEN_RE = re.compile(r"[^\W_]+")


def tokenize(text: str) -> list[str]:
    """단어 맞춤(BM25)용 낱말: 소문자로 바꾸고 글자·숫자가 아닌 것(밑줄 포함)에서 자릅니다. 어간 줄이기는 하지 않습니다."""
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True, slots=True)
class Bm25:
    """대목들의 BM25 통계(RP-6). 색인을 읽을 때 대목 글에서 한 번 만들고 여러 번 찾는 데 다시 씁니다.

    idf = ln(1 + (N − df + 0.5) / (df + 0.5)) (늘 0보다 큼),
    점수 = Σ idf · tf·(k1+1) / (tf + k1·(1 − b + b·길이/평균 길이)). 질문의 같은 낱말은 한 번만 셉니다.
    """

    tf: list[dict[str, int]]  # 대목마다 낱말 → 나온 횟수
    lengths: list[int]
    df: dict[str, int]  # 낱말 → 그 낱말이 든 대목 수
    avgdl: float
    k1: float = 1.5
    b: float = 0.75

    @classmethod
    def build(cls, texts: Sequence[str]) -> "Bm25":
        tf: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for text in texts:
            counts: dict[str, int] = {}
            for t in tokenize(text):
                counts[t] = counts.get(t, 0) + 1
            tf.append(counts)
            for t in counts:
                df[t] = df.get(t, 0) + 1
        lengths = [sum(c.values()) for c in tf]
        avgdl = sum(lengths) / len(lengths) if lengths else 0.0
        return cls(tf, lengths, df, avgdl)

    def _score(self, terms: list[tuple[str, float]], i: int) -> float:
        counts, s = self.tf[i], 0.0
        norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avgdl) if self.avgdl else self.k1
        for term, idf in terms:
            f = counts.get(term, 0)
            if f:
                s += idf * f * (self.k1 + 1) / (f + norm)
        return s

    def rank(self, query: str, positions: Sequence[int]) -> list[tuple[int, float]]:
        """positions의 대목 가운데 질문 낱말이 하나라도 든 것만, 점수가 높은 순으로 (위치, 점수). 같으면 앞의 위치가 먼저."""
        n = len(self.tf)
        terms = [
            (t, math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5)))
            for t in dict.fromkeys(tokenize(query))
            if t in self.df
        ]
        if not terms:
            return []
        scored = [(i, self._score(terms, i)) for i in positions]
        return sorted(((i, s) for i, s in scored if s > 0), key=lambda x: (-x[1], x[0]))


RRF_C = 60


def fuse(rankings: Sequence[Sequence[int]], c: int = RRF_C) -> list[tuple[int, float]]:
    """여러 순위를 순위 역수의 합으로 합칩니다(RP-6): 점수 = Σ 1/(c + 순위), 순위는 1부터.

    어느 순위에 없는 항목은 그 순위의 몫이 없습니다. 점수가 같으면 위치(대목 순서)가 앞의 것이 먼저.
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for r, i in enumerate(ranking, 1):
            scores[i] = scores.get(i, 0.0) + 1 / (c + r)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


# ================================================================ 목록과 대목


@dataclass(frozen=True, slots=True)
class Paper:
    key: str
    title: str
    authors: list[str]
    year: int
    doi: str | None = None
    source: str | None = None  # source/ 안의 파일 이름. 없으면 아직 원문을 넣지 않음
    use: str = ""  # 쓸 수 있는 범위(사용 허락). 지금은 자유 글(SPEC 15 #10)


@dataclass(frozen=True, slots=True)
class Passage:
    id: str
    key: str
    page: int | None
    text: str


def default_root() -> Path:
    """<DATA_ROOT>/papers. 별도 설정 없이 데이터 뿌리에서 정합니다."""
    return settings.get().data_root / "papers"


def read_papers(root: Path) -> dict[str, Paper]:
    """papers.toml의 논문을 파일 순서대로. 파일이 없으면 빈 목록."""
    try:
        with (root / LIST_FILE).open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return {}
    out: dict[str, Paper] = {}
    for p in data.get("paper", []):
        out[p["key"]] = Paper(
            key=p["key"], title=p["title"], authors=list(p["authors"]), year=int(p["year"]),
            doi=p.get("doi"), source=p.get("source"), use=p.get("use", ""),
        )  # fmt: skip
    return out


def papers_version(root: Path) -> str:
    """papers.toml 바이트의 sha256(16진). 보고서 행의 papers_version(RP-5)."""
    return hashlib.sha256((root / LIST_FILE).read_bytes()).hexdigest()


def passages_path(root: Path, key: str) -> Path:
    return root / "passages" / f"{key}.jsonl"


def read_passages(root: Path, key: str) -> list[Passage]:
    """한 논문의 대목. 파일이 없으면 PaperStoreError."""
    path = passages_path(root, key)
    if not path.exists():
        raise PaperStoreError(f"{key}: no passages file ({path})")
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out.append(Passage(id=r["id"], key=key, page=r["page"], text=r["text"]))
    return out


def _toml_str(s: str) -> str:
    # JSON 문자열의 이스케이프(\" \\ \n \t \uXXXX …)는 TOML 기본 문자열에서도 같은 뜻입니다.
    return json.dumps(s, ensure_ascii=False)


def _list_bytes(papers: list[Paper]) -> bytes:
    lines = ["# 논문 목록 (SPEC RP-5). tools/papers.py가 통째로 다시 씁니다(손으로 단 주석은 남지 않음).", ""]
    for p in papers:
        lines += ["[[paper]]", f"key = {_toml_str(p.key)}", f"title = {_toml_str(p.title)}",
                  f"authors = [{', '.join(_toml_str(a) for a in p.authors)}]", f"year = {p.year}"]  # fmt: skip
        if p.doi is not None:
            lines.append(f"doi = {_toml_str(p.doi)}")
        if p.source is not None:
            lines.append(f"source = {_toml_str(p.source)}")
        lines += [f"use = {_toml_str(p.use)}", ""]
    return "\n".join(lines).encode()


def add_paper(root: Path, paper: Paper, text: str, source: Path | None = None, *, replace: bool = False) -> int:
    """논문 하나를 넣습니다(TL-10 add). 대목 수를 돌려줍니다.

    원문(있으면) → 글 → 대목 → 목록 순으로 쓰고, 목록을 마지막에 써서 목록에 있는 논문은 파일이 다 있게 합니다.
    replace에서 source를 주지 않으면 이미 있는 원문을 그대로 둡니다(영구 원본은 지우지 않음).
    """
    if not KEY_RE.match(paper.key):
        raise PaperStoreError(f"bad key {paper.key!r}: expected <first author surname lowercase><year>[letter]")
    listed = read_papers(root)
    old = listed.get(paper.key)
    if old is not None and not replace:
        raise PaperStoreError(f"{paper.key} is already in the list (use --replace to replace it)")
    pieces = split_passages(text)
    if not pieces:
        raise PaperStoreError(f"{paper.key}: the text has no passages")
    if source is not None:
        name = f"{paper.key}{source.suffix.lower()}"
        files.write_atomic_file(root / "source" / name, source.read_bytes())
    else:
        name = old.source if old is not None else None
    files.write_atomic_file(root / "text" / f"{paper.key}.txt", text.encode())
    rows = [
        json.dumps({"id": f"{paper.key}#{i}", "page": page, "text": t}, ensure_ascii=False)
        for i, (page, t) in enumerate(pieces, 1)
    ]
    files.write_atomic_file(passages_path(root, paper.key), ("\n".join(rows) + "\n").encode())
    new = Paper(paper.key, paper.title, paper.authors, paper.year, paper.doi, name, paper.use)
    listed[paper.key] = new
    files.write_atomic_file(root / LIST_FILE, _list_bytes(list(listed.values())))
    return len(pieces)


# ================================================================ 색인


def passages_digest(root: Path, keys: Sequence[str]) -> str:
    """색인이 만들어진 대목을 가리키는 sha256: key 순으로 key와 대목 파일 바이트를 이어 셈. 없는 파일도 표시."""
    h = hashlib.sha256()
    for key in sorted(keys):
        path = passages_path(root, key)
        h.update(f"{key}\n".encode())
        h.update(path.read_bytes() if path.exists() else b"\x00missing")
        h.update(b"\n\x00\n")
    return h.hexdigest()


def _index_dir(root: Path) -> Path:
    return root / "index"


def _pack(vectors: list[list[float]]) -> bytes:
    a = array("f", [x for v in vectors for x in v])
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def _unpack(data: bytes) -> array:
    """벡터 파일을 float32 배열 하나로. 대목마다 list로 풀지 않아 메모리를 아낍니다(행은 찾을 때만 꺼냄)."""
    a = array("f")
    a.frombytes(data)
    if sys.byteorder == "big":
        a.byteswap()
    return a


def _embed_passages(passages: Sequence[Passage], embedder: Embedder) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(passages), EMBED_BATCH):
        batch = [p.text for p in passages[i : i + EMBED_BATCH]]
        out = embedder(batch)
        if len(out) != len(batch) or any(len(v) != embedder.dim for v in out):
            raise PaperStoreError(f"embedder {embedder.name} returned the wrong shape")
        vectors += out
    return vectors


def build_index(root: Path, embedder: Embedder) -> int:
    """목록의 모든 논문 대목으로 색인을 (다시) 만듭니다. 대목 수를 돌려줍니다.

    vectors.f32를 먼저, meta.json을 마지막에 씁니다. meta에는 벡터 함수(이름, dim), 대목의 sha256,
    벡터 파일의 sha256, 대목 id 순서를 적어 낡거나 맞지 않는 색인을 알아봅니다.
    """
    keys = list(read_papers(root))
    passages = [p for key in keys for p in read_passages(root, key)]
    data = _pack(_embed_passages(passages, embedder))
    meta = {
        "format": INDEX_FORMAT,
        "embedder": embedder.name,
        "dim": embedder.dim,
        "passages_sha256": passages_digest(root, keys),
        "vectors_sha256": hashlib.sha256(data).hexdigest(),
        "ids": [p.id for p in passages],
    }
    files.write_atomic_file(_index_dir(root) / "vectors.f32", data)
    files.write_atomic_file(_index_dir(root) / "meta.json", json.dumps(meta, ensure_ascii=False).encode())
    return len(passages)


METHODS = ("hybrid", "bm25", "vector")


@dataclass(frozen=True, slots=True)
class Hit:
    """찾은 대목 하나. score의 뜻은 방식에 따름: hybrid는 순위 역수의 합, bm25는 BM25 점수, vector는 코사인.

    bm25_rank, vector_rank는 찾은 범위(keys) 안의 각 순위(1부터)이고, 그 순위에 없거나 그 방식을 쓰지 않았으면 None.
    """

    passage: Passage
    score: float
    bm25_rank: int | None = None
    vector_rank: int | None = None


@dataclass(frozen=True, slots=True)
class Index:
    """읽어 둔 색인. 한 번 읽으면 파일을 다시 읽지 않고 여러 번 찾을 수 있습니다(대화 하나에서 여러 번 찾기, RP-6).

    낡았는지는 읽을 때(load_index) 한 번 봅니다. embedder가 None이면 벡터로 찾을 때 embedder_name으로 고릅니다.
    """

    embedder_name: str
    dim: int
    keys: tuple[str, ...]  # 목록의 논문 key, 목록 순서
    passages: list[Passage]
    vectors: array = field(repr=False)  # float32, 대목 순서로 dim개씩
    bm25: Bm25 = field(repr=False)
    embedder: Embedder | None = field(default=None, repr=False, compare=False)

    def _positions(self, keys: Sequence[str] | None) -> list[int]:
        if keys is None:
            return list(range(len(self.passages)))
        unknown = [key for key in dict.fromkeys(keys) if key not in self.keys]
        if unknown:
            raise PaperStoreError(f"not in the paper list: {', '.join(unknown)}")
        wanted = set(keys)
        return [i for i, p in enumerate(self.passages) if p.key in wanted]

    def _vector_rank(self, query: str, pos: list[int]) -> list[tuple[int, float]]:
        e = self.embedder if self.embedder is not None else embedder_by_name(self.embedder_name)
        q = embed_queries(e, [query])[0]
        if len(q) != self.dim:
            raise PaperStoreError(f"embedder {e.name} returned the wrong shape")
        rows = [self.vectors[i * self.dim : (i + 1) * self.dim] for i in pos]
        return [(pos[j], s) for j, s in rank(q, rows, len(rows))]

    def search(
        self, query: str, k: int, *, keys: Sequence[str] | None = None, method: str = "hybrid"
    ) -> list[Hit]:
        """query에 맞는 대목 k개(RP-6).

        keys가 None이면 모든 논문, 빈 목록이면 아무것도 찾지 않음, 목록에 없는 key가 있으면 PaperStoreError.
        method: hybrid(BM25 순위와 벡터 순위를 fuse로 합침, 기본), bm25, vector.
        """
        if method not in METHODS:
            raise PaperStoreError(f"unknown search method {method!r} (one of {', '.join(METHODS)})")
        pos = self._positions(keys)
        if not pos or k <= 0:
            return []
        if method == "bm25":
            ranked = self.bm25.rank(query, pos)
            return [Hit(self.passages[i], s, r, None) for r, (i, s) in enumerate(ranked[:k], 1)]
        by_vector = self._vector_rank(query, pos)
        if method == "vector":
            return [Hit(self.passages[i], s, None, r) for r, (i, s) in enumerate(by_vector[:k], 1)]
        by_words = self.bm25.rank(query, pos)
        bm25_rank = {i: r for r, (i, _) in enumerate(by_words, 1)}
        vector_rank = {i: r for r, (i, _) in enumerate(by_vector, 1)}
        fused = fuse([[i for i, _ in by_words], [i for i, _ in by_vector]])
        return [Hit(self.passages[i], s, bm25_rank.get(i), vector_rank.get(i)) for i, s in fused[:k]]


def load_index(root: Path, embedder: Embedder | None) -> Index:
    """색인을 읽습니다. 없거나 낡았거나 깨졌으면 IndexProblem. embedder를 주면 같은 벡터 함수로 만든 것인지도 봅니다."""
    d = _index_dir(root)
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        data = (d / "vectors.f32").read_bytes()
    except FileNotFoundError:
        raise IndexProblem("no_index", f"no index in {d} (run: tools/papers.py build)") from None
    if meta.get("format") != INDEX_FORMAT:
        raise IndexProblem("bad_index", f"index format {meta.get('format')!r} is not {INDEX_FORMAT}")
    if embedder is not None and (meta["embedder"], meta["dim"]) != (embedder.name, embedder.dim):
        raise IndexProblem(
            "stale_index",
            f"index was built with embedder {meta['embedder']} (dim {meta['dim']}), not {embedder.name} "
            f"(dim {embedder.dim}); run build",
        )
    keys = list(read_papers(root))
    if meta["passages_sha256"] != passages_digest(root, keys):
        raise IndexProblem("stale_index", "index is stale: passages or the paper list changed since build; run build")
    if hashlib.sha256(data).hexdigest() != meta["vectors_sha256"] or len(data) != 4 * meta["dim"] * len(meta["ids"]):
        raise IndexProblem("bad_index", "index vectors file does not match meta.json; run build")
    passages = [p for key in keys for p in read_passages(root, key)]
    if [p.id for p in passages] != meta["ids"]:
        raise IndexProblem("stale_index", "index passage ids differ from the passages files; run build")
    bm25 = Bm25.build([p.text for p in passages])
    return Index(meta["embedder"], meta["dim"], tuple(keys), passages, _unpack(data), bm25, embedder)


def index_in_memory(root: Path, embedder: Embedder) -> Index:
    """디스크의 색인을 읽거나 쓰지 않고, 목록의 모든 대목으로 메모리에 색인을 만듭니다(평가 도구에서 벡터 함수를 견줄 때)."""
    keys = list(read_papers(root))
    passages = [p for key in keys for p in read_passages(root, key)]
    vectors = _unpack(_pack(_embed_passages(passages, embedder)))
    bm25 = Bm25.build([p.text for p in passages])
    return Index(embedder.name, embedder.dim, tuple(keys), passages, vectors, bm25, embedder)


def search(
    root: Path, keys: Sequence[str] | None, query: str, k: int, embedder: Embedder | None, method: str = "hybrid"
) -> list[Hit]:
    """색인을 읽어 한 번 찾습니다(RP-6). 여러 번 찾을 때는 load_index로 읽어 둔 Index.search를 씁니다.

    keys가 None이면 모든 논문에서 찾습니다. 빈 목록이면 아무것도 찾지 않습니다(sources가 빈 지표). 목록에 없는
    key가 있으면 PaperStoreError입니다: 지표 목록과 논문 목록이 어긋난 것이므로 근거 없이 넘어가지 않고 멈춥니다.
    """
    if keys is not None and not keys:
        return []
    return load_index(root, embedder).search(query, k, keys=keys, method=method)


# ================================================================ 검사 (TL-10 check, M10-3)


@dataclass(frozen=True, slots=True)
class Problem:
    kind: str  # missing_paper, no_passages, no_source, missing_source, no_index, stale_index, bad_index
    subject: str  # 논문 key 또는 "index"
    detail: str


def catalog_sources(catalog: Path) -> dict[str, list[str]]:
    """지표 목록의 [[metric]] 항목마다 id → sources. 다른 키는 보지 않습니다."""
    with catalog.open("rb") as f:
        data = tomllib.load(f)
    return {m["id"]: list(m.get("sources", [])) for m in data.get("metric", [])}


def check(root: Path, catalog: Path | None, embedder: Embedder | None = None) -> list[Problem]:
    """저장소가 지표 목록과 맞고 파일이 다 있는지. 문제 목록(비면 정상)."""
    listed = read_papers(root)
    problems: list[Problem] = []
    if catalog is not None:
        used: dict[str, list[str]] = {}
        for metric, sources in catalog_sources(catalog).items():
            for key in sources:
                used.setdefault(key, []).append(metric)
        for key, metrics in used.items():
            if key not in listed:
                problems.append(Problem("missing_paper", key, f"used by {', '.join(metrics)}"))
    for key, p in listed.items():
        try:
            if not read_passages(root, key):
                problems.append(Problem("no_passages", key, "passages file is empty"))
        except PaperStoreError as e:
            problems.append(Problem("no_passages", key, str(e)))
        if p.source is None:
            problems.append(Problem("no_source", key, "no original file recorded (add again with --source)"))
        elif not (root / "source" / p.source).exists():
            problems.append(Problem("missing_source", key, f"source/{p.source} is missing"))
    try:
        load_index(root, embedder)
    except IndexProblem as e:
        problems.append(Problem(e.kind, "index", str(e)))
    return problems

