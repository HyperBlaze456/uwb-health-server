"""Self-tests of tools/fake_head.py, the independent wire v1 encoder (SPEC TL-1).

These do not use the server decoder. The checker below is a third, minimal reading of
uwb-firmware/docs/wire.md "서버의 검사" (steps 2-9), so the encoder is not tested against itself.
"""

import math
import struct
import zlib
from dataclasses import dataclass, field

import pytest

from tools import fake_head as fh

DAY_US = 86_400_000_000
UTC_FLOOR_US = 1_735_689_600_000_000            # 2025-01-01T00:00:00Z
MAC = fh.TEST_DEVICE_MAC
H = struct.Struct("<BBHIIqqIHHHHHHHH")
FIXED_LEN = {1: 152, 2: 192, 3: 304}
SURVEY_TEXT = set(b"abcdefghijklmnopqrstuvwxyz0123456789_")
GEOMETRY_TEXT = set(range(0x21, 0x7F))


# ---------------------------------------------------------------------------------------------------------
# independent checker
# ---------------------------------------------------------------------------------------------------------

class Reject(Exception):
    def __init__(self, code: int, step: int, detail: str):
        super().__init__(f"code {code} step {step}: {detail}")
        self.code, self.step, self.detail = code, step, detail


@dataclass
class Chunk:
    header: tuple
    ranges: list = field(default_factory=list)      # (rx, round_seq, slot, vr, report_flags, tag_id, raw4, rssi4)
    imus: list = field(default_factory=list)        # (rx, mac, boot, link_seq, tx_ms, rssi, fs, tag_id, samples)
    blocks: dict = field(default_factory=dict)      # kind -> (at_offset_us, payload)

    @property
    def flags(self) -> int:
        return self.header[8]


def _need(ok: bool, detail: str, code: int = 8, step: int = 8) -> None:
    if not ok:
        raise Reject(code, step, detail)


def _text_ok(raw: bytes, lo: int, hi: int, allowed: set) -> bool:
    s = raw.split(b"\0", 1)[0]
    return not raw[len(s):].strip(b"\0") and lo <= len(s) <= hi and all(c in allowed for c in s)


def _finite(values) -> bool:
    return all(math.isfinite(v) for v in values)


def parse(chunk: bytes) -> Chunk:
    """Steps 2-7 (length, magic, version, header_len, CRC, structure)."""
    n = len(chunk)
    if n < 52:
        raise Reject(7, 2, "len < 52")
    if n > 11_284:
        raise Reject(6, 2, "len > 11284")
    if chunk[0] != 0xC1:
        raise Reject(3, 3, "magic")
    if chunk[1] != 1:
        raise Reject(4, 4, "version")
    if struct.unpack_from("<H", chunk, 2)[0] != 48:
        raise Reject(5, 5, "header_len")
    if zlib.crc32(chunk[:-4]) != struct.unpack_from("<I", chunk, n - 4)[0]:
        raise Reject(2, 6, "crc")
    hd = H.unpack_from(chunk)
    n_range, n_imu, status_len = hd[9], hd[10], hd[14]
    _need(n_range <= 128 and n_imu <= 32, "frame counts", 7, 7)
    out = Chunk(hd)
    pos = 48
    _need(pos + 24 * n_range <= n - 4, "range frames past end", 7, 7)
    for _ in range(n_range):
        v = struct.unpack_from("<IIBBBB4h4b", chunk, pos)
        out.ranges.append((*v[:6], v[6:10], v[10:14]))
        pos += 24
    for _ in range(n_imu):
        _need(pos + 24 <= n - 4, "imu frame past end", 7, 7)
        v = struct.unpack_from("<I6sIHIbBBB", chunk, pos)
        count = v[8]
        _need(1 <= count <= 10, "imu count", 7, 7)
        _need(pos + 24 + 21 * count <= n - 4, "imu samples past end", 7, 7)
        samples = [struct.unpack_from("<HIB7h", chunk, pos + 24 + 21 * i) for i in range(count)]
        out.imus.append((*v[:8], samples))
        pos += 24 + 21 * count
    _need(pos + status_len + 4 == n, "total length", 7, 7)
    end = pos + status_len
    prev = 0
    while pos < end:
        _need(pos + 8 <= end, "block header past status_len", 7, 7)
        kind, kind_version, blen, at = struct.unpack_from("<BBHI", chunk, pos)
        if kind not in FIXED_LEN or kind_version != 1:
            raise Reject(4, 7, f"unknown block kind {kind} v{kind_version}")
        _need(kind > prev, "block order", 7, 7)
        _need(blen == FIXED_LEN[kind], "block len", 7, 7)
        _need(pos + 8 + blen <= end, "block past status_len", 7, 7)
        out.blocks[kind] = (at, chunk[pos + 8:pos + 8 + blen])
        prev = kind
        pos += 8 + blen
    return out


