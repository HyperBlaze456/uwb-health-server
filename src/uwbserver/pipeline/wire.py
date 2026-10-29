"""C1: Head → 서버 묶음(wire v1) 해독기와 검사기.

바이트 계약은 uwb-firmware/docs/wire.md이고, 이 파일은 그것을 옮긴 순수 코드입니다.
DB·파일·HTTP를 모르고 표준 라이브러리만 씁니다. 필드 이름은 wire.md와 같습니다.

- check_chunk: 받기용. wire.md "서버의 검사" 2–9를 순서대로 하고 처음 실패한 단계를 돌려줌(IN-3).
- decode_chunk: 이미 받은 바이트(raw, archive, C2)용. 구조만 풂.
- decode_system / decode_health / decode_survey: 상태 블록 payload → JSON으로 줄 dict(AP-3).
"""

import enum
import re
import struct
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields

# --- 상수 (wire.md) ---------------------------------------------------------

MAGIC = 0xC1
ACK_MAGIC = 0xC3
ACK_VERSION = 1
HEADER_LEN = 48
CRC_LEN = 4
MIN_CHUNK_LEN = HEADER_LEN + CRC_LEN  # 52
MAX_CHUNK_LEN = 11_284
MAX_CHUNKS_PER_REQUEST = 16
MAX_BODY_BYTES = 131_072
SUPPORTED_VERSIONS = frozenset({1})

KIND_SYSTEM = 1
KIND_HEALTH = 2
KIND_SURVEY = 3
KIND_NAMES = {KIND_SYSTEM: "system", KIND_HEALTH: "health", KIND_SURVEY: "survey"}
BLOCK_LEN = {(1, 1): 152, (2, 1): 192, (3, 1): 304}  # (kind, kind_version) -> payload len

FRAMING_CODE = 255  # rejected 파일에서 HTTP 400 본문의 code (SPEC 5.2)

RANGE_LEN = 24
IMU_FRAME_LEN = 24
SAMPLE_LEN = 21
BLOCK_HEADER_LEN = 8
ACK_HEADER_LEN = 12
MAX_RANGE = 128
MAX_IMU_FRAMES = 32
MAX_SAMPLES = 10
MAX_OFFSET_US = 2_000_000  # rx_offset_us, at_offset_us 상한(미만)
UTC_MIN_US = 1_735_689_600_000_000  # 2025-01-01T00:00:00Z
UTC_AHEAD_US = 86_400_000_000  # 서버 시각 + 1일
AGE_NONE = 0xFFFF  # *_age_ms: 부팅 뒤 없음

HEALTH_COUNTERS = (
    "range_count", "invalid_range", "invalid_imu", "duplicate_rounds", "duplicate_imu",
    "uart_overflow", "at_errors", "at_timeouts", "imu_link_frames", "imu_link_samples",
    "imu_link_gaps", "imu_link_duplicates", "imu_link_rejected", "wifi_disconnects",
    "survey_rounds", "link_send_failures", "link_rejected", "link_foreign",
    "link_old_version", "chunks_dropped_overflow", "chunks_rejected", "upload_failures",
)  # fmt: skip


class Code(enum.IntEnum):
    """응답의 묶음별 결과 코드(wire.md "응답")."""

    STORED = 0
    DUPLICATE = 1
    CRC = 2
    MAGIC = 3
    VERSION = 4
    HEADER_LEN = 5
    TOO_LARGE = 6
    INCONSISTENT = 7
    BAD_FIELD = 8
    DEVICE_MISMATCH = 9


class FramingError(ValueError):
    """요청 프레이밍이 깨짐 → HTTP 400 (검사 1단계)."""


# --- 바이트 배치 -------------------------------------------------------------

_HEADER = struct.Struct("<BBHIIqqIHHHHHHHH")
_RANGE = struct.Struct("<IIBBBB4h4b")
_IMU = struct.Struct("<I6sIHIbBBB")
_SAMPLE = struct.Struct("<HIB7h")
_BLOCK = struct.Struct("<BBHI")
_U32 = struct.Struct("<I")
_U16 = struct.Struct("<H")
_KEY = struct.Struct("<4xII")  # head_boot_id, chunk_seq
_ACK = struct.Struct("<BBHq")
_SYSTEM = struct.Struct("<6s6BHH30sH16f8s32s")
_HEALTH = struct.Struct("<HBBHH22IHH" + "BBbBIIIHH" * 3 + "BbHIIHH" * 2)
_SURVEY = struct.Struct("<4B2H24s24s4f" + "hHH" * 16 + "16f2f12f4f")

if (
    _HEADER.size != HEADER_LEN
    or _RANGE.size != RANGE_LEN
    or _IMU.size != IMU_FRAME_LEN
    or _SAMPLE.size != SAMPLE_LEN
    or _BLOCK.size != BLOCK_HEADER_LEN
    or _ACK.size != ACK_HEADER_LEN
    or (_SYSTEM.size, _HEALTH.size, _SURVEY.size) != tuple(BLOCK_LEN.values())
):
    raise ImportError("wire.py: struct 배치가 wire.md 크기와 다름")

