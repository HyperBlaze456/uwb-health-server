"""pipeline/wire.py 단위 시험 (C1, IN-3).

시험용 묶음은 아래의 작은 인코더로 만듭니다. 해독기의 struct 형식 문자열을 쓰지 않고, wire.md 표의
offset마다 따로 써 넣어 해독기와 독립으로 둡니다.
"""

import json
import math
import random
import struct
import time
import zlib

import pytest

from uwbserver.pipeline import wire
from uwbserver.pipeline.wire import Code

SERVER_UTC = 1_793_000_000_000_000  # 2026-10-26 근처
DAY_US = 86_400_000_000
UTC_OK = SERVER_UTC - 5_000_000
DEVICE_MAC = bytes.fromhex("e072a1aca858")
OTHER_MAC = bytes.fromhex("e072a1aca859")
A1_MAC, A2_MAC, A3_MAC = (bytes.fromhex(f"e072a10000{k:02x}") for k in (1, 2, 3))
TAG0_MAC = bytes.fromhex("02a1b2c3d4e5")
TAG1_MAC = bytes.fromhex("02a1b2c3d4e6")
ZERO_MAC = bytes(6)
GEOMETRY_ID = b"sv-1a2b3c4d-2-0"
NAN = float("nan")
INF = float("inf")


# --- 시험용 인코더 (wire.md offset 그대로) ------------------------------------


def sample(seq=0, tag_ms=0, flags=0b101, raw=(100, -200, 16384, 1200, 5, -6, 7)):
    b = bytearray(21)
    struct.pack_into("<H", b, 0, seq)
    struct.pack_into("<I", b, 2, tag_ms)
    b[6] = flags
    struct.pack_into("<7h", b, 7, *raw)
    return bytes(b)


def range_frame(
    *,
    rx=1000,
    round_seq=1,
    slot_mask=0x05,
    valid=0xF,
    rssi=0xF,
    report=0x3F,
    tag_id=0,
    raw_cm=(120, 340, 560, 780),
    rssi_dbm=(-80, -81, -82, -83),
):
    b = bytearray(24)
    struct.pack_into("<I", b, 0, rx)
    struct.pack_into("<I", b, 4, round_seq)
    b[8] = slot_mask
    b[9] = valid | (rssi << 4)
    b[10] = report
    b[11] = tag_id
    struct.pack_into("<4h", b, 12, *raw_cm)
    struct.pack_into("<4b", b, 20, *rssi_dbm)
    return bytes(b)


def imu_frame(
    *,
    rx=2000,
    tag_mac=TAG0_MAC,
    tag_boot_id=0xAABBCCDD,
    link_seq=1,
    tx_tag_ms=5000,
    link_rssi=-40,
    fs=0x10,
    tag_id=0,
    samples=None,
    count=None,
):
    if samples is None:
        samples = [sample(seq=k, tag_ms=4900 + 10 * k) for k in range(10)]
    b = bytearray(24)
    struct.pack_into("<I", b, 0, rx)
    b[4:10] = tag_mac
    struct.pack_into("<I", b, 10, tag_boot_id)
    struct.pack_into("<H", b, 14, link_seq)
    struct.pack_into("<I", b, 16, tx_tag_ms)
    struct.pack_into("<b", b, 20, link_rssi)
    b[21] = fs
    b[22] = tag_id
    b[23] = len(samples) if count is None else count
    return bytes(b) + b"".join(samples)


def block(kind, payload, *, kind_version=1, at=0, length=None):
    b = bytearray(8)
    b[0] = kind
    b[1] = kind_version
    struct.pack_into("<H", b, 2, len(payload) if length is None else length)
    struct.pack_into("<I", b, 4, at)
    return bytes(b) + payload


SYSTEM_DEFAULT = dict(
    head_mac=DEVICE_MAC,
    tag_count=1,
    slot_ms=10,
    position_dimensions=3,
    flags=0b0111,  # surveyed, module_filter, imu_transport = 1 (espnow)
    geometry_source=1,
    fs=0x10,
    imu_acquisition_hz=100,
    imu_transport_hz=100,
    set_members=(A1_MAC, A2_MAC, A3_MAC, TAG0_MAC, ZERO_MAC),
    reserved=0,
    anchors=((0.0, 0.0, 0.0, 0.0), (4.5, 0.0, 0.0, 0.125), (0.0, 5.25, 0.0, -0.125), (4.5, 5.25, 2.5, 0.0)),
    firmware_id=bytes.fromhex("0123456789abcdef"),
    geometry_id=GEOMETRY_ID,
)


def system_payload(**kw):
    f = SYSTEM_DEFAULT | kw
    b = bytearray(152)
    b[0:6] = f["head_mac"]
    b[6] = f["tag_count"]
    b[7] = f["slot_ms"]
    b[8] = f["position_dimensions"]
    b[9] = f["flags"]
    b[10] = f["geometry_source"]
    b[11] = f["fs"]
    struct.pack_into("<H", b, 12, f["imu_acquisition_hz"])
    struct.pack_into("<H", b, 14, f["imu_transport_hz"])
    for k, m in enumerate(f["set_members"]):
        b[16 + 6 * k : 22 + 6 * k] = m
    struct.pack_into("<H", b, 46, f["reserved"])
    for k, a in enumerate(f["anchors"]):
        struct.pack_into("<4f", b, 48 + 16 * k, *a)
    b[112:120] = f["firmware_id"]
    gid = f["geometry_id"]
    b[120 : 120 + len(gid)] = gid
    assert len(b) == 152
    return bytes(b)


ANCHOR_SEEN = dict(
    flags=0b00111, state=2, rssi=-50, valid_mask=0xF, boot_id=11, range_count=100, round_seq=99, age_ms=20, range_age_ms=30
)
ANCHOR_UNSEEN = dict(
    flags=0, state=0, rssi=0, valid_mask=0, boot_id=0, range_count=0, round_seq=0, age_ms=0xFFFF, range_age_ms=0xFFFF
)
TAG_SEEN = dict(flags=0b11, link_rssi=-45, imu_age_ms=12, range_count=500, round_seq=499, range_age_ms=8, reserved=0)
TAG_UNSEEN = dict(flags=0, link_rssi=0, imu_age_ms=0xFFFF, range_count=0, round_seq=0, range_age_ms=0xFFFF, reserved=0)
HEALTH_DEFAULT = dict(
    flags=0b11010011,
    wifi_disconnect_reason=8,
    tag_count=1,
    buffer_used=3,
    buffer_capacity=830,
    counters=tuple(range(1, 23)),
    range_age_ms=10,
    imu_age_ms=0xFFFF,
    anchors=(ANCHOR_SEEN, ANCHOR_SEEN, ANCHOR_UNSEEN),
    tags=(TAG_SEEN, TAG_UNSEEN),
)


def health_payload(**kw):
    f = HEALTH_DEFAULT | kw
    b = bytearray(192)
    struct.pack_into("<H", b, 0, f["flags"])
    b[2] = f["wifi_disconnect_reason"]
    b[3] = f["tag_count"]
    struct.pack_into("<H", b, 4, f["buffer_used"])
    struct.pack_into("<H", b, 6, f["buffer_capacity"])
    struct.pack_into("<22I", b, 8, *f["counters"])
    struct.pack_into("<H", b, 96, f["range_age_ms"])
    struct.pack_into("<H", b, 98, f["imu_age_ms"])
    for k, a in enumerate(f["anchors"]):
        o = 100 + 20 * k
        b[o] = a["flags"]
        b[o + 1] = a["state"]
        struct.pack_into("<b", b, o + 2, a["rssi"])
        b[o + 3] = a["valid_mask"]
        struct.pack_into("<III", b, o + 4, a["boot_id"], a["range_count"], a["round_seq"])
        struct.pack_into("<HH", b, o + 16, a["age_ms"], a["range_age_ms"])
    for k, t in enumerate(f["tags"]):
        o = 160 + 16 * k
        b[o] = t["flags"]
        struct.pack_into("<b", b, o + 1, t["link_rssi"])
        struct.pack_into("<H", b, o + 2, t["imu_age_ms"])
        struct.pack_into("<II", b, o + 4, t["range_count"], t["round_seq"])
        struct.pack_into("<HH", b, o + 12, t["range_age_ms"], t["reserved"])
    return bytes(b)