def check_fields(c: Chunk, now_us: int) -> None:
    """Step 8."""
    (_, _, _, _, _, head_us, utc_us, ghash, flags, _, _, _, _, tag_mask, _, reserved) = c.header
    _need(head_us >= 0, "head_us_start")
    if flags & 1:
        _need(UTC_FLOOR_US <= utc_us < now_us + DAY_US, "utc_us_start range")
    else:
        _need(utc_us == 0, "utc_us_start without bit0")
    _need(flags >> 2 == 0, "header flags bit2-15")
    _need(reserved == 0, "header reserved")
    _need(tag_mask >> 2 == 0, "tag_mask bit2-15")
    _need(bool(c.ranges or c.imus or c.blocks), "empty chunk")
    tags = set()
    prev = -1
    for rx, _seq, _slot, vr, rf, tag_id, raw, rssi in c.ranges:
        valid, rmask, reported = vr & 0xF, vr >> 4, rf & 0xF
        _need(rx < 2_000_000 and rx >= prev, "range rx_offset_us")
        prev = rx
        _need(valid & ~reported == 0 and rmask & ~reported == 0, "range masks")
        _need(rf >> 6 == 0, "report_flags bit6-7")
        _need(tag_id in (0, 1), "range tag_id")
        tags.add(tag_id)
        for i in range(4):
            if not reported >> i & 1:
                _need(raw[i] == 0, "raw_cm not reported")
            if valid >> i & 1:
                _need(1 <= raw[i] <= 3000, "raw_cm valid range")
            _need(rssi[i] < 0 if rmask >> i & 1 else rssi[i] == 0, "rssi_dbm")
    prev = -1
    for rx, mac, _boot, _lseq, _tx, rssi, fs, tag_id, samples in c.imus:
        _need(rx < 2_000_000 and rx >= prev, "imu rx_offset_us")
        prev = rx
        _need(mac != bytes(6) and mac[0] & 1 == 0, "tag_mac")
        _need(rssi <= 0, "link_rssi")
        _need(fs & 0xF <= 3 and fs >> 4 <= 3, "imu fs")
        _need(tag_id in (0, 1), "imu tag_id")
        tags.add(tag_id)
        _need(all(s[2] >> 3 == 0 for s in samples), "sample flags")
    _need(tag_mask == sum(1 << t for t in tags), "tag_mask")
    for kind, (at, payload) in c.blocks.items():
        _need(at < 2_000_000, "block at_offset_us")
        {1: _check_system, 2: _check_health, 3: _check_survey}[kind](payload, ghash)


def _check_system(p: bytes, ghash: int) -> None:
    v = struct.unpack("<6sBBBBBBHH30sH16f8s32s", p)
    _, tag_count, slot_ms, dims, flags, source, fs, acq_hz, tr_hz, members, reserved = v[:11]
    anchors, gid = v[11:27], v[28]
    _need(tag_count in (1, 2) and 1 <= slot_ms <= 100 and dims in (2, 3), "system counts")
    _need(flags >> 4 == 0 and (flags >> 2 & 3) <= 2 and source <= 2, "system flags/source")
    _need(fs & 0xF <= 3 and fs >> 4 <= 3, "system fs")
    _need(1 <= acq_hz <= 1000 and tr_hz <= 1000, "system imu hz")
    for i in range(5):
        m = members[6 * i:6 * i + 6]
        _need(m == bytes(6) or m[0] & 1 == 0, "set_members")
    _need(reserved == 0, "system reserved")
    _need(_finite(anchors), "anchors finite")
    for i in range(4):
        x, y, z, bias = anchors[4 * i:4 * i + 4]
        _need(max(abs(x), abs(y), abs(z)) <= 100 and abs(bias) <= 5, "anchor range")
    _need(_text_ok(gid, 1, 31, GEOMETRY_TEXT), "geometry_id text")
    _need(zlib.crc32(gid.split(b"\0", 1)[0]) == ghash, "geometry_hash")