_ZERO_MAC = bytes(6)
_INF = float("inf")
_GEOMETRY_ID = re.compile(rb"[\x21-\x7e]{1,31}")
_WORD = re.compile(rb"[a-z0-9_]{1,23}")
_MAC_TEXT = re.compile(r"[0-9a-fA-F]{2}([:-]?)[0-9a-fA-F]{2}(?:\1[0-9a-fA-F]{2}){4}")


# --- 해독 결과 ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Header:
    magic: int
    version: int
    header_len: int
    head_boot_id: int
    chunk_seq: int
    head_us_start: int
    utc_us_start: int
    geometry_hash: int
    flags: int
    n_range: int
    n_imu_frames: int
    dropped_range: int
    dropped_imu_frames: int
    tag_mask: int
    status_len: int
    reserved: int


@dataclass(frozen=True, slots=True)
class RangeFrame:
    rx_offset_us: int
    round_seq: int
    slot_mask: int
    valid_mask: int  # byte 9 하위 4비트
    rssi_mask: int  # byte 9 상위 4비트
    report_flags: int
    tag_id: int
    raw_cm: tuple[int, int, int, int]
    rssi_dbm: tuple[int, int, int, int]

    @property
    def reported_mask(self) -> int:
        return self.report_flags & 0x0F

    @property
    def explicit_anchor_ids(self) -> bool:
        return bool(self.report_flags & 0x10)

    @property
    def configuration_verified(self) -> bool:
        return bool(self.report_flags & 0x20)


@dataclass(frozen=True, slots=True)
class ImuSample:
    seq: int
    tag_ms: int
    flags: int
    raw: tuple[int, ...]  # ax, ay, az, temp, gx, gy, gz


@dataclass(frozen=True, slots=True)
class ImuFrame:
    rx_offset_us: int
    tag_mac: bytes
    tag_boot_id: int
    link_seq: int
    tx_tag_ms: int
    link_rssi: int
    fs: int
    tag_id: int
    count: int
    samples: tuple[ImuSample, ...]


@dataclass(frozen=True, slots=True)
class StatusBlock:
    kind: int
    kind_version: int
    len: int
    at_offset_us: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class Chunk:
    header: Header
    ranges: tuple[RangeFrame, ...]
    imu_frames: tuple[ImuFrame, ...]
    blocks: tuple[StatusBlock, ...]
    crc32: int
    data: bytes  # 묶음 전체 바이트

    def block(self, kind: int) -> StatusBlock | None:
        for b in self.blocks:
            if b.kind == kind:
                return b
        return None

    @property
    def key(self) -> tuple[int, int]:
        """(head_boot_id, chunk_seq). device_id와 함께 중복 제거 키."""
        return (self.header.head_boot_id, self.header.chunk_seq)

    @property
    def status_mask(self) -> int:
        """bit0 system, bit1 health, bit2 survey (IN-7 intake.chunk.status_mask)."""
        m = 0
        for b in self.blocks:
            m |= 1 << (b.kind - 1)
        return m

    def first_imu(self, tag_id: int) -> ImuFrame | None:
        """그 Tag 자리의 첫 IMU 프레임(대표 tag_mac·tag_boot_id, IN-7)."""
        for f in self.imu_frames:
            if f.tag_id == tag_id:
                return f
        return None


@dataclass(frozen=True, slots=True)
class Rejection:
    code: Code
    check: int  # wire.md "서버의 검사" 단계 번호 2..9
    detail: str  # 걸린 필드, 예 "header.flags", "range[3].raw_cm", "len"


# --- 검사 2–7: 길이, magic, version, header_len, CRC, 구조 -------------------


class _Parsed:
    """구조 검사를 통과한 묶음의 struct 튜플들. 객체는 받은 묶음에만 만듦."""

    __slots__ = ("hdr", "ranges", "imus", "blocks", "crc")

    def __init__(self, hdr, ranges, imus, blocks, crc):
        self.hdr = hdr  # _HEADER 튜플
        self.ranges = ranges  # [_RANGE 튜플]
        self.imus = imus  # [(_IMU 튜플, [_SAMPLE 튜플])]
        self.blocks = blocks  # [(kind, kind_version, len, at_offset_us, payload)]
        self.crc = crc

    def payload(self, kind: int) -> bytes | None:
        for b in self.blocks:
            if b[0] == kind:
                return b[4]
        return None


