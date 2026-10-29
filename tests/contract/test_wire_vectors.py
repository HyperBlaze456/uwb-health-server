"""C1 계약 시험: 펌웨어가 만든 시험 벡터(wire.md "시험 벡터")를 서버 해독기로 읽어 같은 값이 나오는지 봄.

벡터는 펌웨어 저장소의 tests/wire_vectors/를 tests/contract/wire_vectors/로 복사해 둡니다(SPEC 12절 M1).
아직 없으면 건너뜁니다. request_*.bin은 HTTP 서버가 필요하므로 여기서 보지 않습니다(intake 시험의 몫).

기대 JSON의 모양이 아직 확정되지 않았으므로 부분 비교합니다: 기대 JSON에 있는 키만 보고,
MAC은 "aa:bb:cc:dd:ee:ff"로 맞춘 뒤, 실수는 상대 오차 1e-6으로 비교합니다.
"""

import json
import math
import re
import time
from pathlib import Path

import pytest

from uwbserver.pipeline import wire

VECTOR_DIR = Path(__file__).parent / "wire_vectors"
DEVICE_MAC = wire.parse_mac("e0:72:a1:ac:a8:58")  # wire.md "시험 장치"
REL_TOL = 1e-6

_BINS = sorted(VECTOR_DIR.glob("*.bin")) if VECTOR_DIR.is_dir() else []
CHUNK_NAMES = [p.stem for p in _BINS if not p.stem.startswith(("request_", "ack_"))]
ACK_NAMES = [p.stem for p in _BINS if p.stem.startswith("ack_")]


def _params(names: list[str], what: str) -> list:
    if names:
        return names
    reason = f"no {what} vectors in {VECTOR_DIR} yet (firmware has not produced them)"
    return [pytest.param("-", marks=pytest.mark.skip(reason=reason))]


# --- 부분 비교 ----------------------------------------------------------------

_MAC = re.compile(r"[0-9a-fA-F]{2}([:-]?)[0-9a-fA-F]{2}(?:\1[0-9a-fA-F]{2}){4}")
_HEX = re.compile(r"(?:0[xX])?[0-9a-fA-F]+")


def _norm_str(s: str) -> str:
    if _MAC.fullmatch(s):
        return wire.format_mac(wire.parse_mac(s))
    if _HEX.fullmatch(s):
        return s.lower().removeprefix("0x")
    return s


def _flatten(x) -> list:
    if isinstance(x, (list, tuple)):
        return [y for e in x for y in _flatten(e)]
    return [x]