def _check_health(p: bytes, _ghash: int) -> None:
    v = struct.unpack("<HBBHH22IHH60s32s", p)
    flags, _reason, tag_count, used, capacity = v[:5]
    anchors, tags = v[29], v[30]
    _need(flags >> 8 == 0 and tag_count in (1, 2), "health flags/tag_count")
    _need(capacity >= 1 and used <= capacity, "health buffer")
    for i in range(3):
        f, state, rssi, vmask, boot, rcount, rseq, age, rage = struct.unpack_from("<BBbBIIIHH", anchors, 20 * i)
        _need(f >> 5 == 0 and state <= 5 and rssi <= 0 and vmask >> 4 == 0, "anchor slot")
        if not f & 1:
            _need((state, rssi, vmask, boot, rcount, rseq) == (0,) * 6 and (age, rage) == (0xFFFF, 0xFFFF),
                  "unseen anchor slot")
    for i in range(2):
        f, rssi, imu_age, rcount, rseq, rage, reserved = struct.unpack_from("<BbHIIHH", tags, 16 * i)
        _need(f >> 2 == 0 and rssi <= 0 and reserved == 0, "tag slot")
        if not f & 1:
            _need(rssi == 0 and imu_age == 0xFFFF, "tag slot not heard")
        if not f & 2:
            _need(rcount == 0 and rseq == 0 and rage == 0xFFFF, "tag slot not ranged")
        if tag_count == 1 and i == 1:
            _need(not f & 2, "T1 ranged in 1-Tag mode")


