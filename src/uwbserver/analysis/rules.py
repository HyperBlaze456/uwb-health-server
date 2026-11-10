"""분석 호출 규칙 (AN-5, AN-7, AN-10, AP-4, SPEC 7절). 순수 계산.

- build_context, t_last_s: C2 요청 문맥(7.1절).
- check_response: C2 200 응답 검사(7.2·7.3절). 처음 어긴 규칙 하나를 ResponseInvalid로 냄.
- bout_rows, trajectory_t0: analysis.bout·trajectory 행 값.
- trj_header, channel_slice, decode_channel, read_channels: 저장된 TRJ1 파일 읽기(AP-4).
"""

import json
import math
import re
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from uwbserver.pipeline import wire

C2_VERSION = 1
TRJ_CHANNELS = (
    "px", "py", "pz", "vx", "vy", "vz", "roll_deg", "pitch_deg", "yaw_deg", "stance", "sig_px", "sig_py", "sig_pz",
)  # fmt: skip
TRJ_MAGIC = b"TRJ1"
TRJ_RATE_HZ = 100
TRJ_HEADER_LEN = 24
TRJ_MIN_LEN = TRJ_HEADER_LEN + 4  # n = 0일 때의 길이. 실제로는 n ≥ 1
TRJ_SIDES = ("left", "right")  # 머리 side 0, 1
SIDES = ("left", "right", "both")
MODES = ("fused", "imu_only")
SUBJECT_KEYS = ("height_m", "leg_length_m", "paretic_side")
RESULT_STATUSES = ("complete", "partial")  # result.status. partial = 마감 시각에 끊겨 궤적 일부가 빠짐

# 궤적 끝 시각(t0_s + (n − 1)/rate_hz)은 계산값이라 부동소수 오차가 생김. 1 µs(Head 시각의 단위)까지 봐줌.
TIME_EPS_S = 1e-6

# check_response가 내는 규칙 이름 전부. 순서 = 검사 순서.
RESPONSE_RULES = (
    "multipart",  # 응답 본문이 multipart로 풀리지 않음(클라이언트가 parts=None으로 줌)
    "parts.first",  # 첫 파트가 result가 아님
    "parts.name",  # 이름이 result도 traj-<idx>-<side>도 아님
    "parts.duplicate",  # 같은 이름이 둘
    "result.json",  # result가 JSON 객체가 아님
    "c2_version",
    "gaitfusion_version",
    "status",  # 있는데 complete·partial이 아님
    "summary",
    "bouts",  # 배열이 아님, 또는 항목이 객체가 아님
    "bouts.time",  # start_s·end_s가 유한한 수가 아님
    "bouts.range",  # 0 ≤ start_s < end_s ≤ t_last 아님
    "bouts.order",  # 앞 end_s < 다음 start_s 아님
    "bouts.mode",  # fused·imu_only가 아님, 또는 anchors 없이 fused
    "bouts.sides",  # left·right·both가 아님, 또는 구간 sides의 부분이 아님
    "bouts.quality",
    "bouts.trajectories",  # 배열이 아님, 또는 항목이 {side: str, part: str}가 아님
    "trajectories.side",  # 그 걷기 구간의 발이 아님, 또는 같은 발이 둘
    "trajectories.part",  # part ≠ traj-<그 bout 순번>-<side>
    "trajectories.missing",  # 항목이 가리키는 파트가 응답에 없음
    "trajectories.orphan",  # 어느 항목도 가리키지 않는 궤적 파트
    "trj.length",  # 28 B 미만, 또는 길이 ≠ 28 + 52n
    "trj.magic",
    "trj.n_channels",
    "trj.rate_hz",
    "trj.reserved",
    "trj.n",
    "trj.crc",
    "trj.side",  # 머리 side가 0·1이 아니거나 파트 이름의 발과 다름
    "trj.time",  # t0_s가 유한하지 않음, t0_s < 0, 또는 끝이 t_last 뒤
)

_TRJ_HEADER = struct.Struct("<4sBBHIId")  # magic, side, n_channels, rate_hz, n, reserved, t0_s
_U32 = struct.Struct("<I")
_TRAJ_PART = re.compile(r"traj-(0|[1-9][0-9]*)-(left|right)")