def anchors_with(k, **kw):
    a = list(HEALTH_DEFAULT["anchors"])
    a[k] = a[k] | kw
    return tuple(a)


def tags_with(k, **kw):
    t = list(HEALTH_DEFAULT["tags"])
    t[k] = t[k] | kw
    return tuple(t)


MEAS = tuple(tuple((0, 0, 0) if i == j else (500 + 10 * i + j, 20, 2) for j in range(4)) for i in range(4))
DIST = tuple(tuple(0.0 if i == j else 4.0 + 0.5 * (i + j) for j in range(4)) for i in range(4))
XYZ = ((0.0, 0.0, 0.0), (4.5, 0.0, 0.0), (0.0, 5.25, 0.0), (4.5, 5.25, 2.5))
SURVEY_DEFAULT = dict(
    status=1,
    flags=0b100101,  # have_measurements, solution.ok, height_solved
    heights_known=0b0011,
    reserved=0,
    survey_id=2,
    revision=1,
    message=b"ok",
    solution_reason=b"ok",
    heights_m=(1.5, 2.25, 0.0, 0.0),
    measurements=MEAS,
    distances_m=DIST,
    max_asymmetry_m=0.03125,
    max_mad_m=0.015625,
    xyz_m=XYZ,
    fit_rms_m=0.0625,
    flatness_m=0.5,
    solved_height_m=1.25,
    height_span_m=0.75,
)


def survey_payload(**kw):
    f = SURVEY_DEFAULT | kw
    b = bytearray(304)
    b[0] = f["status"]
    b[1] = f["flags"]
    b[2] = f["heights_known"]
    b[3] = f["reserved"]
    struct.pack_into("<HH", b, 4, f["survey_id"], f["revision"])
    b[8 : 8 + len(f["message"])] = f["message"]
    b[32 : 32 + len(f["solution_reason"])] = f["solution_reason"]
    struct.pack_into("<4f", b, 56, *f["heights_m"])
    for i in range(4):
        for j in range(4):
            struct.pack_into("<hHH", b, 72 + 6 * (4 * i + j), *f["measurements"][i][j])
    struct.pack_into("<16f", b, 168, *(x for row in f["distances_m"] for x in row))
    struct.pack_into("<f", b, 232, f["max_asymmetry_m"])
    struct.pack_into("<f", b, 236, f["max_mad_m"])
    struct.pack_into("<12f", b, 240, *(x for row in f["xyz_m"] for x in row))
    struct.pack_into("<4f", b, 288, f["fit_rms_m"], f["flatness_m"], f["solved_height_m"], f["height_span_m"])
    assert len(b) == 304
    return bytes(b)


def meas_with(i, j, value):
    m = [list(row) for row in MEAS]
    m[i][j] = value
    return tuple(tuple(row) for row in m)


def chunk(
    ranges=None,
    imus=None,
    blocks=(),
    *,
    magic=0xC1,
    version=1,
    header_len=48,
    head_boot_id=0x0BADBEEF,
    chunk_seq=7,
    head_us_start=12_000_000,
    utc_us_start=UTC_OK,
    geometry_hash=None,
    flags=1,
    n_range=None,
    n_imu=None,
    dropped_range=0,
    dropped_imu=0,
    tag_mask=None,
    status_len=None,
    reserved=0,
    extra=b"",
    crc=None,
):
    ranges = [range_frame()] if ranges is None else list(ranges)
    imus = [imu_frame()] if imus is None else list(imus)
    status = b"".join(blocks)
    if tag_mask is None:
        tag_mask = 0
        for t in [r[11] for r in ranges] + [f[22] for f in imus]:
            if t < 2:
                tag_mask |= 1 << t
    h = bytearray(48)
    h[0] = magic
    h[1] = version
    struct.pack_into("<H", h, 2, header_len)
    struct.pack_into("<I", h, 4, head_boot_id)
    struct.pack_into("<I", h, 8, chunk_seq)
    struct.pack_into("<q", h, 12, head_us_start)
    struct.pack_into("<q", h, 20, utc_us_start)
    struct.pack_into("<I", h, 28, zlib.crc32(GEOMETRY_ID) if geometry_hash is None else geometry_hash)
    struct.pack_into("<H", h, 32, flags)
    struct.pack_into("<H", h, 34, len(ranges) if n_range is None else n_range)
    struct.pack_into("<H", h, 36, len(imus) if n_imu is None else n_imu)
    struct.pack_into("<H", h, 38, dropped_range)
    struct.pack_into("<H", h, 40, dropped_imu)
    struct.pack_into("<H", h, 42, tag_mask)
    struct.pack_into("<H", h, 44, len(status) if status_len is None else status_len)
    struct.pack_into("<H", h, 46, reserved)
    body = bytes(h) + b"".join(ranges) + b"".join(imus) + status + extra
    return body + struct.pack("<I", zlib.crc32(body) if crc is None else crc)


def recrc(data):
    body = bytes(data[:-4])
    return body + struct.pack("<I", zlib.crc32(body))


def sys_chunk(*, at=0, **kw):
    gid = kw.get("geometry_id", GEOMETRY_ID).split(b"\0", 1)[0]
    return chunk(blocks=[block(1, system_payload(**kw), at=at)], geometry_hash=zlib.crc32(gid))


def health_chunk(*, at=0, **kw):
    return chunk(blocks=[block(2, health_payload(**kw), at=at)])


def survey_chunk(*, at=0, **kw):
    return chunk(blocks=[block(3, survey_payload(**kw), at=at)])


def all_blocks(**sys_kw):
    return [block(1, system_payload(**sys_kw), at=10), block(2, health_payload(), at=20), block(3, survey_payload(), at=30)]


def full_second():
    """1-Tag 보통의 1초: 거리 100, IMU 10 × 10. 4792 B."""
    ranges = [range_frame(rx=10_000 * k, round_seq=k) for k in range(100)]
    imus = [imu_frame(rx=100_000 * k + 5, link_seq=k) for k in range(10)]
    return chunk(ranges, imus)