def _precheck(data: bytes, *, check_crc: bool = True) -> Rejection | None:
    """검사 2–6."""
    n = len(data)
    if n < MIN_CHUNK_LEN:
        return Rejection(Code.INCONSISTENT, 2, "len")
    if n > MAX_CHUNK_LEN:
        return Rejection(Code.TOO_LARGE, 2, "len")
    if data[0] != MAGIC:
        return Rejection(Code.MAGIC, 3, "header.magic")
    if data[1] not in SUPPORTED_VERSIONS:
        return Rejection(Code.VERSION, 4, "header.version")
    if _U16.unpack_from(data, 2)[0] != HEADER_LEN:
        return Rejection(Code.HEADER_LEN, 5, "header.header_len")
    if check_crc and zlib.crc32(memoryview(data)[: n - CRC_LEN]) != _U32.unpack_from(data, n - CRC_LEN)[0]:
        return Rejection(Code.CRC, 6, "crc32")
    return None


def _parse(data: bytes) -> _Parsed | Rejection:
    """검사 7(구조). 2–5를 통과한 바이트만 받음."""
    mv = memoryview(data)
    end = len(data) - CRC_LEN
    hdr = _HEADER.unpack_from(data)
    n_range, n_imu, status_len = hdr[9], hdr[10], hdr[14]

    def bad(detail: str) -> Rejection:
        return Rejection(Code.INCONSISTENT, 7, detail)

    if n_range > MAX_RANGE:
        return bad("header.n_range")
    if n_imu > MAX_IMU_FRAMES:
        return bad("header.n_imu_frames")

    pos = HEADER_LEN + RANGE_LEN * n_range
    if pos > end:
        return bad("len")
    ranges = list(_RANGE.iter_unpack(mv[HEADER_LEN:pos]))

    imus = []
    for i in range(n_imu):
        if pos + IMU_FRAME_LEN > end:
            return bad("len")
        fh = _IMU.unpack_from(data, pos)
        count = fh[8]
        if not 1 <= count <= MAX_SAMPLES:
            return bad(f"imu[{i}].count")
        start = pos + IMU_FRAME_LEN
        pos = start + SAMPLE_LEN * count
        if pos > end:
            return bad("len")
        imus.append((fh, list(_SAMPLE.iter_unpack(mv[start:pos]))))

    # 48 + 24·n_range + Σ(24 + 21·count) + status_len + 4 = len
    # wire.md 모호: 7단계 안의 순서. 블록 종류를 몰라도 판정되는 inconsistent(전체 길이, 블록 경계·순서,
    # 아는 종류의 고정 len)를 먼저 보고, 그것이 모두 맞을 때만 모르는 kind·kind_version을 version으로 줌.
    if end - pos != status_len:
        return bad("header.status_len")

    blocks = []
    prev_kind = -1
    while pos < end:
        i = len(blocks)
        if end - pos < BLOCK_HEADER_LEN:
            return bad("header.status_len")  # 블록 길이의 합 ≠ status_len
        kind, kind_version, blen, at = _BLOCK.unpack_from(data, pos)
        if kind <= prev_kind:
            return bad(f"block[{i}].kind")  # kind 오름차순, 종류마다 한 번
        start = pos + BLOCK_HEADER_LEN
        pos = start + blen
        if pos > end:
            return bad(f"block[{i}].len")
        prev_kind = kind
        blocks.append((kind, kind_version, blen, at, data[start:pos]))

    for i, (kind, kind_version, blen, _, _) in enumerate(blocks):
        want = BLOCK_LEN.get((kind, kind_version))
        if want is not None and blen != want:
            return bad(f"block[{i}].len")
    for i, (kind, kind_version, _, _, _) in enumerate(blocks):
        if kind not in KIND_NAMES:
            return Rejection(Code.VERSION, 7, f"block[{i}].kind")
        if (kind, kind_version) not in BLOCK_LEN:
            return Rejection(Code.VERSION, 7, f"block[{i}].kind_version")

    crc = _U32.unpack_from(data, end)[0]
    return _Parsed(hdr, ranges, imus, blocks, crc)


# --- 검사 8: 필드 ------------------------------------------------------------
# 각 함수는 처음 걸린 필드 이름(str) 또는 None을 돌려줌.
# wire.md 모호: 여러 필드가 함께 틀리면 어느 것을 detail로 적을지. 코드는 같고(8), 순서는
# 머리 → 빈 묶음 → 거리 프레임 → IMU 프레임 → tag_mask 집합 → 블록(kind 순, 블록 안은 표 순서).


