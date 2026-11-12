"""Fake Head (A0) for the UWB health server: wire v1 encoder, Head simulator, uploader, scenarios and CLI.

This is a second, independent implementation of uwb-firmware/docs/wire.md (SPEC TL-1). It must not import
`uwbserver`: the server decoder and this encoder are written separately and have to agree byte for byte.
Only the standard library is used; httpx is imported inside the CLI sender.

File layout:
  1. wire v1 constants and struct layouts
  2. encoders (one per structure) and small readers (request framing, ack, chunk header)
  3. FakeHead: one simulated Head, second by second (range frames, IMU frames, status blocks)
  4. Uploader: the Head ring buffer and resend rules in simulated time -> list of Request
  5. scenarios (SPEC 11, M1/M2) and wrong chunks (wire.md reject vectors)
  6. CLI: send a scenario to a running intake, fast or in real time

Usage: uv run python tools/fake_head.py --help
"""

import argparse
import datetime as dt
import inspect
import math
import random
import struct
import sys
import threading
import time
import zlib
from collections import Counter, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

# --------------------------------------------------------------------------------------------------------------
# 1. wire v1 constants and layouts
# --------------------------------------------------------------------------------------------------------------

CHUNK_MAGIC = 0xC1
WIRE_VERSION = 1
HEADER_LEN = 48
ACK_MAGIC = 0xC3
ACK_VERSION = 1
MIN_CHUNK_LEN = 52                      # header + CRC
MAX_CHUNK_LEN = 11_284
MAX_REQUEST_CHUNKS = 16
MAX_REQUEST_BYTES = 131_072

KIND_SYSTEM, KIND_HEALTH, KIND_SURVEY = 1, 2, 3
PAYLOAD_LEN = {KIND_SYSTEM: 152, KIND_HEALTH: 192, KIND_SURVEY: 304}

FLAG_UTC = 0x1                          # header flags bit0: UTC estimate present
FLAG_OVERFLOW = 0x2                     # header flags bit1: Head buffer dropped chunks before this one

IMU_FS = 3 | (2 << 4)                   # AFS_SEL 3 (+/-16 g), FS_SEL 2 (+/-1000 dps), uwb-firmware WalkConfig.h
ACCEL_LSB_PER_G = 2048.0
GYRO_LSB_PER_DPS = 32.8
SAMPLE_FLAGS = 0b101                    # normal read + boot gyro bias check passed
FIRMWARE_ID = bytes.fromhex("5ad3c0ffee01a2b3")

# Health counter positions (wire.md "counters 순서").
C_RANGE_COUNT, C_IMU_LINK_FRAMES, C_IMU_LINK_SAMPLES, C_IMU_LINK_GAPS = 0, 8, 9, 10
C_CHUNKS_DROPPED_OVERFLOW, C_CHUNKS_REJECTED, C_UPLOAD_FAILURES = 19, 20, 21

UTC_FLOOR_US = int(dt.datetime(2025, 1, 1, tzinfo=dt.UTC).timestamp()) * 1_000_000
DEFAULT_START_UTC_US = int(dt.datetime(2026, 10, 26, tzinfo=dt.UTC).timestamp()) * 1_000_000
DEFAULT_HEAD_US0 = 5_000_000
DEFAULT_UTC_OFFSET_US = DEFAULT_START_UTC_US - DEFAULT_HEAD_US0   # Head window 0 starts at DEFAULT_START_UTC_US

TEST_DEVICE_MAC = "e0:72:a1:ac:a8:58"   # wire.md test device
# x, y, z, range_bias_m per anchor A0-A3 (A0 = the Head). A 4 x 3.2 m room.
DEFAULT_ANCHORS = [(0.0, 0.0, 1.2, 0.0), (4.1, 0.0, 1.2, 0.0), (3.9, 3.2, 1.2, 0.0), (0.1, 3.1, 0.4, 0.0)]

HEADER = struct.Struct("<BBHIIqqIHHHHHHHH")             # 48 B
RANGE = struct.Struct("<IIBBBB4h4b")                    # 24 B
IMU_FRAME = struct.Struct("<I6sIHIbBBB")                # 24 B, then count samples
SAMPLE = struct.Struct("<HIB7h")                        # 21 B
_SAMPLE_HEAD = struct.Struct("<HIB")                    # first 7 B of a sample (raw packed separately)
_RAW7 = struct.Struct("<7h")
BLOCK = struct.Struct("<BBHI")                          # 8 B block header
SYSTEM = struct.Struct("<6sBBBBBBHH30sH16f8s32s")        # 152 B
HEALTH = struct.Struct("<HBBHH22IHH60s32s")             # 192 B
ANCHOR_SLOT = struct.Struct("<BBbBIIIHH")               # 20 B
TAG_SLOT = struct.Struct("<BbHIIHH")                    # 16 B
SURVEY = struct.Struct("<BBBBHH24s24s4f" + "hHH" * 16 + "16f2f12f4f")   # 304 B
ACK = struct.Struct("<BBHq")                            # 12 B, then count codes
_U32 = struct.Struct("<I")

ACK_CODES = {0: "stored", 1: "duplicate", 2: "crc", 3: "magic", 4: "version", 5: "header_len",
             6: "too_large", 7: "inconsistent", 8: "bad_field", 9: "device_mismatch"}


# --------------------------------------------------------------------------------------------------------------
# 2. encoders and readers
# --------------------------------------------------------------------------------------------------------------

def mac_bytes(mac: str | bytes) -> bytes:
    """'e0:72:a1:ac:a8:58' -> 6 bytes."""
    if isinstance(mac, bytes):
        if len(mac) != 6:
            raise ValueError(f"MAC must be 6 bytes: {mac!r}")
        return mac
    parts = mac.replace("-", ":").split(":")
    if len(parts) != 6:
        raise ValueError(f"bad MAC {mac!r}")
    return bytes(int(p, 16) for p in parts)