def two_tag_second():
    """2-Tag 1초: 거리 100(섞임), IMU 20. 7132 B."""
    ranges = [range_frame(rx=10_000 * k, round_seq=k // 2, tag_id=k % 2) for k in range(100)]
    imus = [
        imu_frame(rx=50_000 * k, link_seq=k // 2, tag_id=k % 2, tag_mac=(TAG0_MAC, TAG1_MAC)[k % 2]) for k in range(20)
    ]
    return chunk(ranges, imus)


def max_size():
    """상한: 거리 128, IMU 32 × 10, 상태 블록 셋. 11,284 B."""
    ranges = [range_frame(rx=7_000 * k, tag_id=k % 2) for k in range(128)]
    imus = [imu_frame(rx=30_000 * k, tag_id=k % 2, tag_mac=(TAG0_MAC, TAG1_MAC)[k % 2]) for k in range(32)]
    blocks = [
        block(1, system_payload(tag_count=2, set_members=(A1_MAC, A2_MAC, A3_MAC, TAG0_MAC, TAG1_MAC))),
        block(2, health_payload(tag_count=2, tags=(TAG_SEEN, TAG_SEEN))),
        block(3, survey_payload()),
    ]
    return chunk(ranges, imus, blocks)


def check(data, *, mac=DEVICE_MAC, now=SERVER_UTC):
    return wire.check_chunk(data, device_mac=mac, server_utc_us=now)


def accepted(data, **kw):
    r = check(data, **kw)
    assert isinstance(r, wire.Chunk), r
    return r


def rejected(data, **kw):
    r = check(data, **kw)
    assert isinstance(r, wire.Rejection), "expected a rejection"
    return r


# --- 받는 묶음 -----------------------------------------------------------------


def test_sizes_from_wire_md():
    assert len(full_second()) == 4792
    assert len(two_tag_second()) == 7132
    assert len(max_size()) == wire.MAX_CHUNK_LEN == 11_284


@pytest.mark.parametrize("make", [chunk, full_second, two_tag_second, max_size, lambda: chunk(blocks=all_blocks())])
def test_valid_chunks_are_accepted(make):
    data = make()
    c = accepted(data)
    assert c.data == data
    assert c.crc32 == zlib.crc32(data[:-4])


def test_status_only_chunk_is_accepted():
    c = accepted(chunk([], [], [block(2, health_payload())]))
    assert c.ranges == () and c.imu_frames == ()
    assert c.header.tag_mask == 0
    assert c.status_mask == 0b010


def test_header_round_trip():
    data = chunk(
        head_boot_id=0xDEADBEEF,
        chunk_seq=0xFFFFFFFF,
        head_us_start=2**62,
        utc_us_start=UTC_OK,
        flags=0b11,
        dropped_range=3,
        dropped_imu=4,
    )
    h = accepted(data).header
    assert (h.magic, h.version, h.header_len) == (0xC1, 1, 48)
    assert (h.head_boot_id, h.chunk_seq, h.head_us_start, h.utc_us_start) == (0xDEADBEEF, 0xFFFFFFFF, 2**62, UTC_OK)
    assert h.geometry_hash == zlib.crc32(GEOMETRY_ID)
    assert (h.flags, h.n_range, h.n_imu_frames, h.dropped_range, h.dropped_imu_frames) == (3, 1, 1, 3, 4)
    assert (h.tag_mask, h.status_len, h.reserved) == (1, 0, 0)
    assert wire.peek_key(data) == (0xDEADBEEF, 0xFFFFFFFF)
    assert accepted(data).key == (0xDEADBEEF, 0xFFFFFFFF)


def test_range_frame_round_trip_no_value_fixing():
    f = range_frame(
        rx=1_999_999,
        round_seq=0xFFFFFFFF,
        slot_mask=0xA5,
        valid=0b0101,
        rssi=0b0011,
        report=0b0011_1111,
        tag_id=0,
        raw_cm=(1, 32767, 3000, -32767),  # reported지만 valid 아닌 칸은 포화값 그대로
        rssi_dbm=(-128, -1, 0, 0),
    )
    r = accepted(chunk([f])).ranges[0]
    assert (r.rx_offset_us, r.round_seq, r.slot_mask) == (1_999_999, 0xFFFFFFFF, 0xA5)
    assert (r.valid_mask, r.rssi_mask, r.report_flags, r.reported_mask) == (0b0101, 0b0011, 0x3F, 0xF)
    assert r.explicit_anchor_ids and r.configuration_verified
    assert r.tag_id == 0
    assert r.raw_cm == (1, 32767, 3000, -32767)
    assert r.rssi_dbm == (-128, -1, 0, 0)


def test_range_frame_unreported_slots():
    f = range_frame(valid=0b0001, rssi=0b0001, report=0b0001, raw_cm=(250, 0, 0, 0), rssi_dbm=(-70, 0, 0, 0))
    r = accepted(chunk([f])).ranges[0]
    assert (r.reported_mask, r.explicit_anchor_ids, r.configuration_verified) == (1, False, False)


def test_imu_frame_round_trip():
    samples = [sample(seq=65535 - k, tag_ms=0xFFFFFFF0 + k, flags=k % 8, raw=(k, -k, 32767, -32768, 1, 2, 3)) for k in range(3)]
    f = imu_frame(
        rx=1_999_999,
        tag_mac=TAG1_MAC,
        tag_boot_id=0x01020304,
        link_seq=65535,
        tx_tag_ms=0xFFFFFFFF,
        link_rssi=0,
        fs=0x33,
        tag_id=1,
        samples=samples,
    )
    m = accepted(chunk([], [f])).imu_frames[0]
    assert m.rx_offset_us == 1_999_999
    assert m.tag_mac == TAG1_MAC
    assert (m.tag_boot_id, m.link_seq, m.tx_tag_ms, m.link_rssi, m.fs, m.tag_id, m.count) == (
        0x01020304, 65535, 0xFFFFFFFF, 0, 0x33, 1, 3,
    )  # fmt: skip
    assert [(s.seq, s.tag_ms, s.flags, s.raw) for s in m.samples] == [
        (65535 - k, 0xFFFFFFF0 + k, k % 8, (k, -k, 32767, -32768, 1, 2, 3)) for k in range(3)
    ]


def test_chunk_helpers():
    imus = [
        imu_frame(rx=1, tag_id=1, tag_mac=TAG1_MAC, tag_boot_id=9),
        imu_frame(rx=2, tag_id=0, tag_mac=TAG0_MAC, tag_boot_id=5),
        imu_frame(rx=3, tag_id=0, tag_mac=bytes.fromhex("02000000beef"), tag_boot_id=6),
    ]
    c = accepted(chunk(None, imus, all_blocks()))
    assert c.status_mask == 0b111
    assert c.block(wire.KIND_HEALTH).kind == 2
    assert c.block(4) is None
    assert (c.first_imu(0).tag_mac, c.first_imu(0).tag_boot_id) == (TAG0_MAC, 5)
    assert (c.first_imu(1).tag_mac, c.first_imu(1).tag_boot_id) == (TAG1_MAC, 9)
    assert accepted(chunk()).first_imu(1) is None


# --- 상태 블록 해독 (AP-3) ------------------------------------------------------


def test_system_block_round_trip():
    c = accepted(chunk(blocks=[block(1, system_payload(), at=1_999_999)]))
    b = c.block(wire.KIND_SYSTEM)
    assert (b.kind, b.kind_version, b.len, b.at_offset_us) == (1, 1, 152, 1_999_999)
    assert wire.decode_block(b) == {
        "head_mac": "e0:72:a1:ac:a8:58",
        "tag_count": 1,
        "slot_ms": 10,
        "position_dimensions": 3,
        "flags": 0b0111,
        "geometry_source": 1,
        "fs": 0x10,
        "imu_acquisition_hz": 100,
        "imu_transport_hz": 100,
        "set_members": ["e0:72:a1:00:00:01", "e0:72:a1:00:00:02", "e0:72:a1:00:00:03", "02:a1:b2:c3:d4:e5", None],
        "reserved": 0,
        "anchors": [
            {"x": 0.0, "y": 0.0, "z": 0.0, "range_bias_m": 0.0},
            {"x": 4.5, "y": 0.0, "z": 0.0, "range_bias_m": 0.125},
            {"x": 0.0, "y": 5.25, "z": 0.0, "range_bias_m": -0.125},
            {"x": 4.5, "y": 5.25, "z": 2.5, "range_bias_m": 0.0},
        ],
        "firmware_id": "0123456789abcdef",
        "geometry_id": GEOMETRY_ID.decode(),
    }


def test_health_block_round_trip():
    c = accepted(health_chunk())
    d = wire.decode_block(c.block(wire.KIND_HEALTH))
    assert list(d) == [
        "flags", "wifi_disconnect_reason", "tag_count", "buffer_used", "buffer_capacity", "counters",
        "range_age_ms", "imu_age_ms", "anchors", "tags",
    ]  # fmt: skip
    assert (d["flags"], d["wifi_disconnect_reason"], d["tag_count"], d["buffer_used"], d["buffer_capacity"]) == (
        0b11010011, 8, 1, 3, 830,
    )  # fmt: skip
    assert list(d["counters"]) == list(wire.HEALTH_COUNTERS)
    assert d["counters"]["range_count"] == 1
    assert d["counters"]["chunks_dropped_overflow"] == 20
    assert d["counters"]["upload_failures"] == 22
    assert (d["range_age_ms"], d["imu_age_ms"]) == (10, 0xFFFF)  # 0xFFFF는 그대로
    assert d["anchors"] == [ANCHOR_SEEN, ANCHOR_SEEN, ANCHOR_UNSEEN]
    assert d["tags"] == [TAG_SEEN, TAG_UNSEEN]


def test_survey_block_round_trip():
    c = accepted(survey_chunk())
    d = wire.decode_block(c.block(wire.KIND_SURVEY))
    assert d == {
        "status": 1,
        "flags": 0b100101,
        "heights_known": 0b0011,
        "reserved": 0,
        "survey_id": 2,
        "revision": 1,
        "message": "ok",
        "solution_reason": "ok",
        "heights_m": [1.5, 2.25, 0.0, 0.0],
        "measurements": [
            [{"median_cm": m[0], "n": m[1], "mad_cm": m[2]} for m in row] for row in MEAS
        ],
        "distances_m": [list(row) for row in DIST],
        "max_asymmetry_m": 0.03125,
        "max_mad_m": 0.015625,
        "xyz_m": [list(row) for row in XYZ],
        "fit_rms_m": 0.0625,
        "flatness_m": 0.5,
        "solved_height_m": 1.25,
        "height_span_m": 0.75,
    }


def test_decode_payload_functions_check_length():
    for fn in (wire.decode_system, wire.decode_health, wire.decode_survey):
        with pytest.raises(ValueError):
            fn(b"\0" * 10)
    with pytest.raises(ValueError):
        wire.decode_block(wire.StatusBlock(9, 1, 0, 0, b""))
    with pytest.raises(ValueError):
        wire.decode_block(wire.StatusBlock(1, 2, 152, 0, system_payload()))


def test_chunk_to_dict_is_json_ready():
    c = accepted(chunk(blocks=all_blocks()))
    d = wire.chunk_to_dict(c)
    assert json.loads(json.dumps(d)) == d
    assert set(d) == {"header", "ranges", "imu_frames", "status", "crc32"}
    assert d["crc32"] == f"{zlib.crc32(c.data[:-4]):08x}"
    assert set(d["status"]) == {"system", "health", "survey"}
    assert d["status"]["health"]["at_offset_us"] == 20
    assert d["imu_frames"][0]["tag_mac"] == "02:a1:b2:c3:d4:e5"
    assert d["imu_frames"][0]["samples"][0] == {"seq": 0, "tag_ms": 4900, "flags": 0b101, "raw": [100, -200, 16384, 1200, 5, -6, 7]}
    assert d["ranges"][0]["raw_cm"] == [120, 340, 560, 780]
    assert d["header"]["geometry_hash"] == zlib.crc32(GEOMETRY_ID)


# --- 거절 코드와 검사 단계 ------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "code", "step", "detail"),
    [
        pytest.param(lambda: b"", Code.INCONSISTENT, 2, "len", id="len0"),
        pytest.param(lambda: chunk()[:51], Code.INCONSISTENT, 2, "len", id="len51"),
        pytest.param(lambda: max_size() + b"\0", Code.TOO_LARGE, 2, "len", id="len11285"),
        pytest.param(lambda: chunk(magic=0xC2), Code.MAGIC, 3, "header.magic", id="magic"),
        pytest.param(lambda: chunk(version=2), Code.VERSION, 4, "header.version", id="version2"),
        pytest.param(lambda: chunk(version=0), Code.VERSION, 4, "header.version", id="version0"),
        pytest.param(lambda: chunk(header_len=47), Code.HEADER_LEN, 5, "header.header_len", id="header_len"),
        pytest.param(lambda: chunk(crc=0), Code.CRC, 6, "crc32", id="crc"),
        pytest.param(lambda: chunk(n_range=129), Code.INCONSISTENT, 7, "header.n_range", id="n_range>128"),
        pytest.param(lambda: chunk(n_imu=33), Code.INCONSISTENT, 7, "header.n_imu_frames", id="n_imu>32"),
        pytest.param(lambda: chunk([range_frame()], [], n_range=5), Code.INCONSISTENT, 7, "len", id="ranges-overrun"),
        pytest.param(lambda: chunk(n_imu=2), Code.INCONSISTENT, 7, "len", id="imu-missing"),
        pytest.param(
            lambda: chunk(imus=[imu_frame(samples=[sample()] * 5, count=10)]), Code.INCONSISTENT, 7, "len",
            id="samples-overrun",
        ),  # fmt: skip
        pytest.param(lambda: chunk(imus=[imu_frame(samples=[], count=0)]), Code.INCONSISTENT, 7, "imu[0].count", id="count0"),
        pytest.param(
            lambda: chunk(imus=[imu_frame(), imu_frame(rx=3000, samples=[sample()] * 11)]), Code.INCONSISTENT, 7,
            "imu[1].count", id="count11",
        ),  # fmt: skip
        pytest.param(
            lambda: chunk(blocks=[block(2, health_payload())], status_len=199), Code.INCONSISTENT, 7,
            "header.status_len", id="status_len-short",
        ),  # fmt: skip
        pytest.param(
            lambda: chunk(blocks=[block(2, health_payload())], status_len=201), Code.INCONSISTENT, 7,
            "header.status_len", id="status_len-long",
        ),  # fmt: skip
        pytest.param(lambda: chunk(extra=b"\0"), Code.INCONSISTENT, 7, "header.status_len", id="trailing-byte"),
        pytest.param(
            lambda: chunk(blocks=[block(2, health_payload()), b"\0\0\0"]), Code.INCONSISTENT, 7, "header.status_len",
            id="blocks-sum",
        ),  # fmt: skip
        pytest.param(
            lambda: chunk(blocks=[block(2, health_payload()), block(1, system_payload())]), Code.INCONSISTENT, 7,
            "block[1].kind", id="kind-order",
        ),  # fmt: skip
        pytest.param(
            lambda: chunk(blocks=[block(2, health_payload()), block(2, health_payload())]), Code.INCONSISTENT, 7,
            "block[1].kind", id="kind-twice",
        ),  # fmt: skip
        pytest.param(
            lambda: chunk(blocks=[block(1, system_payload()[:150])]), Code.INCONSISTENT, 7, "block[0].len",
            id="fixed-len",
        ),  # fmt: skip
        pytest.param(
            lambda: chunk(blocks=[block(2, health_payload(), length=300)]), Code.INCONSISTENT, 7, "block[0].len",
            id="block-overrun",
        ),  # fmt: skip
        pytest.param(lambda: chunk(blocks=[block(4, b"\0" * 12)]), Code.VERSION, 7, "block[0].kind", id="unknown-kind"),
        pytest.param(lambda: chunk(blocks=[block(0, b"")]), Code.VERSION, 7, "block[0].kind", id="kind0"),
        pytest.param(
            lambda: chunk(blocks=[block(2, health_payload(), kind_version=2)]), Code.VERSION, 7, "block[0].kind_version",
            id="unknown-kind_version",
        ),  # fmt: skip
        pytest.param(
            lambda: chunk(blocks=[block(1, system_payload()), block(9, b"\1\2\3")]), Code.VERSION, 7, "block[1].kind",
            id="known-then-unknown",
        ),  # fmt: skip
        pytest.param(lambda: chunk([], [], tag_mask=0), Code.BAD_FIELD, 8, "empty", id="empty"),
        pytest.param(lambda: sys_chunk(head_mac=OTHER_MAC), Code.DEVICE_MISMATCH, 9, "system.head_mac", id="device"),
    ],
)
def test_reject_codes(make, code, step, detail):
    r = rejected(make())
    assert (r.code, r.check, r.detail) == (code, step, detail)


def test_min_len_52_passes_length_check():
    data = chunk([], [], tag_mask=0)
    assert len(data) == wire.MIN_CHUNK_LEN == 52
    assert rejected(data).check == 8  # 길이·구조는 통과, 빈 묶음이라 필드에서 걸림


def test_unknown_kind_does_not_hide_inconsistency():
    # wire.md 모호 해소: 종류를 몰라도 판정되는 inconsistent가 version보다 먼저.
    r = rejected(chunk(blocks=[block(1, system_payload()[:100]), block(9, b"")]))
    assert (r.code, r.check, r.detail) == (Code.INCONSISTENT, 7, "block[0].len")
    r = rejected(chunk(blocks=[block(9, b"")], status_len=9))
    assert (r.code, r.check) == (Code.INCONSISTENT, 7)


def test_device_mac_only_checked_with_system_block():
    assert isinstance(check(chunk(), mac=OTHER_MAC), wire.Chunk)
    assert isinstance(check(health_chunk(), mac=OTHER_MAC), wire.Chunk)
    assert rejected(sys_chunk(), mac=OTHER_MAC).code == Code.DEVICE_MISMATCH


def test_device_mac_argument_must_be_6_bytes():
    with pytest.raises(ValueError):
        wire.check_chunk(chunk(), device_mac=b"\1\2", server_utc_us=SERVER_UTC)


# --- 처음 실패한 단계가 이김 ----------------------------------------------------


@pytest.mark.parametrize(
    ("make", "code", "step"),
    [
        pytest.param(lambda: chunk(magic=0)[:51], Code.INCONSISTENT, 2, id="short+magic"),
        pytest.param(lambda: max_size()[:-4] + b"\0" + max_size()[-4:], Code.TOO_LARGE, 2, id="large+crc"),
        pytest.param(lambda: chunk(magic=0, version=9, header_len=1, crc=0), Code.MAGIC, 3, id="magic+version+hlen+crc"),
        pytest.param(lambda: chunk(version=9, header_len=1, crc=0), Code.VERSION, 4, id="version+hlen+crc"),
        pytest.param(lambda: chunk(header_len=1, crc=0, n_range=300), Code.HEADER_LEN, 5, id="hlen+crc+struct"),
        pytest.param(lambda: chunk(crc=0, n_range=300, reserved=1), Code.CRC, 6, id="crc+struct+field"),
        pytest.param(lambda: chunk(n_range=300, reserved=1), Code.INCONSISTENT, 7, id="struct+field"),
        pytest.param(lambda: chunk(blocks=[block(5, b"")], reserved=1), Code.VERSION, 7, id="kind+field"),
        pytest.param(
            lambda: chunk(blocks=[block(1, system_payload(head_mac=OTHER_MAC, slot_ms=0))]), Code.BAD_FIELD, 8,
            id="field+device",
        ),  # fmt: skip
    ],
)
def test_first_failing_step_wins(make, code, step):
    r = rejected(make())
    assert (r.code, r.check) == (code, step)


def test_field_order_within_step_8():
    r = rejected(chunk([range_frame(rx=2_000_000)], [imu_frame(link_rssi=1)], reserved=1))
    assert r.detail == "header.reserved"
    r = rejected(chunk([range_frame(rx=2_000_000)], [imu_frame(link_rssi=1)]))
    assert r.detail == "range[0].rx_offset_us"


# --- 검사 8: wire.md 검사 열의 규칙마다 하나 이상 --------------------------------

T2 = dict(tag_mask=1)  # tag_id = 2를 넣을 때 머리의 tag_mask는 bit0만


def _r(**kw):
    return chunk([range_frame(**kw)])


def _i(**kw):
    return chunk(None, [imu_frame(**kw)])


BAD_FIELD_CASES = [
    # 머리
    pytest.param(lambda: chunk(head_us_start=-1), "header.head_us_start", id="head_us_start<0"),
    pytest.param(lambda: chunk(utc_us_start=wire.UTC_MIN_US - 1), "header.utc_us_start", id="utc<2025"),
    pytest.param(lambda: chunk(utc_us_start=SERVER_UTC + DAY_US), "header.utc_us_start", id="utc>=server+1d"),
    pytest.param(lambda: chunk(utc_us_start=-5), "header.utc_us_start", id="utc-negative"),
    pytest.param(lambda: chunk(flags=0, utc_us_start=1), "header.utc_us_start", id="utc-without-bit0"),
    pytest.param(lambda: chunk(flags=0b101), "header.flags", id="flags-bit2"),
    pytest.param(lambda: chunk(flags=0x8001), "header.flags", id="flags-bit15"),
    pytest.param(lambda: chunk(tag_mask=0b101), "header.tag_mask", id="tag_mask-bit2"),
    pytest.param(lambda: chunk(tag_mask=0b11), "header.tag_mask", id="tag_mask-extra"),
    pytest.param(lambda: chunk(tag_mask=0), "header.tag_mask", id="tag_mask-missing"),
    pytest.param(
        lambda: chunk([range_frame(tag_id=0)], [imu_frame(tag_id=1, tag_mac=TAG1_MAC)], tag_mask=1),
        "header.tag_mask", id="tag_mask-imu-tag",
    ),  # fmt: skip
    pytest.param(lambda: chunk(reserved=1), "header.reserved", id="header-reserved"),
    pytest.param(
        lambda: chunk(blocks=[block(1, system_payload())], geometry_hash=zlib.crc32(b"default")),
        "header.geometry_hash", id="geometry_hash",
    ),  # fmt: skip
    # 거리 프레임
    pytest.param(lambda: _r(rx=2_000_000), "range[0].rx_offset_us", id="range-rx>=2s"),
    pytest.param(
        lambda: chunk([range_frame(rx=500), range_frame(rx=499)]), "range[1].rx_offset_us", id="range-rx-decreasing"
    ),
    pytest.param(
        lambda: chunk([range_frame(rx=500, tag_id=0), range_frame(rx=400, tag_id=1)]), "range[1].rx_offset_us",
        id="range-rx-decreasing-across-tags",
    ),  # fmt: skip
    pytest.param(lambda: _r(report=0x07, rssi=0x7, raw_cm=(1, 2, 3, 0)), "range[0].valid_mask", id="valid-not-reported"),
    pytest.param(lambda: _r(report=0x07, valid=0x7, raw_cm=(1, 2, 3, 0)), "range[0].rssi_mask", id="rssi-not-reported"),
    pytest.param(lambda: _r(report=0x4F), "range[0].report_flags", id="report_flags-bit6"),
    pytest.param(lambda: _r(report=0x8F), "range[0].report_flags", id="report_flags-bit7"),
    pytest.param(lambda: chunk([range_frame(tag_id=2)], **T2), "range[0].tag_id", id="range-tag_id2"),
    pytest.param(
        lambda: _r(report=0x0E, valid=0xE, rssi=0xE, raw_cm=(5, 1, 2, 3), rssi_dbm=(0, -1, -1, -1)),
        "range[0].raw_cm", id="raw_cm-unreported-nonzero",
    ),  # fmt: skip
    pytest.param(lambda: _r(raw_cm=(0, 1, 2, 3)), "range[0].raw_cm", id="raw_cm-valid-0"),
    pytest.param(lambda: _r(raw_cm=(1, 2, 3, 3001)), "range[0].raw_cm", id="raw_cm-valid-3001"),
    pytest.param(lambda: _r(raw_cm=(1, -5, 3, 4)), "range[0].raw_cm", id="raw_cm-valid-negative"),
    pytest.param(lambda: _r(rssi=0x7, rssi_dbm=(-1, -1, -1, -1)), "range[0].rssi_dbm", id="rssi-unmasked-nonzero"),
    pytest.param(lambda: _r(rssi_dbm=(0, -1, -1, -1)), "range[0].rssi_dbm", id="rssi-zero"),
    pytest.param(lambda: _r(rssi_dbm=(-1, 5, -1, -1)), "range[0].rssi_dbm", id="rssi-positive"),
    # IMU 프레임과 표본
    pytest.param(lambda: _i(rx=2_000_000), "imu[0].rx_offset_us", id="imu-rx>=2s"),
    pytest.param(
        lambda: chunk(None, [imu_frame(rx=900), imu_frame(rx=899)]), "imu[1].rx_offset_us", id="imu-rx-decreasing"
    ),
    pytest.param(lambda: _i(tag_mac=ZERO_MAC), "imu[0].tag_mac", id="tag_mac-zero"),
    pytest.param(lambda: _i(tag_mac=bytes.fromhex("01a1b2c3d4e5")), "imu[0].tag_mac", id="tag_mac-multicast"),
    pytest.param(lambda: _i(link_rssi=1), "imu[0].link_rssi", id="link_rssi>0"),
    pytest.param(lambda: _i(fs=0x14), "imu[0].fs", id="fs-accel4"),
    pytest.param(lambda: _i(fs=0x40), "imu[0].fs", id="fs-gyro4"),
    pytest.param(lambda: chunk(None, [imu_frame(tag_id=2)], **T2), "imu[0].tag_id", id="imu-tag_id2"),
    pytest.param(
        lambda: _i(samples=[sample()] * 3 + [sample(flags=0x08)]), "imu[0].samples[3].flags", id="sample-flags-bit3"
    ),
    pytest.param(lambda: _i(samples=[sample(flags=0x80)]), "imu[0].samples[0].flags", id="sample-flags-bit7"),
    # 블록 머리
    pytest.param(lambda: sys_chunk(at=2_000_000), "system.at_offset_us", id="system-at"),
    pytest.param(lambda: health_chunk(at=2_000_000), "health.at_offset_us", id="health-at"),
    pytest.param(lambda: survey_chunk(at=0xFFFFFFFF), "survey.at_offset_us", id="survey-at"),
    # system
    pytest.param(lambda: sys_chunk(tag_count=0), "system.tag_count", id="sys-tag_count0"),
    pytest.param(lambda: sys_chunk(tag_count=3), "system.tag_count", id="sys-tag_count3"),
    pytest.param(lambda: sys_chunk(slot_ms=0), "system.slot_ms", id="slot_ms0"),
    pytest.param(lambda: sys_chunk(slot_ms=101), "system.slot_ms", id="slot_ms101"),
    pytest.param(lambda: sys_chunk(position_dimensions=1), "system.position_dimensions", id="dims1"),
    pytest.param(lambda: sys_chunk(position_dimensions=4), "system.position_dimensions", id="dims4"),
    pytest.param(lambda: sys_chunk(flags=0x10), "system.flags", id="sys-flags-bit4"),
    pytest.param(lambda: sys_chunk(flags=0b1100), "system.flags", id="imu_transport3"),
    pytest.param(lambda: sys_chunk(geometry_source=3), "system.geometry_source", id="geometry_source3"),
    pytest.param(lambda: sys_chunk(fs=0x04), "system.fs", id="sys-fs-accel4"),
    pytest.param(lambda: sys_chunk(fs=0x50), "system.fs", id="sys-fs-gyro5"),
    pytest.param(lambda: sys_chunk(imu_acquisition_hz=0), "system.imu_acquisition_hz", id="acq0"),
    pytest.param(lambda: sys_chunk(imu_acquisition_hz=1001), "system.imu_acquisition_hz", id="acq1001"),
    pytest.param(lambda: sys_chunk(imu_transport_hz=1001), "system.imu_transport_hz", id="transport1001"),
    pytest.param(
        lambda: sys_chunk(set_members=(A1_MAC, A2_MAC, bytes.fromhex("0300000000aa"), TAG0_MAC, ZERO_MAC)),
        "system.set_members", id="set_members-multicast",
    ),  # fmt: skip
    pytest.param(lambda: sys_chunk(reserved=1), "system.reserved", id="sys-reserved"),
    pytest.param(
        lambda: sys_chunk(anchors=((0.0, 0.0, 0.0, 0.0), (100.5, 0.0, 0.0, 0.0), (0.0,) * 4, (0.0,) * 4)),
        "system.anchors[1].x", id="anchor-x>100",
    ),  # fmt: skip
    pytest.param(
        lambda: sys_chunk(anchors=((0.0, -101.0, 0.0, 0.0),) + ((0.0,) * 4,) * 3), "system.anchors[0].y",
        id="anchor-y<-100",
    ),  # fmt: skip
    pytest.param(
        lambda: sys_chunk(anchors=((0.0,) * 4,) * 3 + ((0.0, 0.0, NAN, 0.0),)), "system.anchors[3].z", id="anchor-z-nan"
    ),
    pytest.param(
        lambda: sys_chunk(anchors=((0.0, 0.0, 0.0, 5.5),) + ((0.0,) * 4,) * 3), "system.anchors[0].range_bias_m",
        id="bias>5",
    ),  # fmt: skip
    pytest.param(
        lambda: sys_chunk(anchors=((0.0,) * 4,) * 2 + ((0.0, 0.0, 0.0, -INF),) + ((0.0,) * 4,)),
        "system.anchors[2].range_bias_m", id="bias-inf",
    ),  # fmt: skip
    pytest.param(lambda: sys_chunk(geometry_id=b""), "system.geometry_id", id="geometry_id-empty"),
    pytest.param(lambda: sys_chunk(geometry_id=b"sv 1"), "system.geometry_id", id="geometry_id-space"),
    pytest.param(lambda: sys_chunk(geometry_id=b"sv\x7f1"), "system.geometry_id", id="geometry_id-0x7f"),
    pytest.param(lambda: sys_chunk(geometry_id=b"sv-\xc3\xa91"), "system.geometry_id", id="geometry_id-non-ascii"),
    pytest.param(lambda: sys_chunk(geometry_id=b"x" * 32), "system.geometry_id", id="geometry_id-32-chars"),
    pytest.param(lambda: sys_chunk(geometry_id=b"abc\0d"), "system.geometry_id", id="geometry_id-after-nul"),
    # health
    pytest.param(lambda: health_chunk(flags=0x100), "health.flags", id="health-flags-bit8"),
    pytest.param(lambda: health_chunk(tag_count=0), "health.tag_count", id="health-tag_count0"),
    pytest.param(lambda: health_chunk(tag_count=3), "health.tag_count", id="health-tag_count3"),
    pytest.param(lambda: health_chunk(buffer_used=831), "health.buffer_used", id="buffer_used>capacity"),
    pytest.param(lambda: health_chunk(buffer_used=0, buffer_capacity=0), "health.buffer_capacity", id="capacity0"),
    pytest.param(lambda: health_chunk(anchors=anchors_with(0, flags=0x27)), "health.anchors[0].flags", id="anchor-flags-bit5"),
    pytest.param(lambda: health_chunk(anchors=anchors_with(2, flags=0b10)), "health.anchors[2].flags", id="unseen-online"),
    pytest.param(lambda: health_chunk(anchors=anchors_with(2, state=1)), "health.anchors[2].state", id="unseen-state"),
    pytest.param(lambda: health_chunk(anchors=anchors_with(2, rssi=-3)), "health.anchors[2].rssi", id="unseen-rssi"),
    pytest.param(lambda: health_chunk(anchors=anchors_with(2, valid_mask=1)), "health.anchors[2].valid_mask", id="unseen-valid"),
    pytest.param(lambda: health_chunk(anchors=anchors_with(2, boot_id=5)), "health.anchors[2].boot_id", id="unseen-boot_id"),
    pytest.param(
        lambda: health_chunk(anchors=anchors_with(2, range_count=1)), "health.anchors[2].range_count", id="unseen-count"
    ),
    pytest.param(lambda: health_chunk(anchors=anchors_with(2, round_seq=1)), "health.anchors[2].round_seq", id="unseen-round"),
    pytest.param(lambda: health_chunk(anchors=anchors_with(2, age_ms=0)), "health.anchors[2].age_ms", id="unseen-age"),
    pytest.param(
        lambda: health_chunk(anchors=anchors_with(2, range_age_ms=0xFFFE)), "health.anchors[2].range_age_ms",
        id="unseen-range_age",
    ),  # fmt: skip
    pytest.param(lambda: health_chunk(anchors=anchors_with(1, state=6)), "health.anchors[1].state", id="anchor-state6"),
    pytest.param(lambda: health_chunk(anchors=anchors_with(0, rssi=1)), "health.anchors[0].rssi", id="anchor-rssi>0"),
    pytest.param(
        lambda: health_chunk(anchors=anchors_with(0, valid_mask=0x10)), "health.anchors[0].valid_mask",
        id="anchor-valid_mask-bit4",
    ),  # fmt: skip
    pytest.param(lambda: health_chunk(tags=tags_with(0, flags=0b111)), "health.tags[0].flags", id="tag-flags-bit2"),
    pytest.param(
        lambda: health_chunk(tags=tags_with(0, flags=0b10, link_rssi=-3, imu_age_ms=0xFFFF)), "health.tags[0].link_rssi",
        id="tag-unheard-rssi",
    ),  # fmt: skip
    pytest.param(
        lambda: health_chunk(tags=tags_with(0, flags=0b10, link_rssi=0, imu_age_ms=5)), "health.tags[0].imu_age_ms",
        id="tag-unheard-imu_age",
    ),  # fmt: skip
    pytest.param(
        lambda: health_chunk(tags=tags_with(1, range_count=5)), "health.tags[1].range_count", id="tag-norange-count"
    ),
    pytest.param(lambda: health_chunk(tags=tags_with(1, round_seq=5)), "health.tags[1].round_seq", id="tag-norange-round"),
    pytest.param(
        lambda: health_chunk(tags=tags_with(1, range_age_ms=7)), "health.tags[1].range_age_ms", id="tag-norange-age"
    ),
    pytest.param(
        lambda: health_chunk(tag_count=1, tags=tags_with(1, flags=0b10, range_count=1, round_seq=1, range_age_ms=1)),
        "health.tags[1].flags", id="tag_count1-T1-ranged",
    ),  # fmt: skip
    pytest.param(lambda: health_chunk(tags=tags_with(0, link_rssi=1)), "health.tags[0].link_rssi", id="tag-rssi>0"),
    pytest.param(lambda: health_chunk(tags=tags_with(0, reserved=1)), "health.tags[0].reserved", id="tag-reserved"),
    # survey
    pytest.param(lambda: survey_chunk(status=0), "survey.status", id="status0"),
    pytest.param(lambda: survey_chunk(status=3), "survey.status", id="status3"),
    pytest.param(lambda: survey_chunk(flags=0b1100101), "survey.flags", id="survey-flags-bit6"),
    pytest.param(lambda: survey_chunk(flags=0b100001), "survey.flags", id="solved-without-ok"),
    pytest.param(lambda: survey_chunk(heights_known=0x13), "survey.heights_known", id="heights_known-bit4"),
    pytest.param(lambda: survey_chunk(reserved=1), "survey.reserved", id="survey-reserved"),
    pytest.param(lambda: survey_chunk(message=b""), "survey.message", id="message-empty"),
    pytest.param(lambda: survey_chunk(message=b"Ok"), "survey.message", id="message-upper"),
    pytest.param(lambda: survey_chunk(message=b"too-far"), "survey.message", id="message-dash"),
    pytest.param(lambda: survey_chunk(message=b"a" * 24), "survey.message", id="message-24"),
    pytest.param(lambda: survey_chunk(message=b"ok\0x"), "survey.message", id="message-after-nul"),
    pytest.param(lambda: survey_chunk(solution_reason=b"bad reason"), "survey.solution_reason", id="reason-space"),
    pytest.param(lambda: survey_chunk(heights_m=(10.5, 0.0, 0.0, 0.0)), "survey.heights_m", id="height>10"),
    pytest.param(lambda: survey_chunk(heights_m=(-0.5, 0.0, 0.0, 0.0)), "survey.heights_m", id="height<0"),
    pytest.param(lambda: survey_chunk(heights_m=(NAN, 0.0, 0.0, 0.0)), "survey.heights_m", id="height-nan"),
    pytest.param(lambda: survey_chunk(heights_m=(1.5, 2.25, 1.0, 0.0)), "survey.heights_m", id="height-unknown-nonzero"),
    pytest.param(
        lambda: survey_chunk(measurements=meas_with(0, 1, (5, 0, 0))), "survey.measurements[0][1]", id="meas-n0-median"
    ),
    pytest.param(
        lambda: survey_chunk(measurements=meas_with(3, 0, (0, 0, 1))), "survey.measurements[3][0]", id="meas-n0-mad"
    ),
    pytest.param(
        lambda: survey_chunk(measurements=meas_with(2, 2, (0, 1, 0))), "survey.measurements[2][2]", id="meas-diagonal"
    ),
    pytest.param(
        lambda: survey_chunk(distances_m=((0.0, 100.5, 0.0, 0.0),) + ((0.0,) * 4,) * 3), "survey.distances_m",
        id="distance>100",
    ),  # fmt: skip
    pytest.param(
        lambda: survey_chunk(distances_m=((0.0,) * 4,) * 3 + ((0.0, 0.0, -1.0, 0.0),)), "survey.distances_m",
        id="distance<0",
    ),  # fmt: skip
    pytest.param(lambda: survey_chunk(max_asymmetry_m=-0.5), "survey.max_asymmetry_m", id="asym<0"),
    pytest.param(lambda: survey_chunk(max_asymmetry_m=INF), "survey.max_asymmetry_m", id="asym-inf"),
    pytest.param(lambda: survey_chunk(max_mad_m=-1.0), "survey.max_mad_m", id="mad<0"),
    pytest.param(lambda: survey_chunk(max_mad_m=NAN), "survey.max_mad_m", id="mad-nan"),
    pytest.param(
        lambda: survey_chunk(xyz_m=XYZ[:3] + ((0.0, 0.0, 100.5),)), "survey.xyz_m", id="xyz>100"
    ),
    pytest.param(lambda: survey_chunk(fit_rms_m=-0.25), "survey.fit_rms_m", id="fit_rms<0"),
    pytest.param(lambda: survey_chunk(flatness_m=-0.25), "survey.flatness_m", id="flatness<0"),
    pytest.param(lambda: survey_chunk(solved_height_m=NAN), "survey.solved_height_m", id="solved_height-nan"),
    pytest.param(lambda: survey_chunk(solved_height_m=-INF), "survey.solved_height_m", id="solved_height-inf"),
    pytest.param(lambda: survey_chunk(height_span_m=-0.25), "survey.height_span_m", id="span<0"),
]


@pytest.mark.parametrize(("make", "detail"), BAD_FIELD_CASES)
def test_bad_field_rules(make, detail):
    r = rejected(make())
    assert (r.code, r.check, r.detail) == (Code.BAD_FIELD, 8, detail)


GOOD_EDGE_CASES = [
    pytest.param(lambda: _r(rx=1_999_999), id="range-rx-1999999"),
    pytest.param(lambda: chunk([range_frame(rx=5), range_frame(rx=5)]), id="range-rx-equal"),
    pytest.param(lambda: chunk(utc_us_start=wire.UTC_MIN_US), id="utc=2025-01-01"),
    pytest.param(lambda: chunk(utc_us_start=SERVER_UTC + DAY_US - 1), id="utc=server+1d-1us"),
    pytest.param(lambda: chunk(flags=0, utc_us_start=0), id="utc-unknown"),
    pytest.param(lambda: chunk(flags=0b11, dropped_range=5, dropped_imu=2), id="overflow-flag"),
    pytest.param(lambda: _r(raw_cm=(1, 3000, 1, 3000)), id="raw_cm-bounds"),
    pytest.param(lambda: _r(valid=0b0110, raw_cm=(32767, 1, 2, -32767)), id="saturated-reported-not-valid"),
    pytest.param(lambda: _r(valid=0, rssi=0, report=0, raw_cm=(0, 0, 0, 0), rssi_dbm=(0, 0, 0, 0)), id="nothing-reported"),
    pytest.param(lambda: _i(link_rssi=0, fs=0x33), id="imu-rssi0-fs33"),
    pytest.param(lambda: _i(samples=[sample(flags=0b111)]), id="sample-flags-0-2"),
    pytest.param(lambda: sys_chunk(geometry_id=b"x" * 31), id="geometry_id-31"),
    pytest.param(lambda: sys_chunk(geometry_id=b"!~"), id="geometry_id-charset-edges"),
    pytest.param(lambda: sys_chunk(flags=0b1011, imu_transport_hz=0), id="imu_transport2-hz0"),
    pytest.param(lambda: sys_chunk(set_members=(ZERO_MAC,) * 5), id="set_members-empty"),
    pytest.param(
        lambda: sys_chunk(anchors=((100.0, -100.0, 100.0, 5.0),) + ((0.0, 0.0, 0.0, -5.0),) * 3), id="anchor-bounds"
    ),
    pytest.param(lambda: health_chunk(buffer_used=830), id="buffer-full"),
    pytest.param(lambda: health_chunk(tag_count=2, tags=(TAG_SEEN, TAG_SEEN)), id="two-tags-ranged"),
    pytest.param(lambda: health_chunk(tags=tags_with(1, flags=0b01, link_rssi=-60, imu_age_ms=3)), id="T1-heard-only"),
    pytest.param(lambda: survey_chunk(status=2, flags=0), id="survey-failed"),
    pytest.param(lambda: survey_chunk(message=b"a" * 23, solution_reason=b"too_few_samples_2"), id="message-23"),
    pytest.param(lambda: survey_chunk(heights_m=(10.0, 0.0, 0.0, 0.0)), id="height-10"),
    pytest.param(lambda: survey_chunk(solved_height_m=-3.5), id="solved_height-negative"),
]


@pytest.mark.parametrize("make", GOOD_EDGE_CASES)
def test_edges_that_pass(make):
    accepted(make())


# --- decode_chunk -------------------------------------------------------------


def test_decode_chunk_matches_check():
    for data in (chunk(), full_second(), max_size(), chunk(blocks=all_blocks())):
        assert wire.decode_chunk(data) == check(data)
        assert wire.decode_chunk(data, verify_crc=True) == check(data)


def test_decode_chunk_skips_field_checks_but_not_structure():
    c = wire.decode_chunk(chunk(reserved=7, crc=0))  # 필드와 CRC는 보지 않음
    assert c.header.reserved == 7
    with pytest.raises(ValueError, match="crc"):
        wire.decode_chunk(chunk(crc=0), verify_crc=True)
    for bad in (b"", chunk()[:51], chunk(magic=0), chunk(version=2), chunk(header_len=0), chunk(n_range=3)):
        with pytest.raises(ValueError):
            wire.decode_chunk(bad)
    with pytest.raises(ValueError, match="version"):
        wire.decode_chunk(chunk(blocks=[block(7, b"")]))


# --- 요청 프레이밍과 응답 -------------------------------------------------------


def test_split_and_join_round_trip():
    chunks = [chunk(chunk_seq=k) for k in range(3)] + [b""]
    body = wire.join_records(chunks)
    assert body[:4] == struct.pack("<I", len(chunks[0]))
    assert wire.split_request(body) == chunks
    assert check(wire.split_request(body)[3]).check == 2  # len 0은 프레이밍이 아니라 검사 2


def test_split_limits():
    one = chunk()
    assert len(wire.split_request(wire.join_records([one] * 16))) == 16
    with pytest.raises(wire.FramingError):
        wire.split_request(wire.join_records([one] * 17))
    assert len(wire.split_request(wire.join_records([one] * 40), max_chunks=None)) == 40


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"", id="empty"),
        pytest.param(struct.pack("<I", 100) + b"\0" * 99, id="len>remaining"),
        pytest.param(wire.join_records([b"abc"]) + b"\1\0", id="trailing-partial-len"),
        pytest.param(b"\0\0\0", id="short-len"),
        pytest.param(struct.pack("<I", 0xFFFFFFFF), id="huge-len"),
    ],
)
def test_split_framing_errors(body):
    with pytest.raises(wire.FramingError):
        wire.split_request(body)
    assert issubclass(wire.FramingError, ValueError)