def _check_fields(p: _Parsed, server_utc_us: int) -> str | None:
    (_, _, _, _, _, head_us, utc_us, geometry_hash, flags, _, _, _, _, tag_mask, _, reserved) = p.hdr
    if head_us < 0:
        return "header.head_us_start"
    if flags & 1:
        # wire.md 모호: "서버 시각 + 1일 이전"을 미만(<)으로 읽음.
        if not UTC_MIN_US <= utc_us < server_utc_us + UTC_AHEAD_US:
            return "header.utc_us_start"
    elif utc_us != 0:
        return "header.utc_us_start"
    if flags & 0xFFFC:
        return "header.flags"
    if tag_mask & 0xFFFC:
        return "header.tag_mask"
    if reserved:
        return "header.reserved"
    if not p.ranges and not p.imus and not p.blocks:
        return "empty"  # Head가 만들지 않는 묶음

    seen = 0
    d = _check_ranges(p.ranges)
    if d is not None:
        return d
    for t in p.ranges:
        seen |= 1 << t[5]
    d = _check_imus(p.imus)
    if d is not None:
        return d
    for fh, _ in p.imus:
        seen |= 1 << fh[7]
    if seen != tag_mask:
        return "header.tag_mask"  # 프레임들의 tag_id 집합과 같아야 함

    for kind, _, _, at, payload in p.blocks:
        name = KIND_NAMES[kind]
        if at >= MAX_OFFSET_US:
            return f"{name}.at_offset_us"
        if kind == KIND_SYSTEM:
            d = _check_system(_SYSTEM.unpack(payload), geometry_hash)
        elif kind == KIND_HEALTH:
            d = _check_health(_HEALTH.unpack(payload))
        else:
            d = _check_survey(_SURVEY.unpack(payload))
        if d is not None:
            return d
    return None


def _check_ranges(rows: list[tuple]) -> str | None:
    # wire.md 모호: "앞 거리 프레임"을 tag_id와 무관하게 바로 앞 프레임으로 읽음(프레임은 도착 순서).
    prev = 0
    for i, (rx, _, _, masks, rf, tid, c0, c1, c2, c3, s0, s1, s2, s3) in enumerate(rows):
        if not prev <= rx < MAX_OFFSET_US:
            return f"range[{i}].rx_offset_us"
        prev = rx
        # 빠른 길: 네 칸 모두 reported·valid·rssi인 보통의 프레임
        if (
            masks == 0xFF
            and rf & 0xCF == 0x0F
            and tid < 2
            and 0 < c0 <= 3000
            and 0 < c1 <= 3000
            and 0 < c2 <= 3000
            and 0 < c3 <= 3000
            and s0 < 0
            and s1 < 0
            and s2 < 0
            and s3 < 0
        ):
            continue
        d = _check_range_slow(i, masks, rf, tid, (c0, c1, c2, c3), (s0, s1, s2, s3))
        if d is not None:
            return d
    return None


def _check_range_slow(i: int, masks: int, rf: int, tid: int, cm: tuple, rssi: tuple) -> str | None:
    reported = rf & 0x0F
    valid = masks & 0x0F
    rssi_mask = masks >> 4
    if valid & ~reported:
        return f"range[{i}].valid_mask"
    if rssi_mask & ~reported:
        return f"range[{i}].rssi_mask"
    if rf & 0xC0:
        return f"range[{i}].report_flags"
    if tid > 1:
        return f"range[{i}].tag_id"
    for k in range(4):
        bit = 1 << k
        if not reported & bit:
            if cm[k] != 0:
                return f"range[{i}].raw_cm"
        elif valid & bit and not 0 < cm[k] <= 3000:
            return f"range[{i}].raw_cm"  # 유효 = 0 초과 30 m 이하. reported지만 valid 아닌 칸은 무엇이든(포화 ±32767)
    for k in range(4):
        if rssi_mask & (1 << k):
            if rssi[k] >= 0:
                return f"range[{i}].rssi_dbm"
        elif rssi[k] != 0:
            return f"range[{i}].rssi_dbm"
    return None


def _check_imus(imus: list) -> str | None:
    prev = 0
    for i, ((rx, mac, _, _, _, link_rssi, fs, tid, _), samples) in enumerate(imus):
        if not prev <= rx < MAX_OFFSET_US:
            return f"imu[{i}].rx_offset_us"
        prev = rx
        if mac == _ZERO_MAC or mac[0] & 1:
            return f"imu[{i}].tag_mac"
        if link_rssi > 0:
            return f"imu[{i}].link_rssi"
        if fs & 0xCC:  # 각 4비트 ≤ 3
            return f"imu[{i}].fs"
        if tid > 1:
            return f"imu[{i}].tag_id"
        for j, s in enumerate(samples):
            if s[2] & 0xF8:
                return f"imu[{i}].samples[{j}].flags"
    return None


def _cstr(field: bytes, pattern: re.Pattern) -> bytes | None:
    """문자열 칸: 글자들 뒤는 모두 NUL. 맞으면 글자 바이트, 아니면 None."""
    n = field.find(0)
    if n < 0 or field.count(0, n) != len(field) - n:
        return None
    text = field[:n]
    return text if pattern.fullmatch(text) else None