def assert_subset(expected, actual, path: str = "$") -> None:
    """expected에 있는 것만 actual에서 확인."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected an object, decoder gave {actual!r}"
        for k, v in expected.items():
            assert k in actual, f"{path}.{k}: missing in decoder output (keys: {sorted(actual)})"
            assert_subset(v, actual[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, (list, tuple)), f"{path}: expected a list, decoder gave {actual!r}"
        if len(expected) != len(actual):  # 행렬을 평평하게 적었을 수 있음
            expected, actual = _flatten(expected), _flatten(actual)
        assert len(expected) == len(actual), f"{path}: length {len(expected)} != {len(actual)}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            assert_subset(e, a, f"{path}[{i}]")
    elif expected is None or isinstance(expected, bool):
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"
    elif isinstance(expected, (int, float)):
        assert isinstance(actual, (int, float)), f"{path}: {actual!r} is not a number"
        if isinstance(expected, float) or isinstance(actual, float):
            ok = math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=1e-12)
            assert ok, f"{path}: {actual!r} != {expected!r} (rel_tol {REL_TOL})"
        else:
            assert actual == expected, f"{path}: {actual!r} != {expected!r}"
    elif isinstance(expected, str):
        if isinstance(actual, int) and not isinstance(actual, bool) and _HEX.fullmatch(expected):
            assert actual == int(expected.lower().removeprefix("0x"), 16), f"{path}: {actual:#x} != {expected}"
        else:
            assert isinstance(actual, str), f"{path}: {actual!r} is not a string"
            assert _norm_str(actual) == _norm_str(expected), f"{path}: {actual!r} != {expected!r}"
    else:
        raise AssertionError(f"{path}: unsupported expected value {expected!r}")


def _code(v) -> wire.Code:
    return wire.Code[v.upper()] if isinstance(v, str) else wire.Code(v)


def _status_by_name(status: dict) -> dict:
    """status 키가 kind 번호여도 이름으로."""
    out = {}
    for k, v in status.items():
        key = str(k)
        out[wire.KIND_NAMES[int(key)] if key.isdigit() else key] = v
    return out


def _now_us() -> int:
    return time.time_ns() // 1000


# --- 벡터 ---------------------------------------------------------------------


@pytest.mark.parametrize("name", _params(CHUNK_NAMES, "chunk"))
def test_chunk_vector(name):
    data = (VECTOR_DIR / f"{name}.bin").read_bytes()
    expected = json.loads((VECTOR_DIR / f"{name}.json").read_text(encoding="utf-8"))
    result = wire.check_chunk(data, device_mac=DEVICE_MAC, server_utc_us=_now_us())
    code = _code(expected["code"])

    if code == wire.Code.STORED:
        assert isinstance(result, wire.Chunk), f"{name}: expected stored, got {result}"
        actual = wire.chunk_to_dict(result)
        for key in ("header", "ranges", "imu_frames"):
            if key in expected:
                assert_subset(expected[key], actual[key], key)
        if "status" in expected:
            assert_subset(_status_by_name(expected["status"]), actual["status"], "status")
        if "crc32" in expected:
            assert_subset(expected["crc32"], result.crc32, "crc32")
        assert wire.decode_chunk(data, verify_crc=True) == result
    else:
        assert isinstance(result, wire.Rejection), f"{name}: expected {code.name}, got stored"
        assert result.code == code, f"{name}: {result}"
        if "check" in expected:
            assert result.check == expected["check"], f"{name}: {result}"


@pytest.mark.parametrize("name", _params(ACK_NAMES, "ack"))
def test_ack_vector(name):
    data = (VECTOR_DIR / f"{name}.bin").read_bytes()
    expected = json.loads((VECTOR_DIR / f"{name}.json").read_text(encoding="utf-8"))
    server_utc_us, codes = wire.decode_ack(data)
    actual = {
        "magic": data[0],
        "version": data[1],
        "count": len(codes),
        "server_utc_us": server_utc_us,
        "codes": codes,
    }
    if "codes" in expected:
        expected = expected | {"codes": [int(_code(c)) for c in expected["codes"]]}
    assert_subset(expected, actual)
    assert wire.encode_ack(server_utc_us, codes) == data


# --- 비교 도우미 자체 시험(벡터 없이도 돎) ----------------------------------------


def test_assert_subset_rules():
    assert_subset({"a": 1}, {"a": 1, "b": 2})
    assert_subset({"mac": "E0-72-A1-AC-A8-58"}, {"mac": "e0:72:a1:ac:a8:58"})
    assert_subset({"x": 0.1}, {"x": 0.10000000149011612})  # f32 → double
    assert_subset({"m": [1, 2, 3, 4]}, {"m": [[1, 2], [3, 4]]})
    assert_subset({"crc": "0xCBF43926"}, {"crc": 0xCBF43926})
    assert_subset({"firmware_id": "0123ABCD"}, {"firmware_id": "0123abcd"})
    assert_subset({"flag": True}, {"flag": True})
    for exp, act in (
        ({"a": 1}, {"b": 1}),
        ({"a": 1}, {"a": 2}),
        ({"x": 0.1}, {"x": 0.1001}),
        ({"l": [1, 2]}, {"l": [1, 2, 3]}),
        ({"s": "default"}, {"s": "Default"}),
    ):
        with pytest.raises(AssertionError):
            assert_subset(exp, act)


def test_code_names():
    assert _code(8) is wire.Code.BAD_FIELD
    assert _code("bad_field") is wire.Code.BAD_FIELD
    assert _status_by_name({"2": {}, "survey": {}}) == {"health": {}, "survey": {}}