class ResponseInvalid(ValueError):
    """C2 200 응답이 7.2·7.3절을 어김(AN-7: 작업 failed, 재시도 안 함). rule은 RESPONSE_RULES의 하나."""

    def __init__(self, rule: str, detail: str = "") -> None:
        super().__init__(f"{rule}: {detail}" if detail else rule)
        self.rule = rule
        self.detail = detail


class PartLike(Protocol):
    name: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class Trj:
    """TRJ1 머리(7.3절). nbytes = 파트(파일) 전체 길이."""

    side: str
    rate_hz: int
    n: int
    t0_s: float
    nbytes: int

    @property
    def end_s(self) -> float:
        """마지막 표본 시각."""
        return self.t0_s + (self.n - 1) / self.rate_hz


@dataclass(frozen=True, slots=True)
class BoutOut:
    idx: int  # 응답 bouts 배열의 순번
    start_s: float
    end_s: float
    mode: str
    sides: str
    quality: dict
    trajectories: tuple[tuple[str, str], ...]  # (side, 파트 이름)


@dataclass(frozen=True, slots=True)
class Checked:
    gaitfusion_version: str
    result_status: str | None  # result.status. 없으면 None
    summary: dict
    bouts: tuple[BoutOut, ...]
    trajectories: dict[str, Trj]  # 파트 이름 → 머리
    result_bytes: bytes  # result 파트 원문(AN-10: 부르는 쪽이 gzip해 저장)


# --- 요청 문맥 (7.1절, AN-5) ---------------------------------------------------


def t_last_s(origin_head_us: int, last_head_us: int) -> float:
    """보낸 데이터가 끝나는 시각 = (마지막 묶음 head_us_start − origin_head_us)/10^6 + 1초(7.1절)."""
    if last_head_us < origin_head_us:
        raise ValueError("last_head_us < origin_head_us")
    return (last_head_us - origin_head_us) / 1_000_000 + 1.0


def build_context(
    *,
    segment_id: int,
    run_id: int,
    sides: str,
    origin_head_us: int,
    anchors: Sequence[Mapping] | None,
    subject: Mapping | None,
    params: Mapping,
) -> dict:
    """C2 context(7.1절). 키는 정확히 8개.

    anchors: segment.installation.anchors(surveyed일 때만), 아니면 None(IMU 단독).
    subject: 착용자 행(키 height_m·leg_length_m·paretic_side만 씀, 없는 키는 null), 없으면 None.
    params: ANALYSIS_PARAMS. 빠진 키는 서비스 기본값.
    """
    if sides not in SIDES:
        raise ValueError(f"sides must be one of {SIDES}, got {sides!r}")
    return {
        "c2_version": C2_VERSION,
        "segment_id": segment_id,
        "run_id": run_id,
        "origin_head_us": origin_head_us,
        "sides": sides,
        "anchors": None
        if anchors is None
        else [{"id": a["id"], "xyz_m": list(a["xyz_m"]), "bias_m": a["bias_m"]} for a in anchors],
        "subject": None if subject is None else {k: subject.get(k) for k in SUBJECT_KEYS},
        "params": dict(params),
    }


# --- 응답 검사 (7.2·7.3절) -----------------------------------------------------


def _finite(x: object) -> bool:
    """유한한 JSON 수. bool은 수가 아님."""
    if type(x) is float:
        return math.isfinite(x)
    if type(x) is int:
        try:
            return math.isfinite(float(x))
        except OverflowError:
            return False
    return False


def _us(s: float) -> int:
    return round(s * 1_000_000)