def mac_str(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def device_id(mac: str) -> str:
    """'e0:72:a1:ac:a8:58' -> 'head-e072a1aca858'."""
    return "head-" + mac_bytes(mac).hex()


def geometry_hash(geometry_id: str) -> int:
    """CRC-32 of the ASCII geometry_id (header geometry_hash)."""
    return zlib.crc32(geometry_id.encode("ascii"))


def _text(value: str, size: int) -> bytes:
    raw = value.encode("ascii")
    if len(raw) > size:
        raise ValueError(f"{value!r} longer than {size} bytes")
    return raw.ljust(size, b"\0")


def encode_range(rx_offset_us: int, *, tag_id: int = 0, round_seq: int = 0,
                 raw_cm: Sequence[int] = (0, 0, 0, 0), rssi_dbm: Sequence[int] = (0, 0, 0, 0),
                 reported_mask: int = 0xF, valid_mask: int | None = None, rssi_mask: int | None = None,
                 slot_mask: int | None = None, explicit_anchor_ids: bool = True,
                 configuration_verified: bool = True) -> bytes:
    """One range frame (24 B). Masks default to what raw_cm/rssi_dbm imply inside reported_mask."""
    if valid_mask is None:
        valid_mask = sum(1 << i for i in range(4) if reported_mask >> i & 1 and 1 <= raw_cm[i] <= 3000)
    if rssi_mask is None:
        rssi_mask = sum(1 << i for i in range(4) if reported_mask >> i & 1 and rssi_dbm[i] < 0)
    if slot_mask is None:
        slot_mask = reported_mask
    report_flags = reported_mask | (int(explicit_anchor_ids) << 4) | (int(configuration_verified) << 5)
    return RANGE.pack(rx_offset_us, round_seq & 0xFFFFFFFF, slot_mask, valid_mask | (rssi_mask << 4),
                      report_flags, tag_id, *raw_cm, *rssi_dbm)


def encode_sample(seq: int, tag_ms: int, raw: Sequence[int], flags: int = SAMPLE_FLAGS) -> bytes:
    """One IMU sample (21 B). raw = ax, ay, az, temp, gx, gy, gz (MPU6050 raw)."""
    return SAMPLE.pack(seq & 0xFFFF, tag_ms & 0xFFFFFFFF, flags, *raw)


def encode_imu_frame(rx_offset_us: int, *, tag_mac: str | bytes, tag_boot_id: int, link_seq: int,
                     tx_tag_ms: int, samples: Sequence[bytes], tag_id: int = 0, link_rssi: int = -50,
                     fs: int = IMU_FS, count: int | None = None) -> bytes:
    """One IMU frame (24 + 21 x count B). samples are encoded samples."""
    if count is None:
        count = len(samples)
    return IMU_FRAME.pack(rx_offset_us, mac_bytes(tag_mac), tag_boot_id & 0xFFFFFFFF, link_seq & 0xFFFF,
                          tx_tag_ms & 0xFFFFFFFF, link_rssi, fs, tag_id, count) + b"".join(samples)


def encode_block(kind: int, payload: bytes, *, at_offset_us: int = 0, kind_version: int = 1,
                 length: int | None = None) -> bytes:
    """One status block: 8 B block header + payload."""
    return BLOCK.pack(kind, kind_version, len(payload) if length is None else length, at_offset_us) + payload


def encode_system(*, head_mac: str | bytes, tag_count: int, geometry_id: str,
                  anchors: Sequence[Sequence[float]], set_members: Sequence[str | bytes | None] = (),
                  surveyed: bool = False, geometry_source: int = 0, slot_ms: int = 10,
                  position_dimensions: int = 3, module_filter: bool = True, imu_transport: int = 1,
                  fs: int = IMU_FS, imu_acquisition_hz: int = 100, imu_transport_hz: int = 100,
                  firmware_id: bytes = FIRMWARE_ID) -> bytes:
    """system payload (152 B). set_members = A1, A2, A3, T0, T1 MACs; None or missing = empty slot."""
    members = list(set_members) + [None] * (5 - len(set_members))
    members_raw = b"".join(b"\0" * 6 if m is None else mac_bytes(m) for m in members[:5])
    flags = int(surveyed) | (int(module_filter) << 1) | (imu_transport << 2)
    floats = [float(v) for a in anchors for v in a]
    return SYSTEM.pack(mac_bytes(head_mac), tag_count, slot_ms, position_dimensions, flags, geometry_source, fs,
                       imu_acquisition_hz, imu_transport_hz, members_raw, 0, *floats, firmware_id,
                       _text(geometry_id, 32))


def encode_anchor_slot(*, seen: bool = True, online: bool = True, uwb_configured: bool = True,
                       tag_role: bool = False, two_tag: bool = False, state: int = 0, rssi: int = -55,
                       valid_mask: int = 0xF, boot_id: int = 0, range_count: int = 0, round_seq: int = 0,
                       age_ms: int = 200, range_age_ms: int = 10) -> bytes:
    """One health anchor slot (20 B). An unseen anchor is all zero except the two ages (0xFFFF)."""
    if not seen:
        return ANCHOR_SLOT.pack(0, 0, 0, 0, 0, 0, 0, 0xFFFF, 0xFFFF)
    flags = 1 | (int(online) << 1) | (int(uwb_configured) << 2) | (int(tag_role) << 3) | (int(two_tag) << 4)
    return ANCHOR_SLOT.pack(flags, state, rssi, valid_mask, boot_id & 0xFFFFFFFF, range_count & 0xFFFFFFFF,
                            round_seq & 0xFFFFFFFF, age_ms, range_age_ms)


def encode_tag_slot(*, heard: bool = False, ranged: bool = False, link_rssi: int = 0, imu_age_ms: int = 0xFFFF,
                    range_count: int = 0, round_seq: int = 0, range_age_ms: int = 0xFFFF) -> bytes:
    """One health Tag slot (16 B). Fields that the flags say were never seen are forced to their empty value."""
    if not heard:
        link_rssi, imu_age_ms = 0, 0xFFFF
    if not ranged:
        range_count, round_seq, range_age_ms = 0, 0, 0xFFFF
    return TAG_SLOT.pack(int(heard) | (int(ranged) << 1), link_rssi, imu_age_ms, range_count & 0xFFFFFFFF,
                         round_seq & 0xFFFFFFFF, range_age_ms, 0)


def encode_health(*, tag_count: int, flags: int = 0b1010011, wifi_disconnect_reason: int = 0,
                  buffer_used: int = 0, buffer_capacity: int = 830, counters: Sequence[int] = (),
                  range_age_ms: int = 0xFFFF, imu_age_ms: int = 0xFFFF, anchors: Sequence[bytes] = (),
                  tags: Sequence[bytes] = ()) -> bytes:
    """health payload (192 B). anchors = 3 encoded anchor slots (A1-A3), tags = 2 encoded Tag slots (T0, T1);
    missing slots are empty."""
    counters = [c & 0xFFFFFFFF for c in counters] + [0] * (22 - len(counters))
    anchors = list(anchors) + [encode_anchor_slot(seen=False)] * (3 - len(anchors))
    tags = list(tags) + [encode_tag_slot()] * (2 - len(tags))
    return HEALTH.pack(flags, wifi_disconnect_reason, tag_count, buffer_used, buffer_capacity, *counters[:22],
                       range_age_ms, imu_age_ms, b"".join(anchors), b"".join(tags))


def encode_survey(anchors: Sequence[Sequence[float]], *, survey_id: int = 1, revision: int = 0,
                  n_samples: int = 40) -> bytes:
    """survey payload (304 B) for a solved survey whose solution is `anchors` (status 1, heights known)."""
    xyz = [(float(a[0]), float(a[1]), float(a[2])) for a in anchors]
    dist = [[math.dist(xyz[i], xyz[j]) for j in range(4)] for i in range(4)]
    meas: list[int] = []
    for i in range(4):
        for j in range(4):
            meas += [0, 0, 0] if i == j else [round(dist[i][j] * 100), n_samples, 2]
    heights = [p[2] for p in xyz]
    flags = 0b000101                    # have_measurements, solution.ok
    return SURVEY.pack(1, flags, 0b1111, 0, survey_id, revision, _text("ok", 24), _text("ok", 24), *heights,
                       *meas, *[d for row in dist for d in row], 0.03, 0.02, *[v for p in xyz for v in p],
                       0.02, 0.0, 0.0, max(heights) - min(heights))


def encode_chunk(*, boot_id: int, seq: int, head_us_start: int, geometry_hash: int,
                 ranges: Sequence[bytes] = (), imus: Sequence[bytes] = (), blocks: Sequence[bytes] = (),
                 utc_us_start: int = 0, flags: int | None = None, dropped_range: int = 0,
                 dropped_imu_frames: int = 0, tag_mask: int | None = None, n_range: int | None = None,
                 n_imu_frames: int | None = None, status_len: int | None = None, magic: int = CHUNK_MAGIC,
                 version: int = WIRE_VERSION, header_len: int = HEADER_LEN, reserved: int = 0,
                 crc: int | None = None) -> bytes:
    """Header + frames + blocks + CRC-32. Counts, tag_mask, flags and CRC are derived unless overridden
    (overrides exist to build wrong chunks)."""
    if flags is None:
        flags = FLAG_UTC if utc_us_start else 0
    if tag_mask is None:
        tag_mask = 0
        for r in ranges:
            tag_mask |= 1 << r[11]
        for f in imus:
            tag_mask |= 1 << f[22]
    status = b"".join(blocks)
    head = HEADER.pack(magic, version, header_len, boot_id & 0xFFFFFFFF, seq & 0xFFFFFFFF, head_us_start,
                       utc_us_start, geometry_hash, flags, len(ranges) if n_range is None else n_range,
                       len(imus) if n_imu_frames is None else n_imu_frames, dropped_range, dropped_imu_frames,
                       tag_mask, len(status) if status_len is None else status_len, reserved)
    body = b"".join((head, *ranges, *imus, status))
    return body + _U32.pack(zlib.crc32(body) if crc is None else crc)


class Header(NamedTuple):
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


def parse_header(chunk: bytes) -> Header:
    """The 48 B chunk header as named fields (no checks)."""
    return Header(*HEADER.unpack_from(chunk))


def chunk_key(chunk: bytes) -> tuple[int, int]:
    """(head_boot_id, chunk_seq) read from the header bytes."""
    return struct.unpack_from("<II", chunk, 4)


def frame_request(chunks: Iterable[bytes]) -> bytes:
    """Request body: repeated u32 len (LE) + chunk."""
    return b"".join(_U32.pack(len(c)) + c for c in chunks)


def unframe_request(body: bytes) -> list[bytes]:
    """Split a request body into chunks. ValueError if a len runs past the body."""
    out, pos = [], 0
    while pos < len(body):
        if pos + 4 > len(body):
            raise ValueError("truncated len")
        (n,) = _U32.unpack_from(body, pos)
        pos += 4
        if n > len(body) - pos:
            raise ValueError("len exceeds remaining body")
        out.append(body[pos:pos + n])
        pos += n
    return out


def encode_ack(server_utc_us: int, codes: Sequence[int]) -> bytes:
    return ACK.pack(ACK_MAGIC, ACK_VERSION, len(codes), server_utc_us) + bytes(codes)


def decode_ack(body: bytes) -> tuple[int, list[int]]:
    """Ack response body -> (server_utc_us, codes). ValueError if malformed."""
    if len(body) < ACK.size:
        raise ValueError("ack shorter than 12 B")
    magic, version, count, server_utc_us = ACK.unpack_from(body)
    if magic != ACK_MAGIC or version != ACK_VERSION:
        raise ValueError(f"bad ack magic/version {magic:#x}/{version}")
    if len(body) != ACK.size + count:
        raise ValueError(f"ack length {len(body)} != 12 + {count}")
    return server_utc_us, list(body[ACK.size:])


# --------------------------------------------------------------------------------------------------------------
# 3. FakeHead
# --------------------------------------------------------------------------------------------------------------

_LAP_RAD_PER_S = 2 * math.pi / 7.0      # one lap of the walking ellipse in 7 s (about 1 m/s)
_TAG_SAMPLE0_MS = 250                   # Tag clock of its first IMU sample after boot
_TAG_RANGE_START_US = 400_000           # a Tag starts ranging this long after boot
_FRAME_TX_AFTER_MS = 91                 # frame j is sent 1 ms after its last sample (sample 10j + 9)
_ROUND_JITTER_US = 300
_SYSTEM_AT_US, _SURVEY_AT_US, _HEALTH_AT_US = 0, 500_000, 999_000   # block at_offset_us
_SYSTEM_EVERY_S, _HEALTH_EVERY_S = 600, 60
_STRIDE_SAMPLES = 110                   # 1.1 s stride at 100 Hz

_gait_cache: dict[int, tuple[bytes, ...]] = {}


def _gait_table(side: int) -> tuple[bytes, ...]:
    """Packed raw (14 B) for 9 strides of synthetic ankle IMU while walking. side = tag_id: 0 = T0 (right foot),
    1 = T1 (left foot, half a stride later). Deterministic."""
    if side in _gait_cache:
        return _gait_cache[side]
    rng = random.Random(f"fake-head-gait:{side}")
    temp = round((31.0 - 36.53) * 340)
    out = []
    for k in range(_STRIDE_SAMPLES * 9):
        ph = ((k + side * _STRIDE_SAMPLES // 2) % _STRIDE_SAMPLES) / _STRIDE_SAMPLES   # 0 = heel strike
        if ph < 0.6:                                                    # stance: slow roll forward
            pitch_dps = -40.0 * math.sin(math.pi * ph / 0.6)
        else:                                                           # swing: fast forward rotation
            pitch_dps = 380.0 * math.sin(math.pi * (ph - 0.6) / 0.4)
        d = ph if ph < 0.5 else ph - 1.0
        strike_g = 3.2 * math.exp(-(d / 0.025) ** 2)
        ax_g = 0.9 * math.sin(2 * math.pi * ph) + 0.4 * strike_g + rng.gauss(0, 0.02)
        ay_g = 0.15 * math.sin(4 * math.pi * ph) + rng.gauss(0, 0.02)
        az_g = 1.0 + strike_g + 0.3 * math.cos(2 * math.pi * ph) + rng.gauss(0, 0.02)
        gx = 25.0 * math.sin(2 * math.pi * ph) + rng.gauss(0, 1.5)
        gy = pitch_dps + rng.gauss(0, 1.5)
        gz = 15.0 * math.cos(2 * math.pi * ph) + rng.gauss(0, 1.5)
        raw = [round(ax_g * ACCEL_LSB_PER_G), round(ay_g * ACCEL_LSB_PER_G), round(az_g * ACCEL_LSB_PER_G),
               temp + rng.randint(-3, 3), round(gx * GYRO_LSB_PER_DPS), round(gy * GYRO_LSB_PER_DPS),
               round(gz * GYRO_LSB_PER_DPS)]
        out.append(_RAW7.pack(*[max(-32768, min(32767, v)) for v in raw]))
    _gait_cache[side] = tuple(out)
    return _gait_cache[side]


@dataclass
class TagState:
    """One ankle Tag of the set. on=False: powered off (no range, no IMU). imu=False: ranging continues but
    the Tag's IMU frames do not reach the Head."""
    tag_id: int
    mac: str
    boot_id: int
    on: bool = True
    imu: bool = True


@dataclass
class _TagClock:
    """Tag-side timing of one Tag boot, in simulation µs. Keeps running across Head reboots."""
    boot_sim_us: int        # simulation time when the Tag clock read 0 ms
    ppm: int                # Tag clock rate error
    latency_us: int         # ESP-NOW delivery latency (base)
    rssi: int               # ESP-NOW link RSSI at the Head
    next_frame: int = 0     # next IMU frame index since Tag boot

    def frame_rx_us(self, j: int) -> int:
        tx_ms = _TAG_SAMPLE0_MS + 100 * j + _FRAME_TX_AFTER_MS
        return (self.boot_sim_us + tx_ms * 1_000_000_000 // (1_000_000 + self.ppm) + self.latency_us
                + (j * 7919) % 2500)

    def frame_at_or_after(self, sim_us: int) -> int:
        elapsed_ms = (sim_us - self.boot_sim_us) * (1_000_000 + self.ppm) // 1_000_000_000
        return max(0, (elapsed_ms - _TAG_SAMPLE0_MS - _FRAME_TX_AFTER_MS) // 100 - 1)


@dataclass
class _TagSeen:
    """What the Head has seen of one Tag since the Head booted (health Tag slot)."""
    range_count: int = 0
    round_seq: int = 0
    last_range_us: int | None = None
    last_imu_us: int | None = None
    last_frame: int | None = None
    last_rssi: int = 0


class FakeHead:
    """One simulated Head (A0) with its set (A1-A3 and up to two Tags).

    Time: a simulation clock starts at 0 when the object is made and never jumps; each next_chunk() covers the
    next second [sim, sim + 1 s). The Head clock is sim + a boot-dependent shift; it restarts at head_us0 on
    reboot. True UTC is sim + a fixed base; the Head knows it only once it has an estimate (utc_offset_us).
    """

    def __init__(self, mac: str = TEST_DEVICE_MAC, *, tag_count: int = 1, boot_id: int | None = None,
                 seed: int = 0, geometry_id: str = "default",
                 anchors: Sequence[Sequence[float]] = DEFAULT_ANCHORS, surveyed: bool = False,
                 head_us0: int = DEFAULT_HEAD_US0, utc_offset_us: int | None = DEFAULT_UTC_OFFSET_US,
                 walk_pattern: tuple[float, float] | None = (40.0, 80.0), range_hz: int = 95):
        if tag_count not in (1, 2):
            raise ValueError("tag_count must be 1 or 2")
        self.mac = mac
        self._mac_raw = mac_bytes(mac)
        self.tag_count = tag_count
        self.head_us0 = head_us0
        self.walk_pattern = walk_pattern
        self.range_hz = range_hz
        # Identity values (MACs, boot ids, clocks) come from their own stream so that they do not depend on
        # how much noise was drawn before, e.g. a reboot gives the same new boot id whenever it happens.
        self._ids = random.Random(f"fake-head-ids:{seed}:{mac}")
        self._rng = random.Random(f"fake-head-noise:{seed}:{mac}")
        self._sim_us = 0
        self.sim_s = 0
        self._anchor_macs = [self._new_mac() for _ in range(3)]
        self._anchor_boot_ids = [self._ids.getrandbits(32) for _ in range(3)]
        self._set_geometry_values(geometry_id, anchors, surveyed, survey=False)
        self.tags: list[TagState] = []
        self._clocks: list[_TagClock] = []
        for i in range(tag_count):
            self.tags.append(TagState(i, mac_str(self._new_mac()), self._ids.getrandbits(32)))
            self._clocks.append(self._new_clock(-self._ids.randrange(30, 900) * 1_000_000))
        known = utc_offset_us is not None
        offset = utc_offset_us if known else DEFAULT_UTC_OFFSET_US
        self._utc_base_us = offset + head_us0           # true UTC at sim 0
        self._utc_estimate: int | None = offset if known else None
        # Upload state the Uploader reports through the health block.
        self.buffer_used = 0
        self.buffer_capacity = 830
        self.wifi_connected = True
        self._boot(boot_id)
        self._utc_estimate = offset if known else None  # _boot clears it; the constructor sets it

    # -- identity helpers -------------------------------------------------------------------------------------

    def _new_mac(self) -> bytes:
        return bytes([0xE0, 0x72, 0xA1]) + self._ids.randbytes(3)   # Espressif OUI, unicast

    def _new_clock(self, boot_sim_us: int) -> _TagClock:
        clock = _TagClock(boot_sim_us, self._ids.randrange(-30, 31), 1500 + self._ids.randrange(1000),
                          -self._ids.randrange(45, 70))
        clock.next_frame = clock.frame_at_or_after(self._sim_us)
        return clock

    def _boot(self, boot_id: int | None) -> None:
        self.boot_id = self._ids.getrandbits(32) if boot_id is None else boot_id
        self.seq = 0
        self.t_s = 0
        self._head_shift_us = self.head_us0 - self._sim_us
        self._round_base_us = self._sim_us
        self._next_round = 0
        self._last_round_us: int | None = None
        self.counters = [0] * 22
        self._seen = [_TagSeen() for _ in range(self.tag_count)]
        self._pending_system = True
        self._pending_survey = False
        self._survey_id = 0
        self._overflow: tuple[int, int] | None = None
        self._utc_estimate = None                       # RAM state: lost on reboot
        self.last_key: tuple[int, int] | None = None

    def _set_geometry_values(self, geometry_id: str, anchors: Sequence[Sequence[float]], surveyed: bool,
                             survey: bool) -> None:
        if len(anchors) != 4:
            raise ValueError("anchors must have 4 entries (A0-A3)")
        self.geometry_id = geometry_id
        self.anchors = [tuple(float(v) for v in a) for a in anchors]
        self.surveyed = surveyed
        if survey or geometry_id.startswith("sv-"):
            self.geometry_source = 1
        elif geometry_id == "default":
            self.geometry_source = 0
        else:
            self.geometry_source = 2
        xs = [a[0] for a in self.anchors]
        ys = [a[1] for a in self.anchors]
        self._room = (sum(xs) / 4, sum(ys) / 4, max(0.4, 0.3 * (max(xs) - min(xs))),
                      max(0.4, 0.3 * (max(ys) - min(ys))))

    # -- public state changes (take effect from the next chunk) ------------------------------------------------

    @property
    def head_us_now(self) -> int:
        """Head clock at the start of the next window."""
        return self._sim_us + self._head_shift_us

    @property
    def utc_offset_us(self) -> int | None:
        """The Head's UTC estimate (utc = head_us + offset), or None when it has none."""
        return self._utc_estimate

    @property
    def true_utc_offset_us(self) -> int:
        """The exact offset for the current boot (what a perfect estimate would be)."""
        return self._utc_base_us - self._head_shift_us

    @property
    def utc_now_us(self) -> int:
        """True UTC at the start of the next window."""
        return self._sim_us + self._utc_base_us

    def set_utc(self, utc_offset_us: int | None) -> None:
        """Set (or clear with None) the Head's UTC estimate."""
        self._utc_estimate = utc_offset_us

    def learn_utc(self) -> None:
        """The Head got an ack: its estimate becomes the exact offset (fake round trip of 0 ms)."""
        self._utc_estimate = self.true_utc_offset_us

    def rebase_utc(self, utc_now_us: int) -> None:
        """Make true UTC at the start of the next window equal utc_now_us (keeps a known estimate exact)."""
        known = self._utc_estimate is not None
        self._utc_base_us = utc_now_us - self._sim_us
        if known:
            self.learn_utc()

    def reboot(self, boot_id: int | None = None) -> None:
        """New head_boot_id, seq 0, Head clock restarts at head_us0, UTC estimate lost, status counters reset.
        Tags keep running."""
        self._boot(boot_id)

    def set_geometry(self, geometry_id: str, anchors: Sequence[Sequence[float]] | None = None, *,
                     surveyed: bool = True, survey: bool = False) -> None:
        """Change the coordinate set. The next chunk carries the new geometry_hash and a system block;
        survey=True also adds a survey block (solved, with these anchors)."""
        self._set_geometry_values(geometry_id, self.anchors if anchors is None else anchors, surveyed, survey)
        self._pending_system = True
        if survey:
            self._survey_id += 1
            self._pending_survey = True

    def replace_tag(self, tag_id: int, mac: str | None = None, boot_id: int | None = None) -> None:
        """Pair a different Tag board in this ankle slot (new MAC, new boot). The next chunk has a system block."""
        tag = self.tags[tag_id]
        tag.mac = mac_str(self._new_mac()) if mac is None else mac_str(mac_bytes(mac))
        tag.boot_id = self._ids.getrandbits(32) if boot_id is None else boot_id
        tag.on = True
        self._clocks[tag_id] = self._new_clock(self._sim_us)
        self._pending_system = True

    def set_tag(self, tag_id: int, *, on: bool | None = None, imu: bool | None = None,
                boot_id: int | None = None) -> None:
        """Power a Tag off/on or stop/resume its IMU frames. Powering on an off Tag is a Tag boot: new
        tag_boot_id (or boot_id), Tag clock and sequence numbers restart."""
        tag = self.tags[tag_id]
        if on is not None:
            if on and not tag.on:
                tag.boot_id = self._ids.getrandbits(32) if boot_id is None else boot_id
                self._clocks[tag_id] = self._new_clock(self._sim_us)
            tag.on = on
        if imu is not None:
            tag.imu = imu

    def mark_overflow(self, drop_range: int = 2, drop_imu: int = 1) -> None:
        """The Head buffer dropped a chunk: the next chunk gets flags bit1, and (as the wire vector
        overflow_flag) also drops a few frames, reported in dropped_range / dropped_imu_frames."""
        self.counters[C_CHUNKS_DROPPED_OVERFLOW] += 1
        dr, di = self._overflow or (0, 0)
        self._overflow = (max(dr, drop_range), max(di, drop_imu))

    def note_upload_failure(self) -> None:
        self.counters[C_UPLOAD_FAILURES] += 1

    def note_rejected(self, n: int) -> None:
        self.counters[C_CHUNKS_REJECTED] += n

    def walking(self, sim_s: int | None = None) -> bool:
        """Whether the wearer walks during this simulation second (IMU frames are sent only then)."""
        if self.walk_pattern is None:
            return True
        walk_s, rest_s = self.walk_pattern
        return (self.sim_s if sim_s is None else sim_s) % (walk_s + rest_s) < walk_s

    # -- the 1-second chunk ------------------------------------------------------------------------------------

    def next_chunk(self) -> bytes | None:
        """Build the chunk for the next 1-second window and advance. None when the window has no frame and
        no status block (wire.md rule 1); seq is not consumed then."""
        ws = self._sim_us
        we = ws + 1_000_000
        ranges = self._range_frames(ws, we)
        imus = self._imu_frames(ws, we, self.walking())
        dropped_range = dropped_imu = 0
        flags = 0
        if self._overflow is not None:
            flags |= FLAG_OVERFLOW
            dropped_range = min(self._overflow[0], max(0, len(ranges) - 1))
            dropped_imu = min(self._overflow[1], max(0, len(imus) - (0 if ranges else 1)))
            ranges = ranges[:len(ranges) - dropped_range]
            imus = imus[:len(imus) - dropped_imu]
            self._overflow = None
        blocks = []
        if self.t_s % _SYSTEM_EVERY_S == 0:
            self._pending_system = True
        if self._pending_system:
            blocks.append(self.system_block())
            self._pending_system = False
        if self.t_s % _HEALTH_EVERY_S == 0:
            blocks.append(self.health_block(ws + _HEALTH_AT_US))
        if self._pending_survey:
            blocks.append(self.survey_block())
            self._pending_survey = False
        head_us_start = ws + self._head_shift_us
        self._sim_us = we
        self.sim_s += 1
        self.t_s += 1
        if not ranges and not imus and not blocks:
            return None
        utc = 0
        if self._utc_estimate is not None:
            flags |= FLAG_UTC
            utc = head_us_start + self._utc_estimate
        chunk = encode_chunk(boot_id=self.boot_id, seq=self.seq, head_us_start=head_us_start, utc_us_start=utc,
                             geometry_hash=geometry_hash(self.geometry_id), flags=flags, ranges=ranges,
                             imus=imus, blocks=blocks, dropped_range=dropped_range,
                             dropped_imu_frames=dropped_imu)
        self.last_key = (self.boot_id, self.seq)
        self.seq += 1
        return chunk

    def _foot_xyz(self, sim_us: int, tag_id: int) -> tuple[float, float, float]:
        t = sim_us / 1e6
        if self.walk_pattern is None:
            walked = t
        else:
            walk_s, rest_s = self.walk_pattern
            laps, rem = divmod(t, walk_s + rest_s)
            walked = laps * walk_s + min(rem, walk_s)
        th = walked * _LAP_RAD_PER_S
        cx, cy, rx, ry = self._room
        side = 0.1 if tag_id == 1 else -0.1             # T1 = left foot on the inner side (anticlockwise lap)
        return cx + (rx - side) * math.cos(th), cy + (ry - side) * math.sin(th), 0.08

    def _range_frames(self, ws: int, we: int) -> list[bytes]:
        out = []
        rng = self._rng.random
        two = self.tag_count == 2
        r = self._next_round
        while True:
            rx = self._round_base_us + r * 1_000_000 // self.range_hz + (r * 2654435761 >> 9) % _ROUND_JITTER_US
            if rx >= we:
                break
            tag_id = r % 2 if two else 0
            r += 1
            tag = self.tags[tag_id]
            if rx < ws or not tag.on or rx < self._clocks[tag_id].boot_sim_us + _TAG_RANGE_START_US:
                continue
            x, y, z = self._foot_xyz(rx, tag_id)
            raw = [0, 0, 0, 0]
            rssi = [0, 0, 0, 0]
            reported = valid = rssi_mask = 0
            for i, (ax, ay, az, bias) in enumerate(self.anchors):
                u = rng()
                if u < 0.008:                           # anchor missed this round: not reported
                    continue
                bit = 1 << i
                reported |= bit
                d = math.sqrt((x - ax) ** 2 + (y - ay) ** 2 + (z - az) ** 2)
                if u >= 0.016:                          # else: reported with distance 0 (not valid)
                    cm = round((d + bias + (rng() - 0.5) * 0.08 + (0.5 if u > 0.995 else 0.0)) * 100)
                    if cm > 3000:
                        cm = 32767                      # saturated: reported, not valid
                    elif cm < 1:
                        cm = 1
                    raw[i] = cm
                    if cm <= 3000:
                        valid |= bit
                if u < 0.97:                            # RSSI present
                    rssi[i] = max(-120, min(-1, round(-58 - 4 * d + (u - 0.5) * 6)))
                    rssi_mask |= bit
            seen = self._seen[tag_id]
            out.append(RANGE.pack(rx - ws, seen.round_seq, reported, valid | (rssi_mask << 4), reported | 0x30,
                                  tag_id, *raw, *rssi))
            seen.round_seq = (seen.round_seq + 1) & 0xFF   # the module's 8-bit round counter
            seen.range_count += 1
            seen.last_range_us = rx
            self._last_round_us = rx
        self._next_round = r
        self.counters[C_RANGE_COUNT] += len(out)
        return out

    def _imu_frames(self, ws: int, we: int, walking: bool) -> list[bytes]:
        events = []
        for tag in self.tags:
            if not tag.on:
                continue
            clock = self._clocks[tag.tag_id]
            deliver = tag.imu and walking
            j = clock.next_frame
            while True:
                rx = clock.frame_rx_us(j)
                if rx >= we:
                    break
                if rx >= ws and deliver:
                    events.append((rx, tag.tag_id, j))
                j += 1
            clock.next_frame = j
        events.sort()
        out = []
        for rx, tag_id, j in events:
            tag = self.tags[tag_id]
            seen = self._seen[tag_id]
            table = _gait_table(tag_id)
            n = len(table)
            k0 = 10 * j
            samples = b"".join(
                _SAMPLE_HEAD.pack((k0 + i) & 0xFFFF, (_TAG_SAMPLE0_MS + 10 * (k0 + i)) & 0xFFFFFFFF, SAMPLE_FLAGS)
                + table[(k0 + i) % n] for i in range(10))
            link_rssi = self._clocks[tag_id].rssi + (j * 31) % 7 - 3
            tx_ms = _TAG_SAMPLE0_MS + 100 * j + _FRAME_TX_AFTER_MS
            out.append(IMU_FRAME.pack(rx - ws, mac_bytes(tag.mac), tag.boot_id, j & 0xFFFF, tx_ms & 0xFFFFFFFF,
                                      link_rssi, IMU_FS, tag_id, 10) + samples)
            if seen.last_frame is not None and j > seen.last_frame + 1:
                self.counters[C_IMU_LINK_GAPS] += 1
            seen.last_frame = j
            seen.last_imu_us = rx
            seen.last_rssi = link_rssi
        self.counters[C_IMU_LINK_FRAMES] += len(out)
        self.counters[C_IMU_LINK_SAMPLES] += 10 * len(out)
        return out

    # -- status blocks -----------------------------------------------------------------------------------------

    def set_members(self) -> list[bytes | None]:
        """A1, A2, A3, T0, T1 MACs as in the system block (None = empty slot)."""
        tags: list[bytes | None] = [mac_bytes(t.mac) for t in self.tags]
        return [*self._anchor_macs, *tags, *[None] * (2 - len(tags))]

    def system_block(self, *, at_offset_us: int = _SYSTEM_AT_US) -> bytes:
        payload = encode_system(head_mac=self._mac_raw, tag_count=self.tag_count, geometry_id=self.geometry_id,
                                anchors=self.anchors, set_members=self.set_members(), surveyed=self.surveyed,
                                geometry_source=self.geometry_source)
        return encode_block(KIND_SYSTEM, payload, at_offset_us=at_offset_us)

    def survey_block(self, *, at_offset_us: int = _SURVEY_AT_US) -> bytes:
        payload = encode_survey(self.anchors, survey_id=max(1, self._survey_id))
        return encode_block(KIND_SURVEY, payload, at_offset_us=at_offset_us)

    def health_block(self, block_sim_us: int | None = None) -> bytes:
        """health block as made at block_sim_us (default: end of the next window)."""
        ws = self._sim_us if block_sim_us is None else block_sim_us - _HEALTH_AT_US
        at = ws + _HEALTH_AT_US

        def age(last: int | None) -> int:
            return 0xFFFF if last is None else min(0xFFFE, max(0, (at - last) // 1000))

        flags = (1 | (int(self.wifi_connected) << 1) | (1 << 4) | (int(self.buffer_capacity > 20) << 6)
                 | (int(self._utc_estimate is not None) << 7))
        range_age = age(self._last_round_us)
        last_imu = [s.last_imu_us for s in self._seen if s.last_imu_us is not None]
        anchors = [encode_anchor_slot(two_tag=self.tag_count == 2, rssi=-48 - 6 * i, boot_id=self._anchor_boot_ids[i],
                                      range_count=self.counters[C_RANGE_COUNT], round_seq=self._seen[0].round_seq,
                                      age_ms=100 + (self.t_s * 37 + 113 * i) % 400,
                                      range_age_ms=0xFFFF if range_age == 0xFFFF else min(0xFFFE, range_age + 3))
                   for i in range(3)]
        tags = [encode_tag_slot(heard=s.last_imu_us is not None, ranged=s.range_count > 0,
                                link_rssi=s.last_rssi, imu_age_ms=age(s.last_imu_us), range_count=s.range_count,
                                round_seq=(s.round_seq - 1) & 0xFF, range_age_ms=age(s.last_range_us))
                for s in self._seen]
        payload = encode_health(tag_count=self.tag_count, flags=flags,
                                buffer_used=min(self.buffer_used, self.buffer_capacity),
                                buffer_capacity=self.buffer_capacity, counters=self.counters,
                                range_age_ms=range_age, imu_age_ms=age(max(last_imu) if last_imu else None),
                                anchors=anchors, tags=tags)
        return encode_block(KIND_HEALTH, payload, at_offset_us=at - ws)


# --------------------------------------------------------------------------------------------------------------
# 4. Uploader (simulated time)
# --------------------------------------------------------------------------------------------------------------

BACKOFF_S = (1, 2, 4, 8, 16, 30)       # wire.md resend rule 3
REQUEST_SPACING_US = 50_000


@dataclass(frozen=True)
class Request:
    at_us: int                              # server-clock UTC µs when this request is sent
    body: bytes                             # framed request body
    keys: tuple[tuple[int, int], ...]       # (head_boot_id, chunk_seq) of each chunk, in order

    @property
    def n_chunks(self) -> int:
        return len(self.keys)


def _take_batch(buffer: deque[bytes], max_chunks: int = MAX_REQUEST_CHUNKS) -> list[bytes]:
    """Oldest chunks first, up to 16 chunks and 128 kB of framed body."""
    batch: list[bytes] = []
    size = 0
    while buffer and len(batch) < max_chunks and size + 4 + len(buffer[0]) <= MAX_REQUEST_BYTES:
        c = buffer.popleft()
        batch.append(c)
        size += 4 + len(c)
    return batch


class Uploader:
    """The Head's ring buffer and resend rules (wire.md "Head의 다시 보내기 규칙") in simulated time.

    Each simulated second: the Head closes a chunk into the buffer (dropping the oldest when full, which sets
    flags bit1 on the next chunk); then, if a send attempt is due, it either sends (online) or fails (offline).
    Sends go oldest first, <= 16 chunks and <= 128 kB per request, requests 50 ms apart, at most one second of
    requests per second. Every online request is taken as acknowledged with codes 0 and removed; the ack gives
    the Head an exact UTC estimate. While offline, failed attempts follow the 1, 2, 4, 8, 16, 30 s backoff (they
    count in health upload_failures); the backlog goes out in the first second the link is back, as if a retry
    fired at that moment. Head time and wall time advance together.
    The Uploader re-anchors the Head's true UTC so that its next window starts at start_utc_us.
    """

    def __init__(self, head: FakeHead, *, capacity: int = 830, start_utc_us: int = DEFAULT_START_UTC_US,
                 learn_utc: bool = True):
        self.head = head
        self.capacity = capacity
        self.start_utc_us = start_utc_us
        self.learn_utc = learn_utc
        self.buffer: deque[bytes] = deque()
        self.requests: list[Request] = []
        self.elapsed_s = 0
        self._last_at: int | None = None
        self._next_attempt_us = 0
        self._fails = 0
        head.rebase_utc(start_utc_us)
        head.buffer_capacity = capacity

    @property
    def now_us(self) -> int:
        return self.start_utc_us + self.elapsed_s * 1_000_000

    def run(self, seconds: int, *, online: bool = True) -> list[Request]:
        """Simulate `seconds` of Head time; returns the requests sent meanwhile."""
        first = len(self.requests)
        for _ in range(seconds):
            self.head.buffer_used = len(self.buffer)
            chunk = self.head.next_chunk()
            self.elapsed_s += 1
            if chunk is not None:
                if len(self.buffer) >= self.capacity:
                    self.buffer.popleft()
                    self.head.mark_overflow()
                self.buffer.append(chunk)
            if not self.buffer:
                continue
            if online:
                end = self.now_us + 1_000_000
                while self.buffer and self._slot() < end:
                    self._emit(_take_batch(self.buffer))
                self._fails = 0
                self._next_attempt_us = 0
            elif self.now_us >= self._next_attempt_us:
                self.head.note_upload_failure()
                self._next_attempt_us = self.now_us + BACKOFF_S[min(self._fails, len(BACKOFF_S) - 1)] * 1_000_000
                self._fails += 1
        return self.requests[first:]

    def reboot(self, boot_id: int | None = None) -> None:
        """Head reboot: RAM buffer and UTC estimate are lost."""
        self.head.reboot(boot_id)
        self.head.buffer_capacity = self.capacity
        self.buffer.clear()
        self._fails = 0
        self._next_attempt_us = 0

    def take_buffer(self) -> list[bytes]:
        """Remove and return everything still buffered (for scenarios that deliver it later)."""
        out = list(self.buffer)
        self.buffer.clear()
        return out

    def send_chunks(self, chunks: Sequence[bytes]) -> list[Request]:
        """Send these chunks now (oldest first, normal request limits)."""
        first = len(self.requests)
        pending = deque(chunks)
        while pending:
            self._emit(_take_batch(pending))
        return self.requests[first:]

    def resend_largest(self) -> Request:
        """Send again, byte for byte, the earliest request with the most chunks."""
        old = max(self.requests, key=lambda r: (r.n_chunks, -r.at_us))
        req = Request(self._slot(), old.body, old.keys)
        self._push_request(req)
        return req

    def _slot(self) -> int:
        return self.now_us if self._last_at is None else max(self.now_us, self._last_at + REQUEST_SPACING_US)

    def _emit(self, batch: list[bytes]) -> None:
        self._push_request(Request(self._slot(), frame_request(batch), tuple(chunk_key(c) for c in batch)))
        if self.learn_utc:
            self.head.learn_utc()

    def _push_request(self, req: Request) -> None:
        self.requests.append(req)
        self._last_at = req.at_us


# --------------------------------------------------------------------------------------------------------------
# 5. scenarios and wrong chunks
# --------------------------------------------------------------------------------------------------------------
# A scenario is a script over a driver (Uploader here, _LiveUploader in the CLI): run(seconds, online),
# head mutations, reboot, take_buffer, send_chunks, resend_largest. Expected segment results (SPEC 6.4, 11 M2)
# are in each docstring; seq numbers assume the defaults.

def _sc_steady(d, duration_s: int = 600) -> None:
    d.run(duration_s)


def _sc_normal(d, duration_s: int = 600) -> None:
    """1-Tag, online. One segment, sides right, 1 segment_tag row; every second is a chunk with frames
    (codes all 0); walking 40 s of every 120 s -> 5 bouts in 600 s."""
    _sc_steady(d, duration_s)


def _sc_two_tag(d, duration_s: int = 600) -> None:
    """2-Tag, online. One segment, sides both, 2 segment_tag rows; tag_mask 3."""
    _sc_steady(d, duration_s)


def _sc_normal_2h(d, duration_s: int = 7200) -> None:
    """1-Tag, online, 2 h. Two segments: seq 0-3599 closed max_length, seq 3600-7199 open."""
    _sc_steady(d, duration_s)


def _sc_outage(d, before_s: int = 120, offline_s: int = 300, after_s: int = 120) -> None:
    """Online, internet down offline_s (chunks buffered), then online again: the backlog goes out in a burst in
    the first second the link is back. One segment, no missing chunks; it is closed silence_60s
    during the outage (if the loop runs), reopened by the burst, closed again by silence after the end ->
    exactly one analysis job pending (M2 test 2)."""
    d.run(before_s)
    d.run(offline_s, online=False)
    d.run(after_s)


def _sc_outage_tag_off(d, before_s: int = 120, offline_s: int = 300, tag_off_at_s: int = 60,
                       tag_off_s: int = 120, after_s: int = 120) -> None:
    """Online, then offline offline_s; tag_off_at_s into the outage the Tag is off for tag_off_s (only
    frame-less health chunks then), then the backlog arrives at once. Segment 1 (seq up to before_s +
    tag_off_at_s - 1) reopened by the burst then closed gap_60s; segment 2 starts when the Tag is back
    (Tag rebooted, new tag_boot_id)."""
    d.run(before_s)
    d.run(tag_off_at_s, online=False)
    d.head.set_tag(0, on=False)
    d.run(tag_off_s, online=False)
    d.head.set_tag(0, on=True)
    d.run(offline_s - tag_off_at_s - tag_off_s, online=False)
    d.run(after_s)


def _sc_tag_off(d, before_s: int = 120, off_s: int = 120, after_s: int = 120) -> None:
    """Online throughout; the Tag is off off_s (only health chunks, every 60 s, reach the server). Segment 1
    closed silence_60s while the Tag is off; segment 2 starts when the Tag is back."""
    d.run(before_s)
    d.head.set_tag(0, on=False)
    d.run(off_s)
    d.head.set_tag(0, on=True)
    d.run(after_s)


def _sc_overflow(d, before_s: int = 60, offline_s: int = 60, after_s: int = 60) -> None:
    """Head buffer of 20 chunks (default capacity for this scenario), offline offline_s: the oldest chunks are
    dropped (their seq is consumed), every chunk made after a drop has flags bit1 and dropped_range > 0. With
    the defaults 41 chunks are lost (seq 60-100) and the Head-time gap stays under 60 s, so it is one segment
    with n_missing_chunks = 41 (count it from Request.keys if you change the options)."""
    d.run(before_s)
    d.run(offline_s, online=False)
    d.run(after_s)


def _sc_resend(d, before_s: int = 20, offline_s: int = 10, after_s: int = 20) -> None:
    """A multi-chunk request (backlog after a short outage) is sent again byte for byte as the last request:
    its codes must all be 1 and nothing new is stored."""
    d.run(before_s)
    d.run(offline_s, online=False)
    d.run(after_s)
    d.resend_largest()


def _sc_reboot(d, before_s: int = 120, after_s: int = 120) -> None:
    """Head reboot: new head_boot_id, seq from 0, Head clock from head_us0, no UTC estimate until the first ack
    (the first chunk of the new boot has flags bit0 = 0). Old segment closed boot_changed."""
    d.run(before_s)
    d.reboot()
    d.run(after_s)


def _sc_late_old_boot(d, before_s: int = 120, held_s: int = 20, new_before_s: int = 20,
                      new_after_s: int = 40) -> None:
    """Old boot online before_s, then held_s chunks stay unsent; the Head reboots, the new boot sends for
    new_before_s, then the held old-boot chunks arrive, then the new boot goes on. Old segment: closed
    boot_changed by the new boot's first chunk, reopened by the late chunks (later closed silence_60s);
    the new boot's segment stays open."""
    d.run(before_s)
    d.run(held_s, online=False)
    held = d.take_buffer()
    d.reboot()
    d.run(new_before_s)
    d.send_chunks(held)
    d.run(new_after_s)


def _sc_tag_swap(d, before_s: int = 120, after_s: int = 120) -> None:
    """The right Tag (T0) is replaced by another board at before_s (a walking start by default, so IMU frames of
    the new Tag come at once). Segment 1 = seq 0..before_s-1 closed tag_changed; segment 2 from there. The swap
    chunk also has a system block with the new set_members."""
    d.run(before_s)
    d.head.replace_tag(0)
    d.run(after_s)


def _sc_t1_appears(d, before_s: int = 60, after_s: int = 180) -> None:
    """2-Tag Head; T1 is off at first and powered on at before_s (a rest period by default: its range frames
    come first, its IMU frames from the next walk at 120 s). One segment, sides both, 2 segment_tag rows; the
    left row gets its MAC when T1's first IMU frame arrives."""
    d.head.set_tag(1, on=False)
    d.run(before_s)
    d.head.set_tag(1, on=True)
    d.run(after_s)


def _sc_geometry_change(d, before_s: int = 120, after_s: int = 120) -> None:
    """Anchors surveyed at before_s: the next chunk has a new geometry_hash, a system block and a survey block.
    Segment 1 closed geometry_changed; segment 2 has installation_id from its creation."""
    d.run(before_s)
    head = d.head
    moved = [(x + 0.05, y - 0.04, z, 0.02) for x, y, z, _ in head.anchors]
    moved[0] = (0.0, 0.0, moved[0][2], 0.02)    # survey frame: A0 at the origin
    head.set_geometry(f"sv-{head.boot_id:08x}-1-0", moved, surveyed=True, survey=True)
    d.run(after_s)


def _sc_frameless_only(d, duration_s: int = 600) -> None:
    """The Tag is off the whole time: only health chunks (every 60 s; the first also has the system block).
    No segment."""
    d.head.set_tag(0, on=False)
    d.run(duration_s)


def _sc_utc_late(d, offline_s: int = 120, after_s: int = 180) -> None:
    """The Head boots without internet, so without a UTC estimate: the first chunks (sent in a burst once
    online) have flags bit0 = 0 and utc_us_start = 0; chunks made after the first ack have bit0 = 1. One
    segment: time_source 'arrival' at first, then 'server' with start_at = the true start (M2 test 5)."""
    d.head.set_utc(None)
    d.run(offline_s, online=False)
    d.run(after_s)


@dataclass(frozen=True)
class _ScenarioDef:
    script: Callable[..., None]
    head: dict = field(default_factory=dict)        # FakeHead keyword arguments
    capacity: int = 830


_SCENARIOS: dict[str, _ScenarioDef] = {
    "normal": _ScenarioDef(_sc_normal),
    "two_tag": _ScenarioDef(_sc_two_tag, {"tag_count": 2}),
    "normal_2h": _ScenarioDef(_sc_normal_2h),
    "outage": _ScenarioDef(_sc_outage),
    "outage_tag_off": _ScenarioDef(_sc_outage_tag_off),
    "tag_off": _ScenarioDef(_sc_tag_off),
    "overflow": _ScenarioDef(_sc_overflow, capacity=20),
    "resend": _ScenarioDef(_sc_resend),
    "reboot": _ScenarioDef(_sc_reboot),
    "late_old_boot": _ScenarioDef(_sc_late_old_boot),
    "tag_swap": _ScenarioDef(_sc_tag_swap),
    "t1_appears": _ScenarioDef(_sc_t1_appears, {"tag_count": 2}),
    "geometry_change": _ScenarioDef(_sc_geometry_change),
    "frameless_only": _ScenarioDef(_sc_frameless_only),
    "utc_late": _ScenarioDef(_sc_utc_late),
}
SCENARIOS: tuple[str, ...] = tuple(_SCENARIOS)
_HEAD_OPTS = ("tag_count", "walk_pattern", "range_hz", "head_us0", "geometry_id", "anchors", "surveyed", "boot_id")


def _split_opts(name: str, opts: dict) -> tuple[_ScenarioDef, dict, int, dict]:
    if name not in _SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; one of {', '.join(SCENARIOS)}")
    sd = _SCENARIOS[name]
    head_kw = dict(sd.head)
    script_kw = dict(opts)
    for key in _HEAD_OPTS:
        if key in script_kw:
            head_kw[key] = script_kw.pop(key)
    capacity = script_kw.pop("capacity", sd.capacity)
    allowed = set(inspect.signature(sd.script).parameters) - {"d"}
    unknown = set(script_kw) - allowed
    if unknown:
        raise TypeError(f"scenario {name!r}: unknown options {sorted(unknown)}; allowed {sorted(allowed)}, "
                        f"head options {list(_HEAD_OPTS)}, capacity")
    return sd, head_kw, capacity, script_kw


def scenario_uploader(name: str, *, seed: int = 0, start_utc_us: int = DEFAULT_START_UTC_US,
                      mac: str = TEST_DEVICE_MAC, **opts) -> Uploader:
    """Run a scenario and return its Uploader (requests, head state)."""
    sd, head_kw, capacity, script_kw = _split_opts(name, opts)
    head = FakeHead(mac, seed=seed, **head_kw)
    up = Uploader(head, capacity=capacity, start_utc_us=start_utc_us)
    sd.script(up, **script_kw)
    return up


def scenario(name: str, *, seed: int = 0, start_utc_us: int = DEFAULT_START_UTC_US, mac: str = TEST_DEVICE_MAC,
             **opts) -> list[Request]:
    """Requests of a named scenario (see SCENARIOS), in send order (at_us never decreases). opts: the script's
    own options (durations, see each _sc_* docstring), FakeHead options (tag_count, walk_pattern, range_hz, ...)
    and capacity."""
    return scenario_uploader(name, seed=seed, start_utc_us=start_utc_us, mac=mac, **opts).requests


def device_macs(n: int) -> list[str]:
    """n distinct Head MACs; the first is TEST_DEVICE_MAC."""
    return [TEST_DEVICE_MAC] + [f"e0:72:a1:b0:{i >> 8 & 0xFF:02x}:{i & 0xFF:02x}" for i in range(1, n)]


def multi_device(n: int, name: str = "normal", *, seed: int = 0, start_utc_us: int = DEFAULT_START_UTC_US,
                 **opts) -> dict[str, list[Request]]:
    """The same scenario on n Heads (MACs from device_macs(n), seed + i each). {mac: requests}."""
    return {mac: scenario(name, seed=seed + i, start_utc_us=start_utc_us, mac=mac, **opts)
            for i, mac in enumerate(device_macs(n))}


def scenario_span_s(name: str, **opts) -> int:
    """How many seconds the scenario's script runs (without building chunks)."""
    sd, head_kw, _, script_kw = _split_opts(name, opts)

    class _Count:
        def __init__(self) -> None:
            self.head = FakeHead(**head_kw)
            self.elapsed_s = 0

        def run(self, seconds: int, *, online: bool = True) -> None:
            self.elapsed_s += seconds

        def reboot(self, boot_id: int | None = None) -> None:
            self.head.reboot(boot_id)

        def take_buffer(self) -> list[bytes]:
            return []

        def send_chunks(self, chunks: Sequence[bytes]) -> None:
            pass

        def resend_largest(self) -> None:
            pass

    counter = _Count()
    sd.script(counter, **script_kw)
    return counter.elapsed_s


# Wrong chunks, one per wire.md reject vector: (code, check step) where each one must fail.
BAD_EXPECT: dict[str, tuple[int, int]] = {
    "bad_crc": (2, 6),
    "bad_magic": (3, 3),
    "bad_version": (4, 4),
    "bad_header_len": (5, 5),
    "too_large": (6, 2),
    "inconsistent_len": (7, 7),
    "inconsistent_status_len": (7, 7),
    "unknown_kind": (4, 7),
    "empty_chunk": (8, 8),
    "tag_mask_mismatch": (8, 8),
    "valid_not_reported": (8, 8),
    "geometry_hash_mismatch": (8, 8),
    "device_mismatch": (9, 9),
}
BAD_KINDS: tuple[str, ...] = tuple(BAD_EXPECT)


def _base_frames(head: FakeHead, n_range: int = 5, n_imu: int = 1) -> tuple[list[bytes], list[bytes]]:
    """Valid frames for Tag 0 of `head`, spread over one second."""
    raws = [(250, 310, 420, 180), (252, 309, 418, 182), (255, 307, 415, 185), (257, 305, 413, 187)]
    ranges = [encode_range(i * (990_000 // max(1, n_range)), tag_id=0, round_seq=i & 0xFF, raw_cm=raws[i % 4],
                           rssi_dbm=(-70, -78, -82, -75)) for i in range(n_range)]
    tag = head.tags[0]
    table = _gait_table(0)
    imus = []
    for j in range(n_imu):
        samples = [_SAMPLE_HEAD.pack((10 * j + i) & 0xFFFF, 1000 + 100 * j + 10 * i, SAMPLE_FLAGS) + table[10 * j + i]
                   for i in range(10)]
        imus.append(encode_imu_frame(j * (990_000 // max(1, n_imu)) + 3000, tag_mac=tag.mac,
                                     tag_boot_id=tag.boot_id, link_seq=j, tx_tag_ms=1000 + 100 * j + 91,
                                     samples=samples, tag_id=0, link_rssi=-55))
    return ranges, imus


def _chunk_from(head: FakeHead, **kw) -> bytes:
    """A chunk with the head's identity (current boot id and seq, not advanced) and the given parts."""
    utc = 0 if head.utc_offset_us is None else head.head_us_now + head.utc_offset_us
    kw.setdefault("geometry_hash", geometry_hash(head.geometry_id))
    return encode_chunk(boot_id=head.boot_id, seq=head.seq, head_us_start=head.head_us_now, utc_us_start=utc, **kw)


def max_size_chunk(head: FakeHead | None = None) -> bytes:
    """A valid chunk of exactly 11,284 B: 128 range frames, 32 IMU frames x 10 samples, all three blocks."""
    head = head or FakeHead()
    ranges, imus = _base_frames(head, 128, 32)
    blocks = [head.system_block(), head.health_block(), head.survey_block()]
    return _chunk_from(head, ranges=ranges, imus=imus, blocks=blocks)


def bad_chunk(kind: str, head: FakeHead | None = None) -> bytes:
    """A chunk that fails exactly at the wire.md check named by `kind` (see BAD_EXPECT); every earlier check
    passes (CRC recomputed unless the CRC is the fault). Uses the head's MAC, boot id, current seq (not
    advanced) and geometry; default head: FakeHead() for TEST_DEVICE_MAC."""
    head = head or FakeHead()
    ranges, imus = _base_frames(head)
    if kind == "bad_crc":
        good = _chunk_from(head, ranges=ranges, imus=imus)
        return good[:-4] + _U32.pack(_U32.unpack(good[-4:])[0] ^ 0xFFFFFFFF)
    if kind == "bad_magic":
        return _chunk_from(head, ranges=ranges, imus=imus, magic=0xC2)
    if kind == "bad_version":
        return _chunk_from(head, ranges=ranges, imus=imus, version=WIRE_VERSION + 1)
    if kind == "bad_header_len":
        return _chunk_from(head, ranges=ranges, imus=imus, header_len=40)
    if kind == "too_large":
        ranges, imus = _base_frames(head, 129, 32)      # 24 B over the limit
        blocks = [head.system_block(), head.health_block(), head.survey_block()]
        return _chunk_from(head, ranges=ranges, imus=imus, blocks=blocks)
    if kind == "inconsistent_len":
        return _chunk_from(head, ranges=ranges, imus=imus, n_range=len(ranges) + 1)
    if kind == "inconsistent_status_len":
        block = head.health_block()
        return _chunk_from(head, ranges=ranges, imus=imus, blocks=[block], status_len=len(block) - 4)
    if kind == "unknown_kind":
        block = encode_block(4, bytes(8), at_offset_us=1000)
        return _chunk_from(head, ranges=ranges, imus=imus, blocks=[block])
    if kind == "empty_chunk":
        return _chunk_from(head)
    if kind == "tag_mask_mismatch":
        return _chunk_from(head, ranges=ranges, imus=imus, tag_mask=0b11)
    if kind == "valid_not_reported":
        ranges[0] = encode_range(0, raw_cm=(250, 310, 420, 0), rssi_dbm=(-70, -78, -82, 0),
                                 reported_mask=0b0111, valid_mask=0b1111, rssi_mask=0b0111)
        return _chunk_from(head, ranges=ranges, imus=imus)
    if kind == "geometry_hash_mismatch":
        return _chunk_from(head, ranges=ranges, imus=imus, blocks=[head.system_block()],
                           geometry_hash=geometry_hash(head.geometry_id + "-other"))
    if kind == "device_mismatch":
        other = bytearray(mac_bytes(head.mac))
        other[5] ^= 0x01
        block = head.system_block()
        payload = bytes(other) + block[BLOCK.size + 6:]
        return _chunk_from(head, ranges=ranges, imus=imus, blocks=[block[:BLOCK.size] + payload])
    raise ValueError(f"unknown bad chunk kind {kind!r}; one of {', '.join(BAD_KINDS)}")


def bad_request_framing() -> bytes:
    """A request body whose second len runs past the end of the body -> HTTP 400."""
    good = _chunk_from(FakeHead(), ranges=_base_frames(FakeHead())[0])
    return frame_request([good]) + _U32.pack(5000) + bytes(10)


# --------------------------------------------------------------------------------------------------------------
# 6. CLI
# --------------------------------------------------------------------------------------------------------------

@dataclass
class _Stats:
    requests: int = 0                       # HTTP attempts
    chunks: int = 0                         # chunks the server answered with a code
    codes: Counter = field(default_factory=Counter)
    http: Counter = field(default_factory=Counter)

    def line(self, mac: str, name: str) -> str:
        codes = {ACK_CODES.get(k, k): v for k, v in sorted(self.codes.items())}
        return (f"{device_id(mac)} scenario={name} requests={self.requests} chunks={self.chunks} "
                f"codes={codes} http={dict(sorted(self.http.items(), key=str))}")


def _post(client, url: str, token: str, body: bytes, stats: _Stats, timeout: float):
    """POST one request body. Returns (status or None, codes or None, server_utc_us or None)."""
    stats.requests += 1
    try:
        resp = client.post(url, content=body, timeout=timeout,
                           headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"})
    except Exception as exc:                            # httpx.HTTPError and friends: treated as "no ack"
        stats.http[type(exc).__name__] += 1
        return None, None, None
    stats.http[resp.status_code] += 1
    if resp.status_code != 200:
        return resp.status_code, None, None
    try:
        server_utc, codes = decode_ack(resp.content)
    except ValueError:
        stats.http["bad_ack"] += 1
        return resp.status_code, None, None
    stats.chunks += len(codes)
    stats.codes.update(codes)
    return 200, codes, server_utc


class _LiveUploader:
    """Real-time driver: same script interface as Uploader, real HTTP. One window per real second; resend rules
    of wire.md (backoff 1..30 s on 5xx/timeouts, 401 retry after 60 s, 413 halves the batch, 400 drops the
    request's chunks); UTC estimate from each ack's server_utc_us (round trips over 1 s are ignored)."""

    def __init__(self, head: FakeHead, client, url: str, token: str, *, capacity: int = 830,
                 timeout: float = 10.0):
        self.head = head
        self.client = client
        self.url = url.rstrip("/") + "/d/v1/chunks"
        self.token = token
        self.capacity = capacity
        self.timeout = timeout
        self.stats = _Stats()
        self.buffer: deque[bytes] = deque()
        self.sent: list[bytes] = []
        self._t0 = time.monotonic()
        self._boot_mono = self._t0
        self._ticks = 0
        self._next_attempt = 0.0
        self._fails = 0
        self._limit = MAX_REQUEST_CHUNKS
        head.set_utc(None)                              # a real Head has no estimate before its first ack
        head.buffer_capacity = capacity

    def _head_us(self, mono: float) -> float:
        return self.head.head_us0 + (mono - self._boot_mono) * 1e6

    def run(self, seconds: int, *, online: bool = True) -> None:
        for _ in range(seconds):
            self._ticks += 1
            tick_end = self._t0 + self._ticks
            delay = tick_end - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.head.buffer_used = len(self.buffer)
            chunk = self.head.next_chunk()
            if chunk is not None:
                if len(self.buffer) >= self.capacity:
                    self.buffer.popleft()
                    self.head.mark_overflow()
                self.buffer.append(chunk)
            if self.buffer and time.monotonic() >= self._next_attempt:
                if online:
                    self._drain(deadline=tick_end + 0.9)
                else:
                    self._fail()

    def _fail(self) -> None:
        self.head.note_upload_failure()
        self._next_attempt = time.monotonic() + BACKOFF_S[min(self._fails, len(BACKOFF_S) - 1)]
        self._fails += 1

    def _send(self, batch: list[bytes]) -> str:
        """One request; returns 'ok', 'drop' (400), 'retry_smaller' (413), 'auth' (401) or 'fail'."""
        body = frame_request(batch)
        t_send = time.monotonic()
        status, codes, server_utc = _post(self.client, self.url, self.token, body, self.stats, self.timeout)
        t_recv = time.monotonic()
        if status == 200 and codes is not None:
            self.sent.append(body)
            rejected = sum(1 for c in codes if c >= 2)
            if rejected:
                self.head.note_rejected(rejected)
            if t_recv - t_send <= 1.0:
                mid = (self._head_us(t_send) + self._head_us(t_recv)) / 2
                self.head.set_utc(round(server_utc - mid))
            return "ok"
        if status == 400:
            self.head.note_rejected(len(batch))
            return "drop"
        if status == 413:
            return "retry_smaller"
        if status == 401:
            return "auth"
        return "fail"

    def _drain(self, deadline: float) -> None:
        while self.buffer:
            batch = _take_batch(deque(self.buffer), self._limit)
            result = self._send(batch)
            if result in ("ok", "drop"):
                for _ in batch:
                    self.buffer.popleft()
            elif result == "retry_smaller":
                self._limit = max(1, len(batch) // 2)
                continue
            elif result == "auth":
                self._next_attempt = time.monotonic() + 60
                return
            else:
                self._fail()
                return
            if time.monotonic() > deadline:
                break
        self._fails = 0
        self._next_attempt = 0.0

    def reboot(self, boot_id: int | None = None) -> None:
        self.head.reboot(boot_id)
        self.head.buffer_capacity = self.capacity
        self.buffer.clear()
        self._boot_mono = self._t0 + self._ticks
        self._fails = 0
        self._next_attempt = 0.0

    def take_buffer(self) -> list[bytes]:
        out = list(self.buffer)
        self.buffer.clear()
        return out

    def send_chunks(self, chunks: Sequence[bytes]) -> None:
        pending = deque(chunks)
        while pending:
            self._send(_take_batch(pending))

    def resend_largest(self) -> None:
        if self.sent:
            body = max(self.sent, key=lambda b: len(unframe_request(b)))
            _post(self.client, self.url, self.token, body, self.stats, self.timeout)


def _run_device(args, index: int, mac: str, token: str, script_opts: dict, out: dict) -> None:
    import httpx

    name = args.scenario
    seed = args.seed + index
    with httpx.Client(timeout=args.timeout) as client:
        if args.realtime:
            sd, head_kw, capacity, script_kw = _split_opts(name, script_opts)
            head = FakeHead(mac, seed=seed, **head_kw)
            drv = _LiveUploader(head, client, args.url, token, capacity=capacity, timeout=args.timeout)
            sd.script(drv, **script_kw)
            out[mac] = drv.stats
            return
        # Fast mode: chunk times end about now, requests go back to back.
        now_us = time.time_ns() // 1000
        start = now_us - scenario_span_s(name, **script_opts) * 1_000_000
        requests = scenario(name, seed=seed, start_utc_us=start, mac=mac, **script_opts)
        stats = _Stats()
        url = args.url.rstrip("/") + "/d/v1/chunks"
        for req in requests:
            for delay in (*BACKOFF_S, None):           # resend on 5xx / timeout / connection failure
                status, _, _ = _post(client, url, token, req.body, stats, args.timeout)
                if status is not None and status < 500:
                    break
                if delay is None:
                    print(f"{device_id(mac)}: gave up on a request of {req.n_chunks} chunks", file=sys.stderr)
                    break
                time.sleep(delay)
        out[mac] = stats


def _parse_opt(text: str) -> tuple[str, object]:
    key, _, value = text.partition("=")
    if not value:
        raise argparse.ArgumentTypeError(f"--opt needs KEY=VALUE, got {text!r}")
    if value.lower() in ("none", "null"):
        return key, None
    if "," in value:
        return key, tuple(float(v) for v in value.split(","))
    try:
        return key, int(value)
    except ValueError:
        return key, float(value)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="fake_head.py",
        description="Send synthetic Head chunks (wire v1) to the intake's POST /d/v1/chunks.",
        epilog="Scenarios: " + ", ".join(SCENARIOS) + ". Options per scenario: see the _sc_* docstrings.")
    p.add_argument("--url", default="http://localhost:8001", help="intake base URL (default %(default)s)")
    p.add_argument("--device", action="append", required=True, metavar="MAC=TOKEN",
                   help="Head MAC and its device token; repeat for several devices sent concurrently")
    p.add_argument("--scenario", default="normal", choices=SCENARIOS)
    p.add_argument("--duration", type=int, metavar="S", help="duration_s for scenarios that take it")
    p.add_argument("--opt", action="append", default=[], type=_parse_opt, metavar="KEY=VALUE",
                   help="other scenario option, e.g. offline_s=120, walk_pattern=30,60, capacity=20")
    p.add_argument("--seed", type=int, default=0, help="seed of the first device; device i uses seed + i")
    p.add_argument("--realtime", action="store_true",
                   help="one chunk per real second, real resend/backoff and UTC estimate from acks "
                        "(default: build the whole scenario, then send requests back to back, resending "
                        "on 5xx/timeouts after 1, 2, 4, 8, 16, 30 s)")
    p.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds")
    args = p.parse_args(argv)

    devices = []
    for item in args.device:
        mac, _, token = item.partition("=")
        try:
            mac_bytes(mac)
        except ValueError:
            p.error(f"bad MAC in --device {item!r}")
        if not token:
            p.error(f"--device needs MAC=TOKEN, got {item!r}")
        devices.append((mac.lower(), token))
    script_opts = dict(args.opt)
    if args.duration is not None:
        script_opts["duration_s"] = args.duration
    try:
        _split_opts(args.scenario, script_opts)
    except (TypeError, ValueError) as exc:
        p.error(str(exc))

    results: dict[str, _Stats] = {}
    threads = [threading.Thread(target=_run_device, args=(args, i, mac, token, script_opts, results), daemon=True)
               for i, (mac, token) in enumerate(devices)]
    for t in threads:
        t.start()
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    for mac, _ in devices:
        stats = results.get(mac)
        print(stats.line(mac, args.scenario) if stats else f"{device_id(mac)} failed (see error above)")
    return 0 if len(results) == len(devices) else 1


if __name__ == "__main__":
    sys.exit(main())