def _check_survey(p: bytes, _ghash: int) -> None:
    v = struct.unpack("<BBBBHH24s24s4f" + "hHH" * 16 + "16f2f12f4f", p)
    status, flags, heights_known, reserved = v[:4]
    message, reason = v[6], v[7]
    heights, meas, dist = v[8:12], v[12:60], v[60:76]
    max_asym, max_mad, xyz, (fit, flat, solved_h, span) = v[76], v[77], v[78:90], v[90:94]
    _need(status in (1, 2) and flags >> 6 == 0 and heights_known >> 4 == 0 and reserved == 0, "survey head")
    _need(status != 1 or flags & 4, "solved without solution.ok")
    _need(_text_ok(message, 1, 23, SURVEY_TEXT) and _text_ok(reason, 1, 23, SURVEY_TEXT), "survey text")
    _need(_finite(v[8:12] + v[60:94]), "survey floats finite")
    for i in range(4):
        _need(0 <= heights[i] <= 10 and (heights_known >> i & 1 or heights[i] == 0), "heights_m")
    for k in range(16):
        med, n, mad = meas[3 * k:3 * k + 3]
        if n == 0 or k // 4 == k % 4:
            _need(med == 0 and mad == 0 and (n == 0 or k // 4 != k % 4), "measurements")
    _need(all(0 <= d <= 100 for d in dist), "distances_m")
    _need(max_asym >= 0 and max_mad >= 0 and fit >= 0 and flat >= 0 and span >= 0, "survey >= 0")
    _need(all(abs(x) <= 100 for x in xyz) and (flags & 4 or not any(xyz)), "xyz_m")
    _need(flags & 32 or solved_h == 0, "solved_height_m")


def verdict(chunk: bytes, device_mac: str = MAC, now_us: int = fh.DEFAULT_START_UTC_US + DAY_US
            ) -> tuple[int, int, str]:
    """(code, step, detail); (0, 0, '') when the chunk would be stored."""
    try:
        c = parse(chunk)
        check_fields(c, now_us)
        if 1 in c.blocks:
            _need(c.blocks[1][1][:6] == fh.mac_bytes(device_mac), "head_mac", 9, 9)
    except Reject as r:
        return r.code, r.step, r.detail
    return 0, 0, ""


def split(body: bytes) -> list[bytes]:
    out, pos = [], 0
    while pos < len(body):
        (n,) = struct.unpack_from("<I", body, pos)
        assert n <= len(body) - pos - 4, "framing"
        out.append(body[pos + 4:pos + 4 + n])
        pos += 4 + n
    return out


def chunks_of(requests) -> list[bytes]:
    return [c for r in requests for c in split(r.body)]


# ---------------------------------------------------------------------------------------------------------
# encoder layout
# ---------------------------------------------------------------------------------------------------------

def test_struct_sizes_match_wire_md():
    assert fh.HEADER.size == 48
    assert fh.RANGE.size == 24
    assert fh.IMU_FRAME.size == 24
    assert fh.SAMPLE.size == 21
    assert fh.BLOCK.size == 8
    assert fh.SYSTEM.size == 152
    assert fh.HEALTH.size == 192
    assert fh.ANCHOR_SLOT.size == 20
    assert fh.TAG_SLOT.size == 16
    assert fh.SURVEY.size == 304
    assert fh.ACK.size == 12


def test_crc_is_ieee_reflected_crc32():
    assert zlib.crc32(b"123456789") == 0xCBF43926       # standard check value of CRC-32/ISO-HDLC
    c = fh.FakeHead().next_chunk()
    assert struct.unpack("<I", c[-4:])[0] == zlib.crc32(c[:-4])


def test_sizes_from_wire_md_table():
    one = fh.FakeHead(range_hz=100, walk_pattern=None)
    c1 = [one.next_chunk() for _ in range(61)]
    assert len(c1[1]) == 4792                           # 100 range + 10 IMU frames x 10 samples
    assert (H.unpack_from(c1[1])[9], H.unpack_from(c1[1])[10]) == (100, 10)
    assert len(c1[60]) == 4792 + 200                    # + health block
    assert len(c1[0]) == 4792 + 200 + 160               # first chunk after boot: system + health
    two = fh.FakeHead(tag_count=2, range_hz=100, walk_pattern=None)
    c2 = [two.next_chunk() for _ in range(2)]
    assert len(c2[1]) == 7132
    assert H.unpack_from(c2[1])[13] == 0b11
    assert len(fh.max_size_chunk()) == 11_284
    assert verdict(fh.max_size_chunk())[0] == 0
    for c in c1 + c2:                                   # length formula of wire.md step 7
        p = parse(c)
        assert len(c) == 48 + 24 * len(p.ranges) + sum(24 + 21 * len(f[8]) for f in p.imus) + p.header[14] + 4


def test_header_fields():
    head = fh.FakeHead()
    c0, c1 = head.next_chunk(), head.next_chunk()
    h0, h1 = H.unpack_from(c0), H.unpack_from(c1)
    assert h1[:5] == (0xC1, 1, 48, head.boot_id, 1)
    assert h0[4] == 0 and head.seq == 2
    assert (h0[5], h1[5]) == (5_000_000, 6_000_000)
    assert (h0[6], h1[6]) == (fh.DEFAULT_START_UTC_US, fh.DEFAULT_START_UTC_US + 1_000_000)
    assert h1[7] == zlib.crc32(b"default")
    assert h1[8] == 1                                   # UTC estimate, no overflow
    assert h1[13] == 1 and h1[15] == 0
    assert h0[14] == 160 + 200 and h1[14] == 0
    blocks = parse(c0).blocks
    assert sorted(blocks) == [1, 2]
    assert blocks[1][1][:6] == fh.mac_bytes(MAC)
    assert fh.device_id(MAC) == "head-e072a1aca858"
    assert fh.parse_header(c1).chunk_seq == 1 and fh.chunk_key(c1) == (head.boot_id, 1)


def test_no_chunk_for_an_empty_second():
    head = fh.FakeHead()
    head.set_tag(0, on=False)
    assert head.next_chunk() is not None                # system + health
    assert head.next_chunk() is None                    # nothing happened
    assert head.seq == 1                                # seq not consumed
    assert head.t_s == 2


def test_frame_request_and_ack():
    head = fh.FakeHead()
    a, b = head.next_chunk(), head.next_chunk()
    body = fh.frame_request([a, b])
    assert struct.unpack_from("<I", body)[0] == len(a)
    assert split(body) == [a, b] == fh.unframe_request(body)
    ack = fh.encode_ack(1_790_000_000_000_000, [0, 1, 2])
    assert ack[:4] == bytes([0xC3, 1, 3, 0]) and len(ack) == 15
    assert fh.decode_ack(ack) == (1_790_000_000_000_000, [0, 1, 2])
    for broken in (ack[:-1], b"\xc4" + ack[1:], ack[:11]):
        with pytest.raises(ValueError):
            fh.decode_ack(broken)
    with pytest.raises(AssertionError):
        split(fh.bad_request_framing())
    with pytest.raises(ValueError):
        fh.unframe_request(fh.bad_request_framing())


def test_batches_respect_request_limits():
    big = fh.max_size_chunk()
    batch = fh._take_batch(fh.deque([big] * 20))
    assert len(batch) == 11                             # 12 x (4 + 11284) > 131072
    small = fh.FakeHead().next_chunk()
    assert len(fh._take_batch(fh.deque([small] * 40))) == 16


# ---------------------------------------------------------------------------------------------------------
# wrong chunks
# ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", fh.BAD_KINDS)
def test_bad_chunk_fails_at_its_step(kind):
    code, step, detail = verdict(fh.bad_chunk(kind))
    assert (code, step) == fh.BAD_EXPECT[kind], detail


def test_bad_chunks_use_the_given_head():
    head = fh.FakeHead("e0:72:a1:00:00:07", tag_count=2, seed=5)
    head.next_chunk()
    c = fh.bad_chunk("tag_mask_mismatch", head)
    assert fh.chunk_key(c) == (head.boot_id, head.seq)
    assert verdict(fh.bad_chunk("bad_crc", head), head.mac)[:2] == (2, 6)
    assert verdict(fh.bad_chunk("device_mismatch", head), head.mac)[:2] == (9, 9)
    assert verdict(fh.bad_chunk("geometry_hash_mismatch", head), head.mac)[:2] == (8, 8)


def test_wrong_device_is_caught_only_at_step_9():
    assert verdict(fh.FakeHead().next_chunk(), "e0:72:a1:00:00:01")[:2] == (9, 9)


# ---------------------------------------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------------------------------------

# Short versions keep this file fast; the defaults are exercised by the server tests.
SHORT = {
    "normal": {"duration_s": 130}, "two_tag": {"duration_s": 130}, "normal_2h": {"duration_s": 130},
    "outage": {"before_s": 20, "offline_s": 70, "after_s": 20},
    "outage_tag_off": {"before_s": 20, "offline_s": 150, "tag_off_at_s": 10, "tag_off_s": 125, "after_s": 20},
    "tag_off": {"before_s": 30, "off_s": 70, "after_s": 30},
    "frameless_only": {"duration_s": 200},
    "utc_late": {"offline_s": 40, "after_s": 30},
}


@pytest.mark.parametrize("name", fh.SCENARIOS)
def test_scenario_requests_are_well_formed(name):
    requests = fh.scenario(name, **SHORT.get(name, {}))
    assert requests
    prev_at = 0
    for r in requests:
        assert r.at_us >= prev_at
        prev_at = r.at_us
        parts = split(r.body)
        assert 1 <= len(parts) <= 16 and len(r.body) <= 131_072
        assert tuple(fh.chunk_key(c) for c in parts) == r.keys
        for c in parts:
            code, step, detail = verdict(c, MAC, r.at_us)
            assert code == 0, f"{name} {fh.chunk_key(c)}: step {step} {detail}"
            assert H.unpack_from(c)[6] < r.at_us or H.unpack_from(c)[6] == 0   # made before it is sent


def test_scenarios_are_deterministic():
    a = fh.scenario("tag_swap", seed=3, before_s=30, after_s=10)
    b = fh.scenario("tag_swap", seed=3, before_s=30, after_s=10)
    c = fh.scenario("tag_swap", seed=4, before_s=30, after_s=10)
    assert a == b
    assert [r.body for r in a] != [r.body for r in c]


def test_unknown_scenario_and_option():
    with pytest.raises(ValueError):
        fh.scenario("nope")
    with pytest.raises(TypeError):
        fh.scenario("normal", offline_s=3)


def test_normal_walk_pattern():
    chunks = chunks_of(fh.scenario("normal", duration_s=130))
    assert [fh.chunk_key(c)[1] for c in chunks] == list(range(130))
    with_imu = [H.unpack_from(c)[10] > 0 for c in chunks]
    assert all(with_imu[:40]) and not any(with_imu[40:120]) and all(with_imu[120:])
    assert all(H.unpack_from(c)[9] > 80 for c in chunks)            # ranging every second


def test_normal_2h_default_length():
    assert fh.scenario_span_s("normal_2h") == 7200


def test_outage_burst():
    requests = fh.scenario("outage", before_s=20, offline_s=70, after_s=20)
    gaps = [b.at_us - a.at_us for a, b in zip(requests, requests[1:])]
    assert max(gaps) >= 70_000_000
    assert max(r.n_chunks for r in requests) == 16
    seqs = [k[1] for r in requests for k in r.keys]
    assert seqs == list(range(110))


def test_overflow_drops_oldest_and_flags_next():
    up = fh.scenario_uploader("overflow")
    chunks = chunks_of(up.requests)
    seqs = [fh.chunk_key(c)[1] for c in chunks]
    assert sorted(set(range(180)) - set(seqs)) == list(range(60, 101))   # 41 lost in one gap
    flagged = {fh.chunk_key(c)[1] for c in chunks if H.unpack_from(c)[8] & 2}
    assert flagged == set(range(101, 122))
    assert all(H.unpack_from(c)[11] > 0 for c in chunks if fh.chunk_key(c)[1] in flagged)
    assert any(H.unpack_from(c)[12] > 0 for c in chunks)
    health = [parse(c).blocks[2][1] for c in chunks if 2 in parse(c).blocks]
    dropped = [struct.unpack_from("<22I", p, 8)[19] for p in health]
    assert dropped[-1] == 40                   # health at t = 120 is made before that second's drop


def test_resend_repeats_a_multi_chunk_request():
    requests = fh.scenario("resend")
    last = requests[-1]
    assert last.n_chunks > 1
    assert any(r.body == last.body for r in requests[:-1])
    assert len({k for r in requests[:-1] for k in r.keys}) == sum(r.n_chunks for r in requests[:-1])


def test_reboot_changes_boot_and_restarts_seq_and_clock():
    chunks = chunks_of(fh.scenario("reboot", before_s=30, after_s=30))
    boots = list(dict.fromkeys(fh.chunk_key(c)[0] for c in chunks))
    assert len(boots) == 2
    new = [c for c in chunks if fh.chunk_key(c)[0] == boots[1]]
    assert [fh.chunk_key(c)[1] for c in new] == list(range(30))
    assert H.unpack_from(new[0])[5] == 5_000_000
    assert H.unpack_from(new[0])[8] & 1 == 0                       # no UTC estimate before the first ack
    assert all(H.unpack_from(c)[8] & 1 for c in new[1:])
    assert H.unpack_from(new[1])[6] == fh.DEFAULT_START_UTC_US + 31_000_000   # true UTC continues


def test_late_old_boot_arrives_after_new_boot():
    requests = fh.scenario("late_old_boot")
    boots = list(dict.fromkeys(k[0] for r in requests for k in r.keys))
    old, new = boots
    first_new = next(i for i, r in enumerate(requests) if r.keys[0][0] == new)
    late = [i for i, r in enumerate(requests) if r.keys[0][0] == old and i > first_new]
    assert late
    old_seqs = [k[1] for r in requests for k in r.keys if k[0] == old]
    assert sorted(old_seqs) == list(range(140))


def test_tag_swap_changes_t0_mac_with_system_block():
    chunks = chunks_of(fh.scenario("tag_swap"))
    macs = [(fh.chunk_key(c)[1], f[1]) for c in chunks for f in parse(c).imus if f[7] == 0]
    distinct = list(dict.fromkeys(m for _, m in macs))
    assert len(distinct) == 2
    first_new = min(s for s, m in macs if m == distinct[1])
    assert first_new == 120
    swap = parse(chunks[120])
    assert 1 in swap.blocks
    assert swap.blocks[1][1][16 + 18:16 + 24] == distinct[1]       # set_members T0


def test_t1_appears():
    chunks = chunks_of(fh.scenario("t1_appears"))
    masks = [H.unpack_from(c)[13] for c in chunks]
    assert set(masks[:60]) == {1} and set(masks[60:]) == {3}
    t1_imu = [fh.chunk_key(c)[1] for c in chunks if any(f[7] == 1 for f in parse(c).imus)]
    assert t1_imu[0] == 120


def test_geometry_change():
    chunks = chunks_of(fh.scenario("geometry_change", before_s=30, after_s=10))
    hashes = [H.unpack_from(c)[7] for c in chunks]
    assert set(hashes[:30]) == {zlib.crc32(b"default")} and len(set(hashes[30:])) == 1 and hashes[30] != hashes[0]
    change = parse(chunks[30])
    assert sorted(change.blocks) == [1, 3]
    gid = change.blocks[1][1][120:152].rstrip(b"\0")
    assert gid.startswith(b"sv-") and zlib.crc32(gid) == hashes[30]
    assert change.blocks[1][1][9] & 1                               # coordinates_surveyed
    assert change.blocks[3][1][0] == 1                              # survey solved


def test_frameless_only():
    chunks = chunks_of(fh.scenario("frameless_only"))
    assert len(chunks) == 10
    assert all(H.unpack_from(c)[9] == H.unpack_from(c)[10] == 0 for c in chunks)
    assert sorted(parse(chunks[0]).blocks) == [1, 2]
    assert all(sorted(parse(c).blocks) == [2] for c in chunks[1:])


def test_tag_off_gives_frameless_chunks_then_a_new_tag_boot():
    chunks = chunks_of(fh.scenario("tag_off"))
    frames = [(fh.chunk_key(c)[1], H.unpack_from(c)[9] + H.unpack_from(c)[10]) for c in chunks]
    assert [s for s, n in frames if n == 0] == [120, 121]           # health at t = 120 and 180
    boots = list(dict.fromkeys(f[2] for c in chunks for f in parse(c).imus))
    assert len(boots) == 2                                          # power-on is a Tag boot


def test_outage_tag_off():
    chunks = chunks_of(fh.scenario("outage_tag_off"))
    frameless = [fh.chunk_key(c)[1] for c in chunks if H.unpack_from(c)[9] + H.unpack_from(c)[10] == 0]
    assert frameless == [180, 181]
    head_s = [H.unpack_from(c)[5] // 1_000_000 for c in chunks]
    assert head_s[182] - head_s[179] == 121                         # 120 s without frames (>= 60 s gap)


def test_utc_late():
    up = fh.scenario_uploader("utc_late")
    chunks = chunks_of(up.requests)
    bits = [H.unpack_from(c)[8] & 1 for c in chunks]
    first_known = bits.index(1)
    assert first_known > 100 and not any(bits[:first_known]) and all(bits[first_known:])
    for c in chunks[first_known:]:
        h = H.unpack_from(c)
        assert h[6] == fh.DEFAULT_START_UTC_US + h[5] - 5_000_000
    assert all(H.unpack_from(c)[6] == 0 for c in chunks[:first_known])


def test_multi_device():
    devices = fh.multi_device(3, duration_s=5)
    assert list(devices) == fh.device_macs(3) and fh.device_macs(3)[0] == MAC
    boots = set()
    for mac, requests in devices.items():
        first = split(requests[0].body)[0]
        assert parse(first).blocks[1][1][:6] == fh.mac_bytes(mac)
        assert all(verdict(c, mac)[0] == 0 for c in chunks_of(requests))
        boots.add(fh.chunk_key(first)[0])
    assert len(boots) == 3


def test_health_block_reflects_uploader():
    up = fh.scenario_uploader("outage", before_s=10, offline_s=70, after_s=5)
    chunks = chunks_of(up.requests)
    health = [struct.unpack_from("<HBBHH22I", parse(c).blocks[2][1]) for c in chunks if 2 in parse(c).blocks]
    flags, _, tag_count, used, capacity = health[1][:5]
    counters = health[1][5:]
    assert tag_count == 1 and capacity == 830 and used == 50       # t = 60: chunks 10-59 waiting
    assert flags & 0b11010011 == 0b11010011                        # configured, wifi, link, PSRAM, UTC
    assert counters[21] > 0                                        # upload_failures during the outage
    assert counters[0] > 0 and counters[8] > 0 and counters[9] == 10 * counters[8]