def parse_trj1(data: bytes) -> Trj:
    """TRJ1 파트 하나의 7.3절 검사(머리, 길이, CRC). 시각 범위(t_last)는 check_response에서 봄."""
    size = len(data)
    if size < TRJ_MIN_LEN:
        raise ResponseInvalid("trj.length", f"{size} bytes < {TRJ_MIN_LEN}")
    magic, side, n_channels, rate_hz, n, reserved, t0_s = _TRJ_HEADER.unpack_from(data)
    if magic != TRJ_MAGIC:
        raise ResponseInvalid("trj.magic", repr(magic))
    if n_channels != len(TRJ_CHANNELS):
        raise ResponseInvalid("trj.n_channels", str(n_channels))
    if rate_hz != TRJ_RATE_HZ:
        raise ResponseInvalid("trj.rate_hz", str(rate_hz))
    if reserved != 0:
        raise ResponseInvalid("trj.reserved", str(reserved))
    if n < 1:
        raise ResponseInvalid("trj.n", str(n))
    want = TRJ_MIN_LEN + 4 * len(TRJ_CHANNELS) * n
    if size != want:
        raise ResponseInvalid("trj.length", f"{size} bytes, n={n} needs {want}")
    if wire.crc32(memoryview(data)[: size - 4]) != _U32.unpack_from(data, size - 4)[0]:
        raise ResponseInvalid("trj.crc")
    if side >= len(TRJ_SIDES):
        raise ResponseInvalid("trj.side", f"side byte {side}")
    if not math.isfinite(t0_s):
        raise ResponseInvalid("trj.time", f"t0_s={t0_s}")
    return Trj(TRJ_SIDES[side], rate_hz, n, t0_s, size)