def _check_system(v: tuple, header_geometry_hash: int) -> str | None:
    # head_mac은 검사 9(장치)에서 봄.
    _, tag_count, slot_ms, dims, flags, source, fs, acq_hz, transport_hz, members, reserved = v[:11]
    anchors = v[11:27]
    geometry_id = v[28]
    if tag_count not in (1, 2):
        return "system.tag_count"
    if not 1 <= slot_ms <= 100:
        return "system.slot_ms"
    if dims not in (2, 3):
        return "system.position_dimensions"
    if flags & 0xF0 or (flags >> 2) & 3 > 2:
        return "system.flags"  # bit4–7 = 0, imu_transport ≤ 2
    if source > 2:
        return "system.geometry_source"
    if fs & 0xCC:
        return "system.fs"
    if not 1 <= acq_hz <= 1000:
        return "system.imu_acquisition_hz"
    if transport_hz > 1000:
        return "system.imu_transport_hz"
    for k in range(0, 30, 6):
        m = members[k : k + 6]
        if m != _ZERO_MAC and m[0] & 1:
            return "system.set_members"
    if reserved:
        return "system.reserved"
    for k in range(4):
        x, y, z, bias = anchors[4 * k : 4 * k + 4]
        for name, val, lim in (("x", x, 100.0), ("y", y, 100.0), ("z", z, 100.0), ("range_bias_m", bias, 5.0)):
            if not -lim <= val <= lim:  # NaN·무한도 여기서 걸림
                return f"system.anchors[{k}].{name}"
    text = _cstr(geometry_id, _GEOMETRY_ID)
    if text is None:
        return "system.geometry_id"
    # wire.md 모호: CRC-32는 geometry_id 글자들(NUL 채움 제외)에 대해 셈.
    if zlib.crc32(text) != header_geometry_hash:
        return "header.geometry_hash"
    return None


def _check_health(v: tuple) -> str | None:
    flags, _, tag_count, used, capacity = v[:5]
    if flags & 0xFF00:
        return "health.flags"
    if tag_count not in (1, 2):
        return "health.tag_count"
    if used > capacity:
        return "health.buffer_used"
    if capacity < 1:
        return "health.buffer_capacity"
    for k in range(3):
        a = v[29 + 9 * k : 38 + 9 * k]
        d = _check_health_anchor(a)
        if d is not None:
            return f"health.anchors[{k}].{d}"
    for k in range(2):
        t = v[56 + 7 * k : 63 + 7 * k]
        d = _check_health_tag(t, tag_count == 1 and k == 1)
        if d is not None:
            return f"health.tags[{k}].{d}"
    return None


_ANCHOR_UNSEEN = (0, 0, 0, 0, 0, 0, 0, AGE_NONE, AGE_NONE)
_ANCHOR_NAMES = ("flags", "state", "rssi", "valid_mask", "boot_id", "range_count", "round_seq", "age_ms", "range_age_ms")
_TAG_NAMES = ("flags", "link_rssi", "imu_age_ms", "range_count", "round_seq", "range_age_ms", "reserved")


def _check_health_anchor(a: tuple) -> str | None:
    flags, state, rssi, valid_mask = a[:4]
    if flags & 0xE0:
        return "flags"
    if not flags & 1:
        # seen = 0이면 칸의 나머지 전부 0, 경과 칸만 0xFFFF.
        # wire.md 모호: "나머지"에 flags의 다른 비트도 넣음(flags 전체 = 0).
        for name, got, want in zip(_ANCHOR_NAMES, a, _ANCHOR_UNSEEN):
            if got != want:
                return name
    if state > 5:
        return "state"
    if rssi > 0:
        return "rssi"
    if valid_mask & 0xF0:
        return "valid_mask"
    return None


def _check_health_tag(t: tuple, single_tag_t1: bool) -> str | None:
    flags, link_rssi, imu_age, range_count, round_seq, range_age, reserved = t
    if flags & 0xFC:
        return "flags"
    if not flags & 1:
        if link_rssi != 0:
            return "link_rssi"
        if imu_age != AGE_NONE:
            return "imu_age_ms"
    if not flags & 2:
        if range_count != 0:
            return "range_count"
        if round_seq != 0:
            return "round_seq"
        if range_age != AGE_NONE:
            return "range_age_ms"
    elif single_tag_t1:
        return "flags"  # tag_count = 1이면 T1의 bit1 = 0
    if link_rssi > 0:
        return "link_rssi"
    if reserved:
        return "reserved"
    return None


def _nonneg(x: float) -> bool:
    return 0.0 <= x < _INF  # NaN·무한 거절


