"""가짜 gaitfusion (SPEC TL-2). C2(7절)를 지키는 시험용 서비스. DB는 모릅니다.

    uv run python tools/fake_gaitfusion.py --host 0.0.0.0 --port 8100 [--mode ok]

모드: create_app(mode) 인자, 없으면 환경 변수 FAKE_GAITFUSION_MODE, 기본 "ok".
  ok             받은 묶음을 해독해 걷기 구간과 궤적을 만듦. result.status = "complete"
  partial        ok처럼 하되 result.status = "partial", 궤적이 있는 마지막 걷기 구간에서 마지막 발의 궤적을 빼고
                 그 구간 quality.deadline = true(진짜 gaitfusion이 마감 시각 540초에 끊긴 경우)
  no_status      ok처럼 하되 result에 status가 없음(status를 보내기 전의 gaitfusion)
  422            {"reason": "fake_422"}
  500            본문 없는 500
  slow:<초>      그만큼 기다린 뒤 ok처럼
  malformed:<규칙>  ok 응답을 7.2·7.3절 규칙 하나만 어기게 고침(MALFORMED_RULES, 이름 = check_response의 rule)

걷기 구간: IMU 프레임이 있는 묶음이 Head 시각으로 이어진(다음 head_us_start − 앞 = 1초 ± 50 ms) 부분이 10초 이상이면
하나. 시각축 0 = context.origin_head_us. 발 = 그 부분에서 IMU 프레임이 있는 tag_id(0 오른쪽, 1 왼쪽).
궤적은 발마다 TRJ1 하나, 걷기 구간 ± bout_pad_s를 [0, t_last]로 자름. 값은 결정적인 합성값.
보폭 줄(strides, gaitfusion.stride/1, gaitfusion service.md 4절과 같은 필드): 발마다 1/STRIDE_HZ초 간격으로 걷기 구간
안에 들어가는 것만. 오른발은 반 주기 늦게 시작. 값은 합성 궤적과 맞춘 결정적인 값(속도 1.1 m/s, 디딤 절반).
uwb_support는 융합이면 그 구간의 quality.uwb_accept_ratio, IMU 단독이면 null. 줄은 시작 시각 순.
TRJ1 부호기는 서버의 rules.py와 따로 씁니다(서버의 해독기를 시험하려고).
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import struct
import zlib

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from uwbserver.analysis.gaitfusion import Part, decode_multipart, encode_multipart
from uwbserver.pipeline import wire

VERSION = {"gaitfusion_version": "fake-1.0", "c2_version": 1}
CHANNELS = (
    "px", "py", "pz", "vx", "vy", "vz", "roll_deg", "pitch_deg", "yaw_deg", "stance", "sig_px", "sig_py", "sig_pz",
)  # fmt: skip
RATE_HZ = 100
MIN_BOUT_S = 10.0
STEP_US = 1_000_000
STEP_TOLERANCE_US = 50_000
DEFAULT_PAD_S = 2.0
STRIDE_HZ = 0.9
SIDE_OF_TAG = {0: "right", 1: "left"}  # tag_id 0 = T0 오른쪽, 1 = T1 왼쪽
FEET = ("left", "right")  # 보고하는 순서. TRJ1 머리 side 값 0, 1(발 부호, tag_id가 아님)
SIDE_MASK = {"right": 0b01, "left": 0b10, "both": 0b11}  # 발 → tag_mask 비트(bit = 1 << tag_id)
CONTEXT_KEYS = {"c2_version", "segment_id", "run_id", "origin_head_us", "sides", "anchors", "subject", "params"}
SUBJECT_KEYS = {"height_m", "leg_length_m", "paretic_side"}

# malformed:<규칙>이 만드는 위반. 이름은 uwbserver.analysis.rules.RESPONSE_RULES와 같음.
MALFORMED_RULES = (
    "multipart",
    "parts.first",
    "parts.name",
    "parts.duplicate",
    "result.json",
    "c2_version",
    "gaitfusion_version",
    "status",
    "summary",
    "bouts",
    "bouts.time",
    "bouts.range",
    "bouts.order",
    "bouts.mode",
    "bouts.sides",
    "bouts.quality",
    "bouts.trajectories",
    "trajectories.side",
    "trajectories.part",
    "trajectories.missing",
    "trajectories.orphan",
    "trj.length",
    "trj.magic",
    "trj.n_channels",
    "trj.rate_hz",
    "trj.reserved",
    "trj.n",
    "trj.crc",
    "trj.side",
    "trj.time",
)

_TRJ_HEADER = struct.Struct("<4sBBHIId")


class BadRequest(Exception):
    """400: 요청이 C1이나 7절을 어김."""

    def __init__(self, reason: str, chunk_index: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.chunk_index = chunk_index


class Unanalyzable(Exception):
    """422: 분석 불가."""


def parse_mode(mode: str) -> tuple[str, float | str | None]:
    """'ok' | 'partial' | 'no_status' | '422' | '500' | 'slow:<초>' | 'malformed:<규칙>' → (종류, 값)."""
    if mode in ("ok", "partial", "no_status", "422", "500"):
        return mode, None
    kind, sep, arg = mode.partition(":")
    if sep and kind == "slow":
        seconds = float(arg)
        if not seconds >= 0:
            raise ValueError(f"slow needs seconds >= 0: {mode!r}")
        return kind, seconds
    if sep and kind == "malformed":
        if arg not in MALFORMED_RULES:
            raise ValueError(f"unknown malformed rule {arg!r}; one of {MALFORMED_RULES}")
        return kind, arg
    raise ValueError(f"unknown mode {mode!r}")


# --- 요청 해독 ---------------------------------------------------------------


def _is_int(x: object) -> bool:
    return type(x) is int


def _is_num(x: object) -> bool:
    return type(x) in (int, float) and math.isfinite(x)


def _check_context(ctx: object) -> dict:
    if not isinstance(ctx, dict):
        raise BadRequest("context: not an object")
    if set(ctx) != CONTEXT_KEYS:
        raise BadRequest(f"context: keys {sorted(ctx)} != {sorted(CONTEXT_KEYS)}")
    if ctx["c2_version"] != 1 or not _is_int(ctx["c2_version"]):
        raise BadRequest(f"context.c2_version: {ctx['c2_version']!r}")
    for k in ("segment_id", "run_id", "origin_head_us"):
        if not _is_int(ctx[k]) or ctx[k] < 0:
            raise BadRequest(f"context.{k}: {ctx[k]!r}")
    if ctx["sides"] not in SIDE_MASK:
        raise BadRequest(f"context.sides: {ctx['sides']!r}")
    anchors = ctx["anchors"]
    if anchors is not None:
        if not isinstance(anchors, list) or not anchors:
            raise BadRequest("context.anchors: not a non-empty array or null")
        for i, a in enumerate(anchors):
            ok = (
                isinstance(a, dict)
                and set(a) == {"id", "xyz_m", "bias_m"}
                and _is_int(a["id"])
                and isinstance(a["xyz_m"], list)
                and len(a["xyz_m"]) == 3
                and all(_is_num(v) for v in a["xyz_m"])
                and _is_num(a["bias_m"])
            )
            if not ok:
                raise BadRequest(f"context.anchors[{i}]: want {{id, xyz_m[3], bias_m}}")
    subject = ctx["subject"]
    if subject is not None:
        if not isinstance(subject, dict) or set(subject) != SUBJECT_KEYS:
            raise BadRequest(f"context.subject: keys must be {sorted(SUBJECT_KEYS)}")
        for k in ("height_m", "leg_length_m"):
            if subject[k] is not None and not _is_num(subject[k]):
                raise BadRequest(f"context.subject.{k}: {subject[k]!r}")
        if subject["paretic_side"] not in (None, "left", "right"):
            raise BadRequest(f"context.subject.paretic_side: {subject['paretic_side']!r}")
    if not isinstance(ctx["params"], dict):
        raise BadRequest("context.params: not an object")
    pad = ctx["params"].get("bout_pad_s")
    if pad is not None and (not _is_num(pad) or pad < 0):
        raise BadRequest(f"context.params.bout_pad_s: {pad!r}")
    return ctx


def decode_request(content_type: str, body: bytes) -> tuple[dict, list[wire.Chunk]]:
    """C2 요청 → (context, 묶음들). 어기면 BadRequest."""
    try:
        parts = decode_multipart(content_type, body)
    except ValueError as e:
        raise BadRequest(f"multipart: {e}") from None
    if [p.name for p in parts] != ["context", "chunks"]:
        raise BadRequest(f"parts must be context, chunks in order; got {[p.name for p in parts]}")
    try:
        ctx = _check_context(json.loads(parts[0].data))
    except ValueError as e:  # JSON이 아님
        raise BadRequest(f"context: {e}") from None
    try:
        records = wire.split_request(parts[1].data, max_chunks=None)
    except wire.FramingError as e:
        raise BadRequest(f"chunks framing: {e}") from None
    allowed = SIDE_MASK[ctx["sides"]]
    chunks: list[wire.Chunk] = []
    for i, rec in enumerate(records):
        try:
            c = wire.decode_chunk(rec, verify_crc=True)
        except ValueError as e:
            raise BadRequest(str(e), i) from None
        h = c.header
        if chunks:
            prev = chunks[-1].header
            if h.head_boot_id != prev.head_boot_id:
                raise BadRequest("head_boot_id differs from previous chunk", i)
            if h.chunk_seq <= prev.chunk_seq:
                raise BadRequest("chunk_seq not ascending", i)
        if h.tag_mask & ~allowed:
            raise BadRequest(f"tag_mask {h.tag_mask:#04b} outside context.sides {ctx['sides']!r}", i)
        chunks.append(c)
    if chunks[0].header.head_us_start != ctx["origin_head_us"]:
        raise BadRequest("context.origin_head_us != first chunk head_us_start", 0)
    return ctx, chunks


# --- 합성 분석 -----------------------------------------------------------------


def encode_trj1(side: str, t0_s: float, channels: list[list[float]]) -> bytes:
    """TRJ1(7.3절). channels = 13개 채널, 각 n개."""
    n = len(channels[0])
    if len(channels) != len(CHANNELS) or any(len(ch) != n for ch in channels) or n < 1:
        raise ValueError("need 13 channels of the same length >= 1")
    out = [_TRJ_HEADER.pack(b"TRJ1", FEET.index(side), len(CHANNELS), RATE_HZ, n, 0, t0_s)]
    out += [struct.pack(f"<{n}f", *ch) for ch in channels]
    data = b"".join(out)
    return data + struct.pack("<I", zlib.crc32(data))


def synth_channels(side: str, t0_s: float, n: int, bout_start_s: float) -> list[list[float]]:
    """결정적인 합성 궤적. 발목이 0.9 Hz로 흔들리며 앞으로 감. 첫 표본의 sig_*는 NaN(계산 못 함)."""
    phase0 = math.pi if side == "right" else 0.0
    lateral = 0.12 if side == "right" else -0.12
    w = 2 * math.pi * STRIDE_HZ
    ts = [t0_s + i / RATE_HZ for i in range(n)]
    sins = [math.sin(w * t + phase0) for t in ts]
    coss = [math.cos(w * t + phase0) for t in ts]
    rel = [max(0.0, t - bout_start_s) for t in ts]
    nan = float("nan")
    return [
        [1.1 * r - 0.08 * c for r, c in zip(rel, coss, strict=True)],  # px
        [lateral + 0.02 * s for s in sins],  # py
        [0.07 * max(0.0, s) for s in sins],  # pz
        [1.1 + 1.1 * s for s in sins],  # vx
        [0.02 * w * c for c in coss],  # vy
        [0.07 * w * c if s > 0 else 0.0 for s, c in zip(sins, coss, strict=True)],  # vz
        [3.0 * s for s in sins],  # roll_deg
        [25.0 * s for s in sins],  # pitch_deg
        [2.0 * math.sin(0.1 * t) for t in ts],  # yaw_deg
        [1.0 if s <= 0 else 0.0 for s in sins],  # stance
        [nan if i == 0 else 0.02 + 0.001 * r for i, r in enumerate(rel)],  # sig_px
        [nan if i == 0 else 0.02 + 0.001 * r for i, r in enumerate(rel)],  # sig_py
        [nan if i == 0 else 0.01 + 0.0005 * r for i, r in enumerate(rel)],  # sig_pz
    ]


def synth_strides(start: float, end: float, tag_ids: set[int], uwb_support: float | None) -> list[dict]:
    """결정적인 보폭 줄(gaitfusion.stride/1). 지표 목록(contracts/metrics.toml)의 범위 안의 값."""
    period = 1.0 / STRIDE_HZ
    rows = []
    for side in _feet(tag_ids):
        t = start + (period / 2 if side == "right" else 0.0)
        while t + period <= end + 1e-9:
            mid = t + period / 2
            rows.append({
                "side": side,
                "start_s": round(t, 6),
                "end_s": round(t + period, 6),
                "duration_s": round(period, 6),
                "length_m": round(1.1 * period, 6),
                "speed_mps": 1.1,
                "clearance_m": 0.07,
                "heading_deg": round(2.0 * math.sin(0.1 * mid), 6),
                "swing_s": round(period / 2, 6),
                "stance_s": round(period / 2, 6),
                "stance_ratio": 0.5,
                "uwb_support": uwb_support,
            })  # fmt: skip
            t += period
    return sorted(rows, key=lambda r: (r["start_s"], r["side"]))


def _walking_runs(chunks: list[wire.Chunk]) -> list[list[wire.Chunk]]:
    """IMU 프레임이 있는 묶음이 Head 시각으로 이어진 부분들."""
    runs: list[list[wire.Chunk]] = []
    cur: list[wire.Chunk] = []
    for c in chunks:
        if not c.imu_frames:
            cur = []
            continue
        if cur and abs(c.header.head_us_start - cur[-1].header.head_us_start - STEP_US) <= STEP_TOLERANCE_US:
            cur.append(c)
        else:
            cur = [c]
            runs.append(cur)
    return runs


def _feet(tag_ids: set[int]) -> list[str]:
    """tag_id들의 발, 왼발·오른발 순."""
    feet = {SIDE_OF_TAG[t] for t in tag_ids}
    return [s for s in FEET if s in feet]


def _sides_of(tag_ids: set[int]) -> str:
    return "both" if tag_ids == {0, 1} else SIDE_OF_TAG[min(tag_ids)]


def _bout(
    idx: int, start: float, end: float, tag_ids: set[int], run: list[wire.Chunk], ctx: dict, t_last: float
) -> tuple[dict, list[Part]]:
    pad = ctx["params"].get("bout_pad_s")
    pad = DEFAULT_PAD_S if pad is None else float(pad)
    mode = "fused" if ctx["anchors"] is not None else "imu_only"
    ranges = [r for c in run for r in c.ranges]
    ratio = None
    if mode == "fused" and ranges:
        ratio = round(sum(1 for r in ranges if r.valid_mask) / len(ranges), 4)
    t0 = max(0.0, start - pad)
    t_end = min(t_last, end + pad)
    n = math.floor((t_end - t0) * RATE_HZ + 1e-9) + 1
    while n > 1 and t0 + (n - 1) / RATE_HZ > t_last:
        n -= 1
    entries, parts = [], []
    for side in _feet(tag_ids):
        name = f"traj-{idx}-{side}"
        entries.append({"side": side, "part": name})
        parts.append(Part(name, "application/octet-stream", encode_trj1(side, t0, synth_channels(side, t0, n, start))))
    bout = {
        "start_s": start,
        "end_s": end,
        "mode": mode,
        "sides": _sides_of(tag_ids),
        "quality": {"uwb_accept_ratio": ratio},
        "trajectories": entries,
        "strides": synth_strides(start, end, tag_ids, ratio),
        "turns": [],
        "steps": [],
        "windows": [],
        "events": [],
    }
    return bout, parts


def analyze(ctx: dict, chunks: list[wire.Chunk], *, force_bout: bool = False) -> tuple[dict, list[Part], float]:
    """(result, 궤적 파트들, t_last). IMU가 전혀 없으면 Unanalyzable.

    force_bout: 걷기 구간이 없으면 [0, t_last] 전체를 하나로(malformed 모드가 고칠 대상이 필요할 때).
    """
    origin = ctx["origin_head_us"]
    t_last = (chunks[-1].header.head_us_start - origin) / 1_000_000 + 1.0
    imu_tags = {f.tag_id for c in chunks for f in c.imu_frames}
    if not imu_tags:
        raise Unanalyzable("no_imu")
    bouts: list[dict] = []
    parts: list[Part] = []
    for run in _walking_runs(chunks):
        start = (run[0].header.head_us_start - origin) / 1_000_000
        end = (run[-1].header.head_us_start - origin) / 1_000_000 + 1.0
        if end - start < MIN_BOUT_S or start < 0 or end > t_last:
            continue
        if bouts and start <= bouts[-1]["end_s"]:  # 7.2: 앞 end_s < 다음 start_s
            continue
        tags = {f.tag_id for c in run for f in c.imu_frames}
        bout, bparts = _bout(len(bouts), start, end, tags, run, ctx, t_last)
        bouts.append(bout)
        parts += bparts
    if force_bout and not bouts:
        bout, parts = _bout(0, 0.0, t_last, imu_tags, chunks, ctx, t_last)
        bouts.append(bout)
    seqs = [c.header.chunk_seq for c in chunks]
    result = {
        "c2_version": 1,
        "gaitfusion_version": VERSION["gaitfusion_version"],
        "status": "complete",
        "summary": {
            "n_imu": sum(f.count for c in chunks for f in c.imu_frames),
            "n_range": sum(len(c.ranges) for c in chunks),
            "seq_gaps": sum(1 for a, b in zip(seqs, seqs[1:], strict=False) if b != a + 1),
            "sides": _feet(imu_tags),
            "sensor_site": "lateral_ankle",
        },
        "bouts": bouts,
    }
    return result, parts, t_last


def cut_partial(result: dict, parts: list[Part]) -> list[Part]:
    """partial 모드: status = "partial", 궤적이 있는 마지막 걷기 구간에서 마지막 발의 궤적을 뺌 → 남은 궤적 파트들.

    궤적이 하나도 없으면 status만 바꿈. 걷기 구간 목록은 그대로(gaitfusion service.md 4절 "마감 시각").
    """
    result["status"] = "partial"
    for b in reversed(result["bouts"]):
        if b["trajectories"]:
            dropped = b["trajectories"].pop()
            b["quality"]["deadline"] = True
            return [p for p in parts if p.name != dropped["part"]]
    return parts


# --- 형식이 틀린 응답 -----------------------------------------------------------


def _patch_trj(data: bytes, **fields: object) -> bytes:
    """TRJ1 머리 필드를 바꾸고 CRC를 다시 셈(그 필드 하나만 틀리게)."""
    names = ("magic", "side", "n_channels", "rate_hz", "n", "reserved", "t0_s")
    values = dict(zip(names, _TRJ_HEADER.unpack_from(data), strict=True))
    values.update(fields)
    body = _TRJ_HEADER.pack(*(values[k] for k in names)) + data[_TRJ_HEADER.size : -4]
    return body + struct.pack("<I", zlib.crc32(body))


def _renamed(parts: list[Part], old: str, new: str) -> list[Part]:
    return [Part(new, p.content_type, p.data) if p.name == old else p for p in parts]


def malform(rule: str, ctx: dict, result: dict, parts: list[Part], t_last: float) -> tuple[bytes, list[Part]]:
    """ok 응답(result에 걷기 구간 ≥ 1, 첫 구간에 궤적 ≥ 1)을 rule 하나만 어기게 고침 → (result 바이트, 궤적 파트들).

    "multipart"와 "parts.first"는 파트 배치 문제라 respond()에서 처리.
    """
    bouts = result["bouts"]
    b0 = bouts[0]
    e0 = b0["trajectories"][0]
    p0 = e0["part"]
    b0_parts = {e["part"] for e in b0["trajectories"]}

    def patch_part(name: str, fn) -> list[Part]:
        return [Part(p.name, p.content_type, fn(p.data)) if p.name == name else p for p in parts]

    match rule:
        case "parts.name":
            parts = parts + [Part("debug", "text/plain", b"x")]
        case "parts.duplicate":
            parts = parts + [next(p for p in parts if p.name == p0)]
        case "result.json":
            return b'{"c2_version": 1, "bouts": [', parts
        case "c2_version":
            result["c2_version"] = 2
        case "gaitfusion_version":
            result["gaitfusion_version"] = ""
        case "status":
            result["status"] = "timeout"
        case "summary":
            result["summary"] = []
        case "bouts":
            result["bouts"] = {"0": b0}
            parts = []
        case "bouts.time":
            b0["end_s"] = None
        case "bouts.range":
            bouts[-1]["end_s"] = t_last + 1.0
        case "bouts.order":
            # 마지막 구간을 같은 시각으로 하나 더(이름만 새 순번으로). start_s가 앞 end_s보다 앞섬.
            last, k = bouts[-1], len(bouts) - 1
            copy = json.loads(json.dumps(last))
            for e in copy["trajectories"]:
                old = e["part"]
                e["part"] = f"traj-{k + 1}-{e['side']}"
                parts = parts + _renamed([p for p in parts if p.name == old], old, e["part"])
            bouts.append(copy)
        case "bouts.mode":
            b0["mode"] = "fused" if ctx["anchors"] is None else "walking"
        case "bouts.sides":
            b0["sides"] = {"left": "right", "right": "left"}.get(ctx["sides"], "middle")
        case "bouts.quality":
            b0["quality"] = 0.93
        case "bouts.trajectories":
            b0["trajectories"] = {e["side"]: e["part"] for e in b0["trajectories"]}
            parts = [p for p in parts if p.name not in b0_parts]
        case "trajectories.side":
            b0["trajectories"].append(dict(e0))
        case "trajectories.part":
            wrong = f"traj-{len(bouts) + 7}-{e0['side']}"
            e0["part"] = wrong
            parts = _renamed(parts, p0, wrong)
        case "trajectories.missing":
            parts = [p for p in parts if p.name != p0]
        case "trajectories.orphan":
            b0["trajectories"].remove(e0)
        case "trj.length":  # 표본 하나를 빼고 CRC는 맞게
            parts = patch_part(p0, lambda d: d[:-8] + struct.pack("<I", zlib.crc32(d[:-8])))
        case "trj.magic":
            parts = patch_part(p0, lambda d: _patch_trj(d, magic=b"TRJ0"))
        case "trj.n_channels":
            parts = patch_part(p0, lambda d: _patch_trj(d, n_channels=12))
        case "trj.rate_hz":
            parts = patch_part(p0, lambda d: _patch_trj(d, rate_hz=50))
        case "trj.reserved":
            parts = patch_part(p0, lambda d: _patch_trj(d, reserved=1))
        case "trj.n":
            parts = patch_part(p0, lambda d: _patch_trj(d, n=0))
        case "trj.crc":
            parts = patch_part(p0, lambda d: d[:-1] + bytes([d[-1] ^ 0xFF]))
        case "trj.side":
            other = 1 - FEET.index(e0["side"])
            parts = patch_part(p0, lambda d: _patch_trj(d, side=other))
        case "trj.time":
            parts = patch_part(p0, lambda d: _patch_trj(d, t0_s=-1.0))
        case _:
            raise ValueError(f"malform: unhandled rule {rule!r}")
    return _dumps(result), parts


def _dumps(doc: dict) -> bytes:
    return json.dumps(doc, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _multipart(result_bytes: bytes, parts: list[Part], seed: bytes, *, result_last: bool = False) -> tuple[str, bytes]:
    """결정적인 경계(요청 본문의 해시)로 응답 본문을 만듦."""
    all_parts = [Part("result", "application/json", result_bytes), *parts]
    if result_last:
        all_parts = all_parts[1:] + all_parts[:1]
    for k in range(100):
        boundary = "fake-" + hashlib.sha256(seed + bytes([k])).hexdigest()[:32]
        try:
            return encode_multipart(all_parts, boundary=boundary)
        except ValueError:
            continue
    return encode_multipart(all_parts)


# --- 앱 -------------------------------------------------------------------------


def respond(mode: str, content_type: str, body: bytes) -> Response:
    """/v1/analyze 하나의 응답(slow의 기다림은 부르는 쪽). 같은 입력이면 같은 바이트."""
    kind, arg = parse_mode(mode)
    if kind == "500":
        return Response(status_code=500)
    try:
        ctx, chunks = decode_request(content_type, body)
    except BadRequest as e:
        return JSONResponse({"reason": e.reason, "chunk_index": e.chunk_index}, status_code=400)
    if kind == "422":
        return JSONResponse({"reason": "fake_422"}, status_code=422)
    try:
        result, parts, t_last = analyze(ctx, chunks, force_bout=kind == "malformed")
    except Unanalyzable as e:
        return JSONResponse({"reason": str(e)}, status_code=422)

    seed = hashlib.sha256(body).digest()
    if kind == "partial":
        parts = cut_partial(result, parts)
    elif kind == "no_status":
        del result["status"]
    if kind == "malformed" and arg not in ("multipart", "parts.first"):
        result_bytes, parts = malform(str(arg), ctx, result, parts, t_last)
    else:
        result_bytes = _dumps(result)
    ctype, out = _multipart(result_bytes, parts, seed, result_last=arg == "parts.first")
    if arg == "multipart":
        out = out[: -len(b"--\r\n")]  # 닫는 경계의 "--"를 뺌
    return Response(content=out, media_type=ctype)


def create_app(mode: str | None = None) -> FastAPI:
    mode = mode if mode is not None else os.environ.get("FAKE_GAITFUSION_MODE", "ok")
    kind, arg = parse_mode(mode)  # 틀린 모드는 시작할 때 실패
    app = FastAPI(title="fake gaitfusion", version=VERSION["gaitfusion_version"])
    app.state.mode = mode

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "mode": mode}

    @app.get("/v1/version")
    def version() -> dict:
        return dict(VERSION)

    @app.post("/v1/analyze")
    async def analyze_endpoint(request: Request) -> Response:
        body = await request.body()
        if kind == "slow":
            await asyncio.sleep(float(arg))
        return respond(mode, request.headers.get("content-type", ""), body)

    return app


app = create_app()


def main() -> None:
    ap = argparse.ArgumentParser(description="가짜 gaitfusion (C2, SPEC TL-2)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument(
        "--mode",
        default=None,
        help="ok | partial | no_status | 422 | 500 | slow:<s> | malformed:<rule> (기본: FAKE_GAITFUSION_MODE 또는 ok)",
    )
    args = ap.parse_args()
    import uvicorn

    uvicorn.run(create_app(args.mode), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