def test_ack_round_trip():
    data = wire.encode_ack(1_793_000_000_123_456, [Code.STORED, Code.DUPLICATE, Code.CRC])
    assert data == bytes([0xC3, 1, 3, 0]) + struct.pack("<q", 1_793_000_000_123_456) + bytes([0, 1, 2])
    assert wire.decode_ack(data) == (1_793_000_000_123_456, [0, 1, 2])
    assert wire.decode_ack(wire.encode_ack(-1, [])) == (-1, [])
    assert len(wire.encode_ack(0, [8] * 16)) == 12 + 16


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"\xc3\x01\x00", id="short"),
        pytest.param(bytes([0xC4, 1, 0, 0]) + bytes(8), id="magic"),
        pytest.param(bytes([0xC3, 2, 0, 0]) + bytes(8), id="version"),
        pytest.param(bytes([0xC3, 1, 2, 0]) + bytes(8) + b"\0", id="count>codes"),
        pytest.param(bytes([0xC3, 1, 0, 0]) + bytes(8) + b"\0", id="codes>count"),
    ],
)
def test_ack_decode_errors(data):
    with pytest.raises(ValueError):
        wire.decode_ack(data)


def test_ack_encode_rejects_bad_codes():
    with pytest.raises(ValueError):
        wire.encode_ack(0, [256])