def _check_survey(v: tuple) -> str | None:
    # wire.md 모호: "내용" 열에만 있는 조건(solution.ok = 0이면 xyz_m = 0, height_solved = 0이면
    # solved_height_m = 0)은 검사하지 않음. wire.md가 서버의 검사를 "검사" 열로 정했기 때문.
    status, flags, heights_known, reserved = v[:4]
    message, reason = v[6], v[7]
    heights = v[8:12]
    meas = v[12:60]
    dist = v[60:76]
    max_asym, max_mad = v[76], v[77]
    xyz = v[78:90]
    fit_rms, flatness, solved_h, span = v[90:94]
    if status not in (1, 2):
        return "survey.status"
    if flags & 0xC0 or (status == 1 and not flags & 0x04):
        return "survey.flags"  # bit6–7 = 0, solved면 solution.ok = 1
    if heights_known & 0xF0:
        return "survey.heights_known"
    if reserved:
        return "survey.reserved"
    if _cstr(message, _WORD) is None:
        return "survey.message"
    if _cstr(reason, _WORD) is None:
        return "survey.solution_reason"
    for k, h in enumerate(heights):
        if not 0.0 <= h <= 10.0 or (not heights_known & (1 << k) and h != 0.0):
            return "survey.heights_m"
    for k in range(16):
        median, n, mad = meas[3 * k : 3 * k + 3]
        i, j = divmod(k, 4)
        if i == j:
            bad = median or n or mad  # i = j 칸은 0
        else:
            bad = n == 0 and (median or mad)  # n = 0이면 나머지 0
        if bad:
            return f"survey.measurements[{i}][{j}]"
    for x in dist:
        if not 0.0 <= x <= 100.0:
            return "survey.distances_m"
    if not _nonneg(max_asym):
        return "survey.max_asymmetry_m"
    if not _nonneg(max_mad):
        return "survey.max_mad_m"
    for x in xyz:
        if not -100.0 <= x <= 100.0:
            return "survey.xyz_m"
    if not _nonneg(fit_rms):
        return "survey.fit_rms_m"
    if not _nonneg(flatness):
        return "survey.flatness_m"
    if not -_INF < solved_h < _INF:
        return "survey.solved_height_m"
    if not _nonneg(span):
        return "survey.height_span_m"
    return None


# --- 공개 함수: 검사와 해독 ---------------------------------------------------

_STEP_CODE = {7: Code.INCONSISTENT, 8: Code.BAD_FIELD, 9: Code.DEVICE_MISMATCH}


def check_chunk(data: bytes, *, device_mac: bytes, server_utc_us: int) -> Chunk | Rejection:
    """wire.md "서버의 검사" 2–9를 순서대로, 처음 실패한 단계가 이김(IN-3). 10(중복)은 부르는 쪽.

    device_mac: 토큰으로 찾은 장치의 MAC 6 바이트. server_utc_us: utc_us_start 상한용.
    값을 고치지 않음. 어떤 바이트를 받아도 예외를 내지 않고 Rejection을 돌려줌.
    """
    device_mac = bytes(device_mac)
    if len(device_mac) != 6:
        raise ValueError("device_mac must be 6 bytes")
    data = bytes(data)
    rej = _precheck(data)
    if rej is not None:
        return rej
    step = 7
    try:
        p = _parse(data)
        if isinstance(p, Rejection):
            return p
        step = 8
        detail = _check_fields(p, server_utc_us)
        if detail is not None:
            return Rejection(Code.BAD_FIELD, 8, detail)
        step = 9
        system = p.payload(KIND_SYSTEM)
        if system is not None and system[:6] != device_mac:
            return Rejection(Code.DEVICE_MISMATCH, 9, "system.head_mac")
        return _build(data, p)
    except Exception as e:  # 해독기 버그의 안전망: 요청 전체를 503으로 막지 않고 거절 보관(IN-10으로 다시 넣음)
        return Rejection(_STEP_CODE[step], step, f"internal: {type(e).__name__}: {e}")


def decode_chunk(data: bytes, *, verify_crc: bool = False) -> Chunk:
    """이미 받은 바이트(raw, archive, C2)의 구조 해독(검사 2–5, 7). 필드 값은 보지 않음.

    깨졌으면 ValueError. verify_crc=True면 CRC(검사 6)도 봄.
    """
    data = bytes(data)
    rej = _precheck(data, check_crc=verify_crc)
    if rej is None:
        p = _parse(data)
        if isinstance(p, Rejection):
            rej = p
    if rej is not None:
        raise ValueError(f"malformed chunk: {rej.code.name.lower()} at check {rej.check} ({rej.detail})")
    return _build(data, p)


def _build(data: bytes, p: _Parsed) -> Chunk:
    ranges = tuple(
        [RangeFrame(t[0], t[1], t[2], t[3] & 0x0F, t[3] >> 4, t[4], t[5], t[6:10], t[10:14]) for t in p.ranges]
    )
    imus = tuple(
        [ImuFrame(*fh, tuple([ImuSample(s[0], s[1], s[2], s[3:]) for s in samples])) for fh, samples in p.imus]
    )
    blocks = tuple([StatusBlock(*b) for b in p.blocks])
    return Chunk(Header(*p.hdr), ranges, imus, blocks, p.crc, data)