def check_response(
    parts: Sequence[PartLike] | None, *, segment_sides: str, anchors_sent: bool, t_last: float
) -> Checked:
    """7.2절 표의 규칙 전부와 7.3절 검사. 처음 어긴 규칙에서 ResponseInvalid.

    parts: 200 응답의 파트들(순서대로). None = 본문이 multipart로 풀리지 않음.
    segment_sides: 보낸 context.sides. anchors_sent: 보낸 context.anchors가 null이 아니었는지.
    t_last: t_last_s(…). 표에 없는 필드(strides 등)는 보지 않고 result_bytes에 그대로 남음.
    """
    if segment_sides not in SIDES:
        raise ValueError(f"segment_sides must be one of {SIDES}")
    if parts is None:
        raise ResponseInvalid("multipart", "200 body is not multipart/form-data")

    # 파트 이름
    if not parts or parts[0].name != "result":
        raise ResponseInvalid("parts.first", f"first part is {parts[0].name!r}" if parts else "no parts")
    traj_data: dict[str, bytes] = {}
    seen: set[str] = set()
    for p in parts:
        if p.name != "result" and not _TRAJ_PART.fullmatch(p.name):
            raise ResponseInvalid("parts.name", repr(p.name))
        if p.name in seen:
            raise ResponseInvalid("parts.duplicate", repr(p.name))
        seen.add(p.name)
        if p.name != "result":
            traj_data[p.name] = bytes(p.data)

    # result 머리 필드
    result_bytes = bytes(parts[0].data)
    try:
        doc = json.loads(result_bytes)
    except (ValueError, RecursionError) as e:
        raise ResponseInvalid("result.json", str(e)) from None
    if not isinstance(doc, dict):
        raise ResponseInvalid("result.json", f"top level is {type(doc).__name__}")
    v = doc.get("c2_version")
    if type(v) is not int or v != C2_VERSION:
        raise ResponseInvalid("c2_version", repr(v))
    version = doc.get("gaitfusion_version")
    if not isinstance(version, str) or version == "":
        raise ResponseInvalid("gaitfusion_version", repr(version))
    status = doc.get("status")  # 선택. 있으면 complete·partial(null도 틀림)
    if "status" in doc and not (isinstance(status, str) and status in RESULT_STATUSES):
        raise ResponseInvalid("status", repr(status))
    summary = doc.get("summary")
    if not isinstance(summary, dict):
        raise ResponseInvalid("summary", f"not an object: {type(summary).__name__}")
    bouts = doc.get("bouts")
    if not isinstance(bouts, list):
        raise ResponseInvalid("bouts", f"not an array: {type(bouts).__name__}")

    # 걷기 구간
    bout_sides_ok = SIDES if segment_sides == "both" else (segment_sides,)
    out: list[BoutOut] = []
    refs: list[str] = []
    prev_end: float | None = None
    for i, b in enumerate(bouts):
        where = f"bouts[{i}]"
        if not isinstance(b, dict):
            raise ResponseInvalid("bouts", f"{where} is not an object")
        start, end = b.get("start_s"), b.get("end_s")
        if not (_finite(start) and _finite(end)):
            raise ResponseInvalid("bouts.time", f"{where} start_s={start!r} end_s={end!r}")
        start, end = float(start), float(end)
        # start < end를 저장 단위(µs)로도 봄: DB CHECK (end_at > start_at)
        if not (0.0 <= start < end <= t_last) or _us(start) >= _us(end):
            raise ResponseInvalid("bouts.range", f"{where} [{start}, {end}] vs t_last={t_last}")
        if prev_end is not None and not prev_end < start:
            raise ResponseInvalid("bouts.order", f"{where} start_s={start} <= previous end_s={prev_end}")
        mode = b.get("mode")
        if not isinstance(mode, str) or mode not in MODES or (mode == "fused" and not anchors_sent):
            raise ResponseInvalid("bouts.mode", f"{where} mode={mode!r} anchors_sent={anchors_sent}")
        sides = b.get("sides")
        if not isinstance(sides, str) or sides not in bout_sides_ok:
            raise ResponseInvalid("bouts.sides", f"{where} sides={sides!r} segment sides={segment_sides!r}")
        quality = b.get("quality")
        if not isinstance(quality, dict):
            raise ResponseInvalid("bouts.quality", f"{where} not an object")
        trajs = b.get("trajectories")
        if not isinstance(trajs, list):
            raise ResponseInvalid("bouts.trajectories", f"{where} not an array")
        feet = TRJ_SIDES if sides == "both" else (sides,)
        entries: list[tuple[str, str]] = []
        for j, t in enumerate(trajs):
            if not (isinstance(t, dict) and isinstance(t.get("side"), str) and isinstance(t.get("part"), str)):
                raise ResponseInvalid("bouts.trajectories", f"{where}.trajectories[{j}] needs side and part strings")
            side = t["side"]
            if side not in feet or any(side == s for s, _ in entries):
                raise ResponseInvalid("trajectories.side", f"{where}.trajectories[{j}] side={side!r} sides={sides!r}")
            want = f"traj-{i}-{side}"
            if t["part"] != want:
                raise ResponseInvalid("trajectories.part", f"{where}.trajectories[{j}] part={t['part']!r} != {want!r}")
            entries.append((side, want))
            refs.append(want)
        out.append(BoutOut(i, start, end, mode, sides, quality, tuple(entries)))
        prev_end = end

    # 파트와 항목의 짝: 항목마다 파트 하나, 파트마다 항목 하나(이름이 bout 순번·발로 정해지므로 둘 이상은 못 가리킴)
    for name in refs:
        if name not in traj_data:
            raise ResponseInvalid("trajectories.missing", repr(name))
    referenced = set(refs)
    for name in traj_data:
        if name not in referenced:
            raise ResponseInvalid("trajectories.orphan", repr(name))

    # 궤적 파트 (7.3절)
    trjs: dict[str, Trj] = {}
    for name, data in traj_data.items():
        try:
            trj = parse_trj1(data)
        except ResponseInvalid as e:
            raise ResponseInvalid(e.rule, f"{name}: {e.detail}" if e.detail else name) from None
        if trj.side != name.rsplit("-", 1)[1]:
            raise ResponseInvalid("trj.side", f"{name}: header side {trj.side!r}")
        if trj.t0_s < 0 or trj.end_s > t_last + TIME_EPS_S:
            raise ResponseInvalid("trj.time", f"{name}: [{trj.t0_s}, {trj.end_s}] vs t_last={t_last}")
        trjs[name] = trj

    return Checked(version, status, summary, tuple(out), trjs, result_bytes)


# --- 행 값 ---------------------------------------------------------------------


def _at(origin_at: datetime, s: float) -> datetime:
    return origin_at + timedelta(microseconds=_us(s))


def bout_rows(checked: Checked, *, run_id: int, origin_at: datetime) -> list[dict]:
    """analysis.bout 행(id 제외). 시각 = origin_at + 응답의 초(µs로 반올림)."""
    return [
        {
            "run_id": run_id,
            "idx": b.idx,
            "start_at": _at(origin_at, b.start_s),
            "end_at": _at(origin_at, b.end_s),
            "mode": b.mode,
            "sides": b.sides,
            "quality": b.quality,
        }
        for b in checked.bouts
    ]