# --- 도우미 --------------------------------------------------------------------


def test_crc32_is_ieee():
    assert wire.crc32(b"123456789") == 0xCBF43926  # CRC-32/ISO-HDLC 확인값


def test_peek_key():
    assert wire.peek_key(b"\0" * 11) is None
    assert wire.peek_key(b"\xc1\x01\x30\x00" + struct.pack("<II", 5, 6)) == (5, 6)


def test_mac_text():
    assert wire.format_mac(DEVICE_MAC) == "e0:72:a1:ac:a8:58"
    for s in ("e0:72:a1:ac:a8:58", "E0-72-A1-AC-A8-58", "e072a1aca858"):
        assert wire.parse_mac(s) == DEVICE_MAC
    for s in ("e0:72:a1:ac:a8", "e0:72-a1:ac:a8:58", "zz:72:a1:ac:a8:58", "e0 72 a1 ac a8 58"):
        with pytest.raises(ValueError):
            wire.parse_mac(s)
    with pytest.raises(ValueError):
        wire.format_mac(b"\0" * 5)


def test_constants_match_wire_md():
    assert wire.BLOCK_LEN == {(1, 1): 152, (2, 1): 192, (3, 1): 304}
    assert wire.MIN_CHUNK_LEN == 52
    assert wire.MAX_CHUNK_LEN == 48 + 128 * 24 + 32 * (24 + 21 * 10) + (8 + 152) + (8 + 192) + (8 + 304) + 4
    assert wire.UTC_MIN_US == 1_735_689_600 * 1_000_000
    assert [c.value for c in Code] == list(range(10))
    assert wire.FRAMING_CODE == 255