def peek_key(data: bytes) -> tuple[int, int] | None:
    """(head_boot_id, chunk_seq). 12 바이트보다 짧으면 None. 다른 검사는 하지 않음."""
    if len(data) < 12:
        return None
    return _KEY.unpack_from(data)


# --- 상태 블록 → JSON dict (AP-3) ---------------------------------------------


def _need(payload: bytes, kind: int) -> None:
    want = BLOCK_LEN[(kind, 1)]
    if len(payload) != want:
        raise ValueError(f"{KIND_NAMES[kind]} payload must be {want} bytes, got {len(payload)}")


def _text(field: bytes) -> str:
    return field.split(b"\0", 1)[0].decode("ascii", "replace")


def decode_system(payload: bytes) -> dict:
    _need(payload, KIND_SYSTEM)
    v = _SYSTEM.unpack(payload)
    members = v[9]
    anchors = v[11:27]
    return {
        "head_mac": format_mac(v[0]),
        "tag_count": v[1],
        "slot_ms": v[2],
        "position_dimensions": v[3],
        "flags": v[4],
        "geometry_source": v[5],
        "fs": v[6],
        "imu_acquisition_hz": v[7],
        "imu_transport_hz": v[8],
        "set_members": [
            None if members[k : k + 6] == _ZERO_MAC else format_mac(members[k : k + 6]) for k in range(0, 30, 6)
        ],
        "reserved": v[10],
        "anchors": [
            {"x": anchors[k], "y": anchors[k + 1], "z": anchors[k + 2], "range_bias_m": anchors[k + 3]}
            for k in range(0, 16, 4)
        ],
        "firmware_id": v[27].hex(),
        "geometry_id": _text(v[28]),
    }


def decode_health(payload: bytes) -> dict:
    _need(payload, KIND_HEALTH)
    v = _HEALTH.unpack(payload)
    return {
        "flags": v[0],
        "wifi_disconnect_reason": v[1],
        "tag_count": v[2],
        "buffer_used": v[3],
        "buffer_capacity": v[4],
        "counters": dict(zip(HEALTH_COUNTERS, v[5:27], strict=True)),
        "range_age_ms": v[27],
        "imu_age_ms": v[28],
        "anchors": [dict(zip(_ANCHOR_NAMES, v[29 + 9 * k : 38 + 9 * k], strict=True)) for k in range(3)],
        "tags": [dict(zip(_TAG_NAMES, v[56 + 7 * k : 63 + 7 * k], strict=True)) for k in range(2)],
    }


def decode_survey(payload: bytes) -> dict:
    _need(payload, KIND_SURVEY)
    v = _SURVEY.unpack(payload)
    meas = v[12:60]
    dist = v[60:76]
    xyz = v[78:90]
    return {
        "status": v[0],
        "flags": v[1],
        "heights_known": v[2],
        "reserved": v[3],
        "survey_id": v[4],
        "revision": v[5],
        "message": _text(v[6]),
        "solution_reason": _text(v[7]),
        "heights_m": list(v[8:12]),
        "measurements": [
            [
                {"median_cm": meas[3 * k], "n": meas[3 * k + 1], "mad_cm": meas[3 * k + 2]}
                for k in range(4 * i, 4 * i + 4)
            ]
            for i in range(4)
        ],
        "distances_m": [list(dist[4 * i : 4 * i + 4]) for i in range(4)],
        "max_asymmetry_m": v[76],
        "max_mad_m": v[77],
        "xyz_m": [list(xyz[3 * i : 3 * i + 3]) for i in range(4)],
        "fit_rms_m": v[90],
        "flatness_m": v[91],
        "solved_height_m": v[92],
        "height_span_m": v[93],
    }


_DECODERS = {KIND_SYSTEM: decode_system, KIND_HEALTH: decode_health, KIND_SURVEY: decode_survey}


def decode_block(block: StatusBlock) -> dict:
    if (block.kind, block.kind_version) not in BLOCK_LEN:
        raise ValueError(f"unknown status block kind={block.kind} kind_version={block.kind_version}")
    return _DECODERS[block.kind](block.payload)


# --- 묶음 → dict (계약 시험, 도구) ---------------------------------------------

_HEADER_FIELDS = tuple(f.name for f in fields(Header))


def header_to_dict(h: Header) -> dict:
    return {name: getattr(h, name) for name in _HEADER_FIELDS}


def range_to_dict(r: RangeFrame) -> dict:
    return {
        "rx_offset_us": r.rx_offset_us,
        "round_seq": r.round_seq,
        "slot_mask": r.slot_mask,
        "valid_mask": r.valid_mask,
        "rssi_mask": r.rssi_mask,
        "report_flags": r.report_flags,
        "reported_mask": r.reported_mask,
        "explicit_anchor_ids": r.explicit_anchor_ids,
        "configuration_verified": r.configuration_verified,
        "tag_id": r.tag_id,
        "raw_cm": list(r.raw_cm),
        "rssi_dbm": list(r.rssi_dbm),
    }


