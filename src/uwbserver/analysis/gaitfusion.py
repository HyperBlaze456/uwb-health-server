"""C2 HTTP 클라이언트(서버 → gaitfusion)와 multipart/form-data 부호기·해독기 (SPEC 7절).

요청과 응답이 같은 형식이라 부호기·해독기 하나를 서버, 가짜 gaitfusion, 시험이 같이 씁니다.
"""

import json
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class Part:
    name: str
    content_type: str
    data: bytes


# --- multipart/form-data (RFC 7578, RFC 2046) ---------------------------------

_CRLF = b"\r\n"
_BOUNDARY = re.compile(r"[0-9A-Za-z'()+_,\-./:=? ]{0,69}[0-9A-Za-z'()+_,\-./:=?]")  # RFC 2046: 끝은 공백 아님
_PARAM = re.compile(r';\s*([^\s=;]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;]*))')
_ESCAPE = re.compile(r"\\(.)")


def _header_value(value: str) -> tuple[str, dict[str, str]]:
    """'type; a=1; b="x y"' → ('type', {'a': '1', 'b': 'x y'}). 이름은 소문자."""
    main, sep, rest = value.partition(";")
    params: dict[str, str] = {}
    if sep:
        for m in _PARAM.finditer(";" + rest):
            quoted, bare = m.group(2), m.group(3)
            params[m.group(1).lower()] = _ESCAPE.sub(r"\1", quoted) if quoted is not None else bare.strip()
    return main.strip().lower(), params


def _collides(parts: Sequence[Part], boundary: bytes) -> bool:
    delim = b"--" + boundary
    return any(delim in p.data for p in parts)


def encode_multipart(parts: Sequence[Part], *, boundary: str | None = None) -> tuple[str, bytes]:
    """파트들 → (Content-Type 헤더 값, 본문). 순서 그대로, 같은 이름도 그대로.

    boundary를 주지 않으면 데이터에 없는 무작위 값을 고릅니다. 준 값이 데이터에 있으면 ValueError.
    """
    for p in parts:
        if any(c in p.name for c in '"\r\n\\') or not p.name:
            raise ValueError(f"bad part name {p.name!r}")
        if any(c in p.content_type for c in "\r\n") or not p.content_type:
            raise ValueError(f"bad content type {p.content_type!r}")
    if boundary is None:
        while True:
            boundary = secrets.token_hex(16)
            if not _collides(parts, boundary.encode()):
                break
    elif not _BOUNDARY.fullmatch(boundary):
        raise ValueError(f"bad boundary {boundary!r}")
    elif _collides(parts, boundary.encode()):
        raise ValueError("boundary occurs in part data")
    b = boundary.encode("ascii")
    out: list[bytes] = []
    for p in parts:
        head = f'Content-Disposition: form-data; name="{p.name}"\r\nContent-Type: {p.content_type}\r\n\r\n'
        out += [b"--", b, _CRLF, head.encode("utf-8"), p.data, _CRLF]
    out += [b"--", b, b"--", _CRLF]
    return f"multipart/form-data; boundary={boundary}", b"".join(out)


