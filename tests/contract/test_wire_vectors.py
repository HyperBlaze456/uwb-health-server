"""C1 계약 시험: 펌웨어가 만든 시험 벡터(wire.md "시험 벡터")를 서버 해독기로 읽어 같은 값이 나오는지 봄.

벡터는 펌웨어 저장소의 tests/wire_vectors/를 tests/contract/wire_vectors/로 복사해 둡니다(SPEC 12절 1번).
어느 펌웨어 판에서 왔는지는 그 폴더의 VERSION에 적습니다. request_*.bin은 HTTP가 필요하므로
test_request_vectors.py가 봅니다.

wire.md "묶음 .json의 형식"이 정해졌으므로 완전히 같아야 합니다: 키와 그 순서, 배열 길이, 값.
f32는 배정도로 넓혀 수 값으로 비교합니다(wire.md: `==`, 형은 비교하지 않음).
"""

import json
from pathlib import Path

import pytest

from uwbserver.pipeline import wire

VECTOR_DIR = Path(__file__).parent / "wire_vectors"
DEVICE_MAC = wire.parse_mac("e0:72:a1:ac:a8:58")  # wire.md "시험이 가정하는 것"

_BINS = sorted(VECTOR_DIR.glob("*.bin")) if VECTOR_DIR.is_dir() else []
CHUNK_NAMES = [p.stem for p in _BINS if not p.stem.startswith(("request_", "ack_"))]
ACK_NAMES = [p.stem for p in _BINS if p.stem.startswith("ack_")]
CODE_NAMES = {c.value: c.name.lower() for c in wire.Code}


def _params(names: list[str], what: str) -> list:
    if names:
        return names
    return [pytest.param("-", marks=pytest.mark.skip(reason=f"{VECTOR_DIR}에 {what} 벡터가 아직 없음"))]


def _load(name: str) -> tuple[bytes, dict]:
    return (VECTOR_DIR / f"{name}.bin").read_bytes(), json.loads((VECTOR_DIR / f"{name}.json").read_text())


def assert_same(expected, actual, path: str = "$") -> None:
    """키 순서까지 같은지. 실패하면 처음 다른 곳의 경로를 보임."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: 객체여야 함, 해독기 {actual!r}"
        assert list(actual) == list(expected), f"{path}: 키 {list(actual)} != {list(expected)}"
        for k in expected:
            assert_same(expected[k], actual[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, (list, tuple)), f"{path}: 배열이어야 함, 해독기 {actual!r}"
        assert len(actual) == len(expected), f"{path}: 길이 {len(actual)} != {len(expected)}"
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            assert_same(e, a, f"{path}[{i}]")
    else:
        assert type(actual) is not bool or type(expected) is bool, f"{path}: {actual!r} != {expected!r}"
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


@pytest.mark.parametrize("name", _params(CHUNK_NAMES, "묶음"))
def test_chunk_vector(name: str) -> None:
    data, expected = _load(name)
    # 시험 벡터의 UTC는 2025년 6월(utc_floor·utc_too_early 제외)이라 오늘 시각으로 상한을 봐도 통과함
    result = wire.check_chunk(data, device_mac=DEVICE_MAC, server_utc_us=wire_now_us())
    if isinstance(result, wire.Rejection):
        got = {"code": int(result.code), "code_name": CODE_NAMES[result.code], "check": result.check}
        assert got == expected, f"{name}: {got} (detail {result.detail!r}) != {expected}"
        return
    assert expected["code"] == 0, f"{name}: 받아들였지만 기대는 {expected}"
    got = {"code": 0, "code_name": "stored", **wire.chunk_to_dict(result)}
    assert_same(expected, got)


@pytest.mark.parametrize("name", _params(ACK_NAMES, "응답"))
def test_ack_vector(name: str) -> None:
    data, expected = _load(name)
    server_us, codes = wire.decode_ack(data)
    assert {"magic": data[0], "version": data[1], "count": len(codes), "server_utc_us": server_us, "codes": codes} == expected
    assert wire.encode_ack(server_us, codes) == data


def test_version_file_pins_every_vector() -> None:
    """VERSION의 해시와 폴더의 파일이 하나도 빠짐없이 같은지(일부만 다시 복사한 경우를 잡음)."""
    import hashlib

    if not _BINS:
        pytest.skip("벡터가 아직 없음")
    version = VECTOR_DIR / "VERSION"
    assert version.exists(), "벡터를 복사할 때 VERSION(펌웨어 판)을 함께 둡니다(SPEC 12절 1번)"
    text = version.read_text()
    assert "firmware_commit:" in text
    listed = {}
    for line in text.split("files:\n", 1)[1].splitlines():
        digest, name = line.split()
        listed[name] = digest
    present = {p.name for p in VECTOR_DIR.iterdir() if p.suffix in (".bin", ".json")}
    assert set(listed) == present
    for name, digest in listed.items():
        assert hashlib.sha256((VECTOR_DIR / name).read_bytes()).hexdigest() == digest, name


def wire_now_us() -> int:
    import time

    return time.time_ns() // 1000