def imu_frame_to_dict(f: ImuFrame) -> dict:
    return {
        "rx_offset_us": f.rx_offset_us,
        "tag_mac": format_mac(f.tag_mac),
        "tag_boot_id": f.tag_boot_id,
        "link_seq": f.link_seq,
        "tx_tag_ms": f.tx_tag_ms,
        "link_rssi": f.link_rssi,
        "fs": f.fs,
        "tag_id": f.tag_id,
        "count": f.count,
        "samples": [{"seq": s.seq, "tag_ms": s.tag_ms, "flags": s.flags, "raw": list(s.raw)} for s in f.samples],
    }


def chunk_to_dict(chunk: Chunk) -> dict:
    """{"header", "ranges", "imu_frames", "status": {종류 이름: 필드}, "crc32": "hex8"}.

    status의 각 dict에는 payload 필드에 더해 블록 머리의 kind_version, at_offset_us가 들어감.
    """
    return {
        "header": header_to_dict(chunk.header),
        "ranges": [range_to_dict(r) for r in chunk.ranges],
        "imu_frames": [imu_frame_to_dict(f) for f in chunk.imu_frames],
        "status": {
            KIND_NAMES[b.kind]: decode_block(b) | {"kind_version": b.kind_version, "at_offset_us": b.at_offset_us}
            for b in chunk.blocks
        },
        "crc32": f"{chunk.crc32:08x}",
    }


# --- 요청 프레이밍과 응답 -----------------------------------------------------


def split_request(body: bytes, *, max_chunks: int | None = MAX_CHUNKS_PER_REQUEST) -> list[bytes]:
    """u32 len + 묶음의 반복을 자름(검사 1). 묶음 내용은 보지 않음.

    본문이 비었거나, len이 남은 본문을 넘거나, len 4 바이트가 모자라거나, 묶음이 max_chunks를 넘으면
    FramingError. max_chunks=None은 한도 없음(C2, 파일 읽기).
    """
    n = len(body)
    if n == 0:
        raise FramingError("empty body")
    out: list[bytes] = []
    pos = 0
    while pos < n:
        if n - pos < 4:
            raise FramingError(f"record {len(out)}: {n - pos} trailing bytes, need 4 for len")
        (size,) = _U32.unpack_from(body, pos)
        pos += 4
        if size > n - pos:
            raise FramingError(f"record {len(out)}: len {size} > remaining {n - pos}")
        if max_chunks is not None and len(out) >= max_chunks:
            raise FramingError(f"more than {max_chunks} chunks")
        # wire.md 모호: len = 0인 레코드는 프레이밍 오류가 아님(검사 2에서 inconsistent).
        out.append(bytes(body[pos : pos + size]))
        pos += size
    return out


def join_records(chunks: Iterable[bytes]) -> bytes:
    """split_request의 반대: u32 len + 묶음을 이어 붙임."""
    parts = []
    for c in chunks:
        parts.append(_U32.pack(len(c)))
        parts.append(c)
    return b"".join(parts)


def encode_ack(server_utc_us: int, codes: Sequence[int]) -> bytes:
    """응답 본문: magic 0xC3, version 1, count, server_utc_us, codes."""
    body = bytes(codes)
    if len(body) > 0xFFFF:
        raise ValueError("too many codes")
    return _ACK.pack(ACK_MAGIC, ACK_VERSION, len(body), server_utc_us) + body


def decode_ack(data: bytes) -> tuple[int, list[int]]:
    if len(data) < ACK_HEADER_LEN:
        raise ValueError("ack shorter than 12 bytes")
    magic, version, count, server_utc_us = _ACK.unpack_from(data)
    if magic != ACK_MAGIC:
        raise ValueError(f"ack magic 0x{magic:02x}")
    if version != ACK_VERSION:
        raise ValueError(f"ack version {version}")
    if len(data) != ACK_HEADER_LEN + count:
        raise ValueError(f"ack length {len(data)} != 12 + count {count}")
    return server_utc_us, list(data[ACK_HEADER_LEN:])


# --- 작은 도우미 --------------------------------------------------------------


def crc32(data: bytes) -> int:
    """CRC-32 IEEE 802.3(zlib). 묶음과 TRJ1(SPEC 7.3)이 같이 씀."""
    return zlib.crc32(data) & 0xFFFFFFFF


def format_mac(b: bytes) -> str:
    if len(b) != 6:
        raise ValueError("MAC must be 6 bytes")
    return bytes(b).hex(":")


def parse_mac(s: str) -> bytes:
    """"aa:bb:cc:dd:ee:ff", "aa-bb-…", "aabbccddeeff"(대소문자 무관) → 6 바이트."""
    if not _MAC_TEXT.fullmatch(s):
        raise ValueError(f"not a MAC: {s!r}")
    return bytes.fromhex(s.replace(":", "").replace("-", ""))