def _part_headers(raw: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    last = None
    for line in raw.decode("utf-8").split("\r\n"):
        if line[:1] in (" ", "\t") and last is not None:  # 옛 접힌 줄
            headers[last] += " " + line.strip()
            continue
        name, sep, value = line.partition(":")
        if not sep or not name.strip():
            raise ValueError(f"bad part header line {line!r}")
        last = name.strip().lower()
        headers[last] = value.strip()
    return headers


def decode_multipart(content_type: str, body: bytes) -> list[Part]:
    """multipart/form-data 본문 → 파트들(순서와 같은 이름 유지). 깨졌으면 ValueError.

    파트에 Content-Type이 없으면 text/plain(RFC 7578). 앞말(preamble)과 뒷말(epilogue)은 버림.
    """
    mtype, params = _header_value(content_type or "")
    if mtype != "multipart/form-data":
        raise ValueError(f"not multipart/form-data: {content_type!r}")
    boundary = params.get("boundary", "")
    if not _BOUNDARY.fullmatch(boundary):
        raise ValueError(f"bad or missing boundary in {content_type!r}")
    delim = b"--" + boundary.encode("ascii")
    body = bytes(body)
    size = len(body)

    if body.startswith(delim):
        pos = len(delim)
    else:
        i = body.find(_CRLF + delim)
        if i < 0:
            raise ValueError("no opening boundary")
        pos = i + 2 + len(delim)

    parts: list[Part] = []
    while True:
        if body.startswith(b"--", pos):
            return parts  # 닫는 경계. 뒷말은 버림
        while pos < size and body[pos] in b" \t":
            pos += 1
        if not body.startswith(_CRLF, pos):
            raise ValueError(f"part {len(parts)}: boundary line not followed by CRLF")
        pos += 2
        if body.startswith(_CRLF, pos):
            raw_headers, start = b"", pos + 2
        else:
            end = body.find(b"\r\n\r\n", pos)
            if end < 0:
                raise ValueError(f"part {len(parts)}: headers not terminated")
            raw_headers, start = body[pos:end], end + 4
        headers = _part_headers(raw_headers) if raw_headers else {}
        disposition, dparams = _header_value(headers.get("content-disposition", ""))
        if disposition != "form-data" or "name" not in dparams:
            raise ValueError(f"part {len(parts)}: needs Content-Disposition: form-data; name=...")
        nxt = body.find(_CRLF + delim, start)
        if nxt < 0:
            raise ValueError(f"part {len(parts)}: no closing boundary")
        parts.append(Part(dparams["name"], headers.get("content-type", "text/plain"), body[start:nxt]))
        pos = nxt + 2 + len(delim)


# --- C2 클라이언트 ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnalyzeReply:
    """status 200: parts(본문이 multipart로 안 풀리면 None과 reason). 400·422: reason(400은 chunk_index도).
    5xx: status만. 그 밖의 코드: status와 본문에서 읽은 reason(있으면)."""

    status: int
    parts: list[Part] | None
    reason: str | None
    chunk_index: int | None


def _error_body(r: httpx.Response) -> tuple[str, int | None]:
    """{"reason": ..., "chunk_index": n} → (reason, chunk_index). reason은 비지 않은 글자."""
    reason, index = None, None
    try:
        doc = r.json()
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        if isinstance(doc.get("reason"), str) and doc["reason"]:
            reason = doc["reason"]
        if type(doc.get("chunk_index")) is int:
            index = doc["chunk_index"]
    if reason is None:
        reason = r.text.strip()[:200] or f"http {r.status_code}"
    return reason, index


class Client:
    """C2 클라이언트. 시간 초과·연결 실패는 httpx 예외로 그대로 올립니다(부르는 쪽이 PL-2로 재시도).

    timeout_s: httpx 시간 한도(연결·읽기·쓰기 각각). gaitfusion은 다 계산한 뒤 한 번에 답하므로
    읽기 한도가 사실상 요청 하나의 한도(GAITFUSION_TIMEOUT_S)입니다.
    http: 시험에서 starlette TestClient(가짜 앱)를 넘기는 자리. 넘긴 클라이언트는 자기 시간 한도를 쓰고,
    이 객체가 닫지 않습니다.
    """

    def __init__(self, base_url: str, timeout_s: float, *, http: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._own = http is None
        self._http = http if http is not None else httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        if self._own:
            self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def version(self) -> dict:
        """GET /v1/version → {"gaitfusion_version": ..., "c2_version": ...}. 200이 아니면 httpx.HTTPStatusError."""
        r = self._http.get(f"{self._base}/v1/version")
        r.raise_for_status()
        doc = r.json()
        if not isinstance(doc, dict):
            raise ValueError(f"/v1/version returned {type(doc).__name__}, want object")
        return doc

    def analyze(self, context: dict, chunks: bytes) -> AnalyzeReply:
        """POST /v1/analyze. 파트 순서: context(application/json), chunks(application/octet-stream)."""
        ctx = json.dumps(context, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ctype, body = encode_multipart(
            [Part("context", "application/json", ctx), Part("chunks", "application/octet-stream", bytes(chunks))]
        )
        r = self._http.post(f"{self._base}/v1/analyze", content=body, headers={"content-type": ctype})
        status = r.status_code
        if status == 200:
            try:
                return AnalyzeReply(200, decode_multipart(r.headers.get("content-type", ""), r.content), None, None)
            except ValueError as e:
                return AnalyzeReply(200, None, f"multipart: {e}", None)  # check_response가 "multipart"로 거절
        if status >= 500:
            return AnalyzeReply(status, None, None, None)
        reason, index = _error_body(r)
        return AnalyzeReply(status, None, reason, index if status == 400 else None)