def trajectory_t0(origin_at: datetime, trj: Trj) -> datetime:
    """analysis.trajectory.t0 = origin_at + t0_s."""
    return _at(origin_at, trj.t0_s)


# --- 저장된 TRJ1 읽기 (AP-4) ---------------------------------------------------
# 파일은 저장 전에 parse_trj1을 통과했으므로 머리만 다시 확인하고 CRC는 보지 않음.


def trj_header(head: bytes) -> Trj:
    """TRJ1 파일의 앞 24 바이트 이상 → Trj. nbytes는 머리의 n으로 셈한 파일 길이."""
    if len(head) < TRJ_HEADER_LEN:
        raise ResponseInvalid("trj.length", f"header needs {TRJ_HEADER_LEN} bytes")
    magic, side, n_channels, rate_hz, n, _, t0_s = _TRJ_HEADER.unpack_from(head)
    if magic != TRJ_MAGIC:
        raise ResponseInvalid("trj.magic", repr(magic))
    if n_channels != len(TRJ_CHANNELS):
        raise ResponseInvalid("trj.n_channels", str(n_channels))
    if side >= len(TRJ_SIDES):
        raise ResponseInvalid("trj.side", f"side byte {side}")
    if rate_hz < 1 or n < 1:
        raise ResponseInvalid("trj.rate_hz" if rate_hz < 1 else "trj.n", f"rate_hz={rate_hz} n={n}")
    return Trj(TRJ_SIDES[side], rate_hz, n, t0_s, TRJ_MIN_LEN + 4 * len(TRJ_CHANNELS) * n)


def rate_step(rate_hz: int, want_hz: int) -> int:
    """want_hz로 줄이려면 몇 개마다 하나를 읽는지(100 → 25 = 4). 나누어떨어져야 함."""
    if want_hz < 1 or want_hz > rate_hz or rate_hz % want_hz:
        raise ValueError(f"cannot read {want_hz} Hz from {rate_hz} Hz")
    return rate_hz // want_hz


def stepped_n(n: int, step: int) -> int:
    """step마다 하나씩 읽은 표본 수(0, step, 2·step, …)."""
    return (n - 1) // step + 1


def channel_slice(trj_header_bytes: bytes, channels: Sequence[str], step: int) -> list[tuple[str, int, int]]:
    """채널마다 (이름, 파일 안 시작 바이트, 읽을 바이트 수). AP-4.

    채널 k는 24 + 4·n·k에서 시작합니다. step마다 하나를 쓰므로 마지막으로 쓰는 표본까지만 읽습니다.
    읽은 바이트는 decode_channel(raw, step)로 풉니다.
    """
    if step < 1:
        raise ValueError("step must be >= 1")
    trj = trj_header(trj_header_bytes)
    nbytes = 4 * ((stepped_n(trj.n, step) - 1) * step + 1)
    out = []
    for name in channels:
        try:
            k = TRJ_CHANNELS.index(name)
        except ValueError:
            raise ValueError(f"unknown channel {name!r}") from None
        out.append((name, TRJ_HEADER_LEN + 4 * trj.n * k, nbytes))
    return out


def decode_channel(raw: bytes, step: int, *, digits: int | None = None) -> list[float | None]:
    """f32 LE 배열에서 step마다 하나. NaN·무한은 None(JSON용). digits가 있으면 유효 숫자로 줄임."""
    count = len(raw) // 4
    values = struct.unpack_from(f"<{count}f", raw)[::step]
    if digits is None:
        return [v if math.isfinite(v) else None for v in values]
    fmt = f".{digits}g"
    return [float(format(v, fmt)) if math.isfinite(v) else None for v in values]


def read_channels(
    data: bytes, channels: Sequence[str] | None = None, step: int = 1, *, digits: int | None = None
) -> dict[str, list[float | None]]:
    """TRJ1 파일 바이트 전체 → {채널: 값}. channels가 None이면 13개 전부."""
    names = TRJ_CHANNELS if channels is None else channels
    mv = memoryview(data)
    return {
        name: decode_channel(mv[off : off + size], step, digits=digits)
        for name, off, size in channel_slice(data, names, step)
    }