# --- 어떤 바이트에도 예외를 내지 않음 --------------------------------------------


def test_fuzz_never_raises():
    rng = random.Random(20261029)
    seeds = [chunk(), full_second(), two_tag_second(), max_size(), chunk(blocks=all_blocks()), chunk([], [], all_blocks())]
    prefix = bytes([0xC1, 1, 48, 0])
    outcomes = set()
    for n in range(2500):
        roll = rng.random()
        if roll < 0.15:
            data = rng.randbytes(rng.randrange(0, 400))
        elif roll < 0.3:
            data = recrc(prefix + rng.randbytes(rng.randrange(48, 2000)))
        elif roll < 0.45:
            base = rng.choice(seeds)
            data = base[: rng.randrange(len(base) + 1)]
        elif roll < 0.6:
            b = bytearray(rng.choice(seeds))
            for off in (34, 36, 44):  # n_range, n_imu_frames, status_len
                if rng.random() < 0.5:
                    struct.pack_into("<H", b, off, rng.randrange(0, 1 << rng.choice((3, 6, 16))))
            data = recrc(b)
        else:
            b = bytearray(rng.choice(seeds))
            for _ in range(rng.randint(1, 6)):
                b[rng.randrange(4, len(b) - 4)] = rng.randrange(256)
            data = recrc(b) if rng.random() < 0.9 else bytes(b)
        r = check(data)
        assert isinstance(r, (wire.Chunk, wire.Rejection))
        if isinstance(r, wire.Rejection):
            assert not r.detail.startswith("internal"), (n, r)
            assert 2 <= r.check <= 9
            outcomes.add(r.code)
        else:
            outcomes.add(Code.STORED)
        try:
            wire.decode_chunk(data)
        except ValueError:
            pass
    # 무작위 변형이 깊은 단계까지 닿았는지
    assert {Code.INCONSISTENT, Code.BAD_FIELD, Code.CRC, Code.STORED} <= outcomes


def test_check_accepts_bytes_like():
    data = chunk()
    assert isinstance(check(bytearray(data)), wire.Chunk)
    assert isinstance(check(memoryview(data)), wire.Chunk)
    assert isinstance(check(bytearray(data)).data, bytes)


# --- 속도 (SPEC 9: 묶음 하나 요청 ≤ 2 ms) ----------------------------------------


def test_check_chunk_speed():
    data = full_second()
    assert len(data) == 4792
    for _ in range(30):
        accepted(data)
    n = 300
    t0 = time.perf_counter()
    for _ in range(n):
        check(data)
    avg = (time.perf_counter() - t0) / n
    assert avg < 1e-3, f"check_chunk on 4792 B took {avg * 1e6:.0f} µs on average"
