"""TL-7 import_usb.py: USB 기록 → wire v1 묶음. 마지막 시험만 DB가 필요합니다.

- 실제 기록 조각(tests/data/usb_head_slice.jsonl, ../uwb-firmware/.build/recordings/wifi-save-head.jsonl의 37줄)을
  바꿔 서버 해독기로 풀고 원본 JSON과 필드마다 맞춰 봄
- imu_batch와 지금 모양의 system은 펌웨어 출력 코드(uwb_anchor/HeadMain.h)대로 손으로 쓴 줄로 같은 대조
  (실제 기록에 imu_batch 줄이 아직 없음)
- 창 규칙, 시각, 검사기 통과, 같은 입력 → 같은 바이트, CLI, 보내기
"""

import json
import subprocess
import sys
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools import fake_head
from tools import import_usb as iu
from uwbserver.pipeline import wire

ROOT = Path(__file__).resolve().parents[1]
SLICE = ROOT / "tests" / "data" / "usb_head_slice.jsonl"
MAC = fake_head.TEST_DEVICE_MAC
MAC_B = fake_head.mac_bytes(MAC)
UTC0 = iu.parse_utc("2026-10-20T10:24:00Z")
NOW = int(datetime(2026, 11, 10, tzinfo=UTC).timestamp()) * 1_000_000
US = 1_000_000


# --- 도우미 ---------------------------------------------------------------------------------------------------


def build(lines: list[str], utc_start: int = UTC0) -> tuple[list[iu.Built], iu.Stats]:
    scan = iu.scan_lines(lines, head_mac=MAC_B)
    offsets = iu.utc_offsets(scan, utc_start)
    stats = iu.Stats()
    return list(iu.convert(lines, head_mac=MAC_B, utc_offsets=offsets, stats=stats)), stats


def assert_all_pass(chunks: list[iu.Built]) -> list[wire.Chunk]:
    out = []
    for c in chunks:
        r = wire.check_chunk(c.data, device_mac=MAC_B, server_utc_us=NOW)
        assert isinstance(r, wire.Chunk), (c.boot_id, c.seq, r)
        out.append(r)
    return out


def line(obj: dict, pc: int | None = None) -> str:
    text = json.dumps(obj, separators=(",", ":"))
    return f"{text}\n" if pc is None else f"{pc}\t{text}\n"


def env(kind: str, seq: int, rx: int, boot: str = "0badf00d") -> dict:
    return {"schema": 1, "type": kind, "head_id": 0, "boot_id": boot, "packet_seq": seq, "head_rx_us": rx}


def range_obj(seq: int, rx: int, *, boot: str = "0badf00d", geometry: str = "default", tag_id: int = 0,
              round_seq: int = 7, cm=(212, 305, 377, 145), rssi=(-71.0, -80.0, None, -66.0),
              reported: int = 0xF, verified: bool = True) -> dict:  # fmt: skip
    """HeadMain.h emitRange와 같은 필드 순서."""
    valid = sum(1 << i for i in range(4) if reported >> i & 1 and cm[i] is not None and 0 < cm[i] <= 3000)
    rmask = sum(1 << i for i in range(4) if reported >> i & 1 and rssi[i] is not None)
    o = env("range", seq, rx, boot)
    o.update(
        configuration_verified=verified, tag_id=tag_id, round_seq=round_seq, geometry_id=geometry,
        timing={"basis": "module_round", "measurement_time_us": None, "inter_anchor_skew_us": None},
        slot_mask=reported, reported_mask=reported, valid_mask=valid, rssi_mask=rmask, explicit_anchor_ids=True,
        ranges=[
            {"anchor_id": i, "raw_cm": cm[i] if reported >> i & 1 else None, "valid": bool(valid >> i & 1),
             "corrected_m": cm[i] / 100 if valid >> i & 1 else None,
             "rssi_dbm": rssi[i] if rmask >> i & 1 else None,
             "weak_rssi": (rssi[i] < -85) if rmask >> i & 1 else None}
            for i in range(4)
        ],
        position={"valid": False, "dimensions": 3, "reason": "not_surveyed", "used_mask": 0, "xyz_m": None,
                  "residual_rms_m": None},
        imu_ref=None,
    )  # fmt: skip
    return o


def imu_obj(seq: int, rx: int, *, boot: str = "0badf00d", tag_id: int = 0, link_seq: int = 41,
            tag_mac: str = "e0:72:a1:00:00:10", tag_boot_id: str = "9a3c00f1", count: int = 10,
            accel: int | None = 2048, gyro: float | None = 32.8) -> dict:  # fmt: skip
    """HeadMain.h emitImuBatch와 같은 필드 순서."""
    o = env("imu_batch", seq, rx, boot)
    o.update(tag_id=tag_id, tag_mac=tag_mac, transport="espnow", tag_boot_id=tag_boot_id, link_seq=link_seq,
             link_rssi=-57, tx_tag_ms=123_456 + link_seq * 100)  # fmt: skip
    if accel is not None:
        o["accel_lsb_per_g"] = accel
    if gyro is not None:
        o["gyro_lsb_per_dps"] = gyro
    o["count"] = count
    o["samples"] = [
        {"sample_seq": (link_seq * 10 + k) & 0xFFFF, "tag_ms": 123_000 + link_seq * 100 + 10 * k, "flags": 5,
         "raw": [100 + k, -200 - k, 2048, -1876, 33 * k, -12, 7 - k]}
        for k in range(count)
    ]  # fmt: skip
    return o


ANCHORS = [(0.0, 0.0, 1.2, 0.0), (4.1, 0.0, 1.2, 0.05), (3.9, 3.2, 1.25, -0.1), (0.1, 3.1, 0.4, 0.0)]
MEMBERS = {"anchors": ["e0:72:a1:00:00:01", "e0:72:a1:00:00:02", None], "tags": ["e0:72:a1:00:00:10", None]}


def system_obj(seq: int, rx: int, *, boot: str = "0badf00d", geometry: str = "default", old: bool = False,
               members: dict | None = MEMBERS, device: str | None = None, anchors=ANCHORS) -> dict:  # fmt: skip
    """HeadMain.h emitSystem(지금 모양). old=True면 실제 기록의 예전 모양(device_id·set_members·tag_sides 없음)."""
    o = env("system", seq, rx, boot)
    if not old:
        o["device_id"] = device or "head-" + MAC_B.hex()
        o["set_members"] = members
    o.update(geometry_id=geometry, geometry_source="default" if geometry == "default" else "manual",
             coordinates_surveyed=geometry != "default", position_dimensions=3, slot_ms=10, tag_count=1)  # fmt: skip
    if not old:
        o["tag_sides"] = ["left"]
    o.update(
        module_filter=1, imu_acquisition_hz=100, imu_transport_hz=100, imu_transport="espnow", accel_lsb_per_g=2048,
        gyro_lsb_per_dps=32.8, clock_sync="unavailable_in_at_api",
        anchors=[{"id": i, "xyz_m": list(a[:3]), "range_bias_m": a[3]} for i, a in enumerate(anchors)],
    )  # fmt: skip
    return o


def decoded_ranges(chunks: list[wire.Chunk]) -> list[tuple[int, wire.RangeFrame]]:
    return [(c.header.head_us_start + r.rx_offset_us, r) for c in chunks for r in c.ranges]


def assert_range_matches(at: int, r: wire.RangeFrame, o: dict) -> None:
    assert at == o["head_rx_us"]
    assert r.round_seq == o["round_seq"]
    assert r.slot_mask == o["slot_mask"]
    assert r.valid_mask == o["valid_mask"]
    assert r.rssi_mask == o["rssi_mask"]
    assert r.reported_mask == o["reported_mask"]
    assert r.explicit_anchor_ids == o["explicit_anchor_ids"]
    assert r.configuration_verified == o["configuration_verified"]
    assert r.tag_id == o["tag_id"]
    assert list(r.raw_cm) == [0 if x["raw_cm"] is None else x["raw_cm"] for x in o["ranges"]]
    assert list(r.rssi_dbm) == [0 if x["rssi_dbm"] is None else round(x["rssi_dbm"]) for x in o["ranges"]]


def source_events(lines: list[str]) -> list[dict]:
    out = []
    for text in lines:
        body = text.rstrip("\n").split("\t", 1)[-1]
        if body.startswith("{"):
            o = json.loads(body)
            if o["type"] in iu.USED_TYPES:
                out.append(o)
    return out


# --- 1. 실제 기록 조각 -----------------------------------------------------------------------------------------


def test_real_slice_round_trip() -> None:
    lines = SLICE.read_text().splitlines(keepends=True)
    chunks, stats = build(lines)
    decoded = assert_all_pass(chunks)
    for c in chunks:  # 해독기로도 같은 구조(CRC 포함)
        assert wire.decode_chunk(c.data, verify_crc=True).header.chunk_seq == c.seq

    src = source_events(lines)
    ranges = [o for o in src if o["type"] == "range"]
    systems = [o for o in src if o["type"] == "system"]
    got = decoded_ranges(decoded)
    assert len(got) == len(ranges) == 34
    for (at, r), o in zip(got, ranges, strict=True):
        assert_range_matches(at, r, o)
    assert any(o["valid_mask"] != o["reported_mask"] for o in ranges)  # 거리 0, reported지만 valid 아님인 실제 줄

    # 실제 기록의 예전 모양 system(device_id·set_members 없음) → 블록 하나
    blocks = [(c, c.block(wire.KIND_SYSTEM)) for c in decoded if c.block(wire.KIND_SYSTEM)]
    assert len(blocks) == len(systems) == 1
    c, b = blocks[0]
    o = systems[0]
    assert c.header.head_us_start + b.at_offset_us == o["head_rx_us"]
    s = wire.decode_system(b.payload)
    assert s["head_mac"] == MAC
    assert (s["tag_count"], s["slot_ms"], s["position_dimensions"]) == (1, 10, 3)
    assert s["flags"] == 0 | (1 << 1) | (1 << 2)  # 실측 아님, module_filter 1, espnow
    assert s["geometry_source"] == 0 and s["geometry_id"] == o["geometry_id"] == "room-unmeasured-v1"
    assert s["fs"] == 3 | (2 << 4)  # 2048 LSB/g, 32.8 LSB/dps
    assert (s["imu_acquisition_hz"], s["imu_transport_hz"]) == (100, 100)
    assert s["set_members"] == ["00:00:00:00:00:00"] * 5
    assert s["firmware_id"] == "00" * 8
    for got_a, a in zip(s["anchors"], o["anchors"], strict=True):  # f32
        assert got_a == pytest.approx([*a["xyz_m"], a["range_bias_m"]], abs=1e-6)
    for ch in decoded:
        assert ch.header.geometry_hash == zlib.crc32(b"room-unmeasured-v1")

    # 건너뛴 줄과 경고
    assert stats.skipped == {"type:health": 1, "console_text": 1}
    assert (0xE593A112, 7594, 12962) in stats.seq_gaps
    assert stats.late == 0

    # 부팅 하나, seq는 0부터 빈칸 없이
    assert {c.boot_id for c in chunks} == {0xE593A112}
    assert [c.seq for c in chunks] == list(range(len(chunks)))
    assert all(c.n_range or c.n_imu or c.has_system for c in chunks)


def test_real_slice_time_follows_pc_clock() -> None:
    """첫 4줄은 레코더가 포트를 열 때 보드 버퍼에 남아 있던 줄(Head 시각으로 53초 전)이라, 첫 줄을 기준으로 삼지
    않고 (PC 시각 − head_rx_us)의 최솟값으로 Head 시계를 맞춤."""
    lines = SLICE.read_text().splitlines(keepends=True)
    chunks, _ = build(lines)
    pcs = [int(t.split("\t", 1)[0]) for t in lines]
    best = min(int(t.split("\t", 1)[0]) - json.loads(t.split("\t", 1)[1])["head_rx_us"]
               for t in lines if t.split("\t", 1)[1].startswith("{"))  # fmt: skip
    for c in chunks:
        assert c.utc_us_start == UTC0 + c.head_us_start + best - pcs[0]
    first = chunks[0]
    assert first.head_us_start == 98_466_700  # 파일의 첫 이벤트
    assert UTC0 - 54 * US < first.utc_us_start < UTC0 - 53 * US  # 기록 시작 약 53초 전에 생긴 이벤트
    # 다른 --utc-start면 utc만 그만큼 옮겨 감
    moved, _ = build(lines, UTC0 + 3 * US)
    assert [c.utc_us_start - 3 * US for c in moved] == [c.utc_us_start for c in chunks]


def test_deterministic_bytes() -> None:
    lines = SLICE.read_text().splitlines(keepends=True)
    a, _ = build(lines)
    b, _ = build(list(lines))
    assert [c.data for c in a] == [c.data for c in b]


# --- 2. imu_batch와 지금 모양의 system (손으로 쓴 줄) ------------------------------------------------------------


def test_imu_batch_and_current_system_round_trip() -> None:
    t = 5_000_000
    objs = [
        system_obj(1, t),
        range_obj(2, t + 10_000),
        imu_obj(3, t + 95_000, link_seq=41),
        imu_obj(4, t + 195_000, link_seq=42, tag_id=1, tag_mac="e0:72:a1:00:00:11", tag_boot_id="00000abc", count=3),
        imu_obj(5, t + 295_000, link_seq=43, accel=None, gyro=None),  # 스케일이 없는 줄 → 4096, 32.8
        imu_obj(6, t + 395_000, link_seq=44, accel=16384, gyro=131.0),
        range_obj(7, t + 400_000, rssi=(-150.0, -80.4, None, -66.6), cm=(40000, 0, 377, -5)),
    ]
    lines = [line(o) for o in objs]
    chunks, stats = build(lines)
    assert stats.skipped == {}
    decoded = assert_all_pass(chunks)
    assert len(decoded) == 1
    c = decoded[0]
    assert c.header.tag_mask == 0b11
    assert c.header.utc_us_start == UTC0 and c.header.head_us_start == t  # 맨 JSON: 첫 이벤트 = --utc-start

    imus = [o for o in objs if o["type"] == "imu_batch"]
    assert len(c.imu_frames) == len(imus)
    want_fs = [3 | 2 << 4, 3 | 2 << 4, 2 | 2 << 4, 0 | 0 << 4]
    for f, o, fs in zip(c.imu_frames, imus, want_fs, strict=True):
        assert c.header.head_us_start + f.rx_offset_us == o["head_rx_us"]
        assert wire.format_mac(f.tag_mac) == o["tag_mac"]
        assert f.tag_boot_id == int(o["tag_boot_id"], 16)
        assert (f.link_seq, f.tx_tag_ms, f.link_rssi, f.tag_id, f.count) == (
            o["link_seq"], o["tx_tag_ms"], o["link_rssi"], o["tag_id"], o["count"])  # fmt: skip
        assert f.fs == fs
        assert [(s.seq, s.tag_ms, s.flags, list(s.raw)) for s in f.samples] == [
            (s["sample_seq"], s["tag_ms"], s["flags"], s["raw"]) for s in o["samples"]]  # fmt: skip

    ranges = [o for o in objs if o["type"] == "range"]
    got = decoded_ranges(decoded)
    assert_range_matches(*got[0], ranges[0])
    r = got[1][1]
    assert list(r.raw_cm) == [32767, 0, 377, -5]  # 포화, reported지만 valid 아닌 칸은 그대로
    assert list(r.rssi_dbm) == [-128, -80, 0, -67]  # i8로 포화, 반올림

    b = c.block(wire.KIND_SYSTEM)
    assert b is not None and b.at_offset_us == 0
    s = wire.decode_system(b.payload)
    assert s["head_mac"] == MAC
    assert s["set_members"] == ["e0:72:a1:00:00:01", "e0:72:a1:00:00:02", "00:00:00:00:00:00",
                                "e0:72:a1:00:00:10", "00:00:00:00:00:00"]  # fmt: skip
    assert s["geometry_id"] == "default" and s["geometry_source"] == 0
    for got_a, want in zip(s["anchors"], ANCHORS, strict=True):
        assert got_a == pytest.approx(list(want), abs=1e-6)


def test_firmware_id_and_manual_geometry() -> None:
    o = system_obj(1, 1_000_000, geometry="manual-0badf00d-1")
    o["firmware_id"] = "3f9a1c0e" + "0" * 56
    chunks, _ = build([line(o), line(range_obj(2, 1_100_000, geometry="manual-0badf00d-1"))])
    s = wire.decode_system(assert_all_pass(chunks)[0].block(wire.KIND_SYSTEM).payload)
    assert s["firmware_id"] == "3f9a1c0e00000000"
    assert (s["geometry_source"], s["flags"] & 1) == (2, 1)
    o["firmware_id"] = "unversioned"
    chunks, _ = build([line(o)])
    assert wire.decode_system(assert_all_pass(chunks)[0].block(wire.KIND_SYSTEM).payload)["firmware_id"] == "00" * 8


def test_bad_lines_are_skipped_and_counted() -> None:
    good = range_obj(1, 1_000_000)
    bad_imu = imu_obj(2, 1_100_000, count=3)
    bad_imu["count"] = 4  # samples 수와 다름
    bad_scale = imu_obj(3, 1_200_000, accel=1000)
    broken = line(range_obj(4, 1_300_000)).rstrip("\n") + line(range_obj(5, 1_310_000))  # 두 줄이 붙음
    lines = [
        "# UWB OK\n",
        "*wm:6 networks found\n",
        "\n",
        line(good),
        line(bad_imu),
        line(bad_scale),
        broken,
        line(good),  # 같은 줄이 두 번
        line({"schema": 1, "type": "identity", "protocol": 1}),
        line(env("health", 6, 1_400_000)),
        line(env("survey", 7, 1_400_100)),
        line(env("imu", 8, 1_400_200)),
        line({**range_obj(9, 1_500_000), "schema": 2}),
        line({**range_obj(10, 1_500_000), "boot_id": "xyz"}),
        line(range_obj(11, 1_600_000)),
    ]
    chunks, stats = build(lines)
    assert stats.skipped == {
        "console_text": 2, "empty": 1, "bad_imu_batch": 2, "broken_json": 1, "repeated_packet_seq": 1,
        "type:identity": 1, "type:health": 1, "type:survey": 1, "type:imu": 1, "schema": 1, "bad_envelope": 1,
    }  # fmt: skip
    assert stats.events == {"range": 2}
    assert [c.n_range for c in chunks] == [2]
    assert_all_pass(chunks)


# --- 3. 창 규칙 -----------------------------------------------------------------------------------------------


def test_empty_seconds_make_no_chunk_and_keep_seq() -> None:
    """wire.md 다시 보내기 규칙 1: 프레임도 블록도 없는 초에는 묶음을 만들지 않고 seq도 쓰지 않음.
    (프레임도 블록도 없는 묶음은 check_chunk가 bad_field 'empty'로 거절함.)"""
    t = 10_000_000
    lines = [line(range_obj(1, t)), line(range_obj(2, t + 500_000)), line(range_obj(3, t + 5_200_000))]
    chunks, _ = build(lines)
    assert [(c.seq, c.head_us_start, c.n_range) for c in chunks] == [(0, t, 2), (1, t + 5 * US, 1)]
    assert_all_pass(chunks)
    empty = fake_head.encode_chunk(boot_id=1, seq=0, head_us_start=0, geometry_hash=0, utc_us_start=UTC0)
    assert wire.check_chunk(empty, device_mac=MAC_B, server_utc_us=NOW).detail == "empty"


def test_system_only_window_is_a_chunk() -> None:
    t = 10_000_000
    lines = [line(range_obj(1, t)), line(system_obj(2, t + 3_400_000)), line(range_obj(3, t + 6_000_000))]
    chunks, _ = build(lines)
    assert [(c.seq, c.n_range, c.has_system) for c in chunks] == [(0, 1, False), (1, 0, True), (2, 1, False)]
    assert assert_all_pass(chunks)[1].block(wire.KIND_SYSTEM).at_offset_us == 400_000


def test_second_boot_restarts_seq() -> None:
    a, b = "0000aaaa", "0000bbbb"
    pc0 = 900_000_000_000
    lines = [line(range_obj(1 + k, 2_000_000 + k * 300_000, boot=a), pc0 + 1_000 + k * 300_000) for k in range(8)]
    # 재부팅: Head 시계가 다시 작은 값에서 시작, PC 시계는 계속 감
    lines += [line(range_obj(1 + k, 1_500_000 + k * 300_000, boot=b), pc0 + 9 * US + k * 300_000) for k in range(5)]
    chunks, _ = build(lines)
    assert_all_pass(chunks)
    by = {}
    for c in chunks:
        by.setdefault(c.boot_id, []).append(c)
    assert [c.seq for c in by[0xAAAA]] == [0, 1, 2]
    assert [c.seq for c in by[0xBBBB]] == [0, 1]
    assert by[0xBBBB][0].head_us_start == 1_500_000
    # 두 부팅 모두 PC 시계로 시각을 맞춤: 첫 줄 = --utc-start
    assert by[0xAAAA][0].utc_us_start == UTC0
    assert by[0xBBBB][0].utc_us_start == UTC0 + 9 * US - 1_000


def test_two_boots_without_pc_time_are_refused() -> None:
    lines = [line(range_obj(1, 2_000_000, boot="0000aaaa")), line(range_obj(1, 1_000_000, boot="0000bbbb"))]
    with pytest.raises(iu.Refused):
        build(lines)


def test_geometry_change_closes_chunk_mid_window() -> None:
    t = 20_000_000
    lines = [
        line(range_obj(1, t)),
        line(range_obj(2, t + 100_000)),
        line(system_obj(3, t + 300_000, geometry="manual-x-1")),  # 새 배치를 알림 → 여기서 닫음
        line(range_obj(4, t + 310_000, geometry="manual-x-1")),
        line(range_obj(5, t + 1_350_000, geometry="manual-x-1")),  # 새 시작점 t + 0.3초에서 1초씩
    ]
    chunks, _ = build(lines)
    decoded = assert_all_pass(chunks)
    assert [(c.seq, c.head_us_start, c.n_range) for c in chunks] == [
        (0, t, 2), (1, t + 300_000, 1), (2, t + 1_300_000, 1)]  # fmt: skip
    assert decoded[0].header.geometry_hash == zlib.crc32(b"default")
    assert decoded[1].header.geometry_hash == decoded[2].header.geometry_hash == zlib.crc32(b"manual-x-1")
    assert decoded[1].block(wire.KIND_SYSTEM).at_offset_us == 0


def test_range_geometry_change_without_system() -> None:
    t = 20_000_000
    lines = [line(range_obj(1, t)), line(range_obj(2, t + 600_000, geometry="sv-0badf00d-1-0"))]
    chunks, _ = build(lines)
    assert [(c.head_us_start, c.n_range) for c in chunks] == [(t, 1), (t + 600_000, 1)]
    assert_all_pass(chunks)


def test_imu_before_any_geometry_uses_first_geometry() -> None:
    t = 1_000_000
    lines = [line(imu_obj(1, t)), line(imu_obj(2, t + 1_100_000, link_seq=42)),
             line(range_obj(3, t + 2_200_000, geometry="room-1"))]  # fmt: skip
    chunks, stats = build(lines)
    decoded = assert_all_pass(chunks)
    assert [c.seq for c in chunks] == [0, 1, 2]
    assert {c.header.geometry_hash for c in decoded} == {zlib.crc32(b"room-1")}
    assert stats.no_geometry == 0


def test_imu_only_boot_has_empty_geometry() -> None:
    chunks, stats = build([line(imu_obj(1, 1_000_000))])
    assert stats.no_geometry == 1
    assert assert_all_pass(chunks)[0].header.geometry_hash == zlib.crc32(b"")


def test_frames_over_limit_are_dropped_and_counted() -> None:
    t = 1_000_000
    lines = [line(range_obj(1 + k, t + k * 5_000)) for k in range(140)]
    lines += [line(imu_obj(200 + k, t + k * 20_000, link_seq=k, count=1)) for k in range(40)]
    chunks, _ = build(lines)
    (c,) = chunks
    assert (c.n_range, c.dropped_range, c.n_imu, c.dropped_imu) == (128, 12, 32, 8)
    (d,) = assert_all_pass(chunks)
    assert (d.header.dropped_range, d.header.dropped_imu_frames) == (12, 8)
    assert d.ranges[-1].rx_offset_us == 127 * 5_000  # 앞의 것을 둠


def test_packet_seq_jump_is_a_warning_only() -> None:
    t = 1_000_000
    lines = [line(range_obj(10, t)), line(range_obj(11, t + 10_000)), line(range_obj(500, t + 20_000))]
    chunks, stats = build(lines)
    assert stats.seq_gaps == [(0x0BADF00D, 11, 500)]
    assert chunks[0].n_range == 3


def test_out_of_order_head_rx_is_sorted_or_late() -> None:
    t = 1_000_000
    lines = [
        line(range_obj(1, t)),
        line(imu_obj(2, t + 900_000)),
        line(imu_obj(3, t + 850_000, link_seq=40)),  # 조금 앞선 도착 시각: 다시 세움
        line(range_obj(4, t + 4_000_000)),
        line(imu_obj(5, t + 100_000, link_seq=39)),  # 2초 넘게 늦게 나타남: 건너뜀
        line(range_obj(6, t + 4_100_000)),
    ]
    chunks, stats = build(lines)
    decoded = assert_all_pass(chunks)
    assert [f.link_seq for f in decoded[0].imu_frames] == [40, 41]
    assert stats.late == 1


def test_system_block_rules() -> None:
    """부팅 안 첫 system, 내용이 바뀐 system, 10분이 지난 system만. 한 창에 둘이면 나중 것."""
    t = 1_000_000
    s1 = system_obj(0, 0)
    s2 = system_obj(0, 0, members={**MEMBERS, "tags": ["e0:72:a1:00:00:10", "e0:72:a1:00:00:11"]})
    events = [
        (s1, t), (s1, t + 10 * US), (s1, t + 20 * US),  # 처음 것만
        (s2, t + 30 * US),  # 세트 구성이 바뀜
        (s2, t + 40 * US),
        (s2, t + 630 * US),  # 마지막으로 넣은 지 10분
        (s1, t + 700 * US), (s2, t + 700 * US + 500_000),  # 한 창에 둘 → 나중 것(s2)만
    ]
    lines = [line({**o, "packet_seq": k, "head_rx_us": rx}) for k, (o, rx) in enumerate(events)]
    chunks, stats = build(lines)
    decoded = assert_all_pass(chunks)
    got = [(c.header.head_us_start + c.block(1).at_offset_us, wire.decode_system(c.block(1).payload)["set_members"][4])
           for c in decoded if c.block(1)]  # fmt: skip
    t1 = "e0:72:a1:00:00:11"
    zero = "00:00:00:00:00:00"
    assert got == [(t, zero), (t + 30 * US, t1), (t + 630 * US, t1), (t + 700 * US + 500_000, t1)]
    assert stats.system_events == 8 and stats.system_blocks == 4


def test_other_device_is_refused() -> None:
    lines = [line(system_obj(1, 1_000_000, device="head-e072a1000099"))]
    with pytest.raises(iu.Refused):
        iu.import_lines(lambda: lines, head_mac=MAC_B, utc_start_us=UTC0, server_utc_us=NOW, sender=None)


# --- 4. 시각과 인자 -------------------------------------------------------------------------------------------


def test_parse_utc() -> None:
    assert iu.parse_utc("2026-10-20T10:24:00Z") == UTC0
    assert iu.parse_utc("2026-10-20T19:24:00+09:00") == UTC0
    assert iu.parse_utc("2026-10-20T10:24:00.250Z") == UTC0 + 250_000
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        iu.parse_utc("2026-10-20T10:24:00")  # 시간대 없음
    with pytest.raises(argparse.ArgumentTypeError):
        iu.parse_utc("yesterday")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "tools/import_usb.py", *args], cwd=ROOT, capture_output=True, text=True,
                          timeout=60)  # fmt: skip


def test_cli_dry_run_on_real_slice() -> None:
    p = run_cli(str(SLICE), "--device", f"{MAC}=x", "--utc-start", "2026-10-20T10:24:00Z", "--dry-run")
    assert p.returncode == 0, p.stderr
    assert "읽은 줄 37" in p.stdout
    assert "range 34, system 1" in p.stdout
    assert "검사(check_chunk): ok " in p.stdout
    assert "--dry-run: 보내지 않음" in p.stdout
    assert "7594 → 12962" in p.stderr


@pytest.mark.parametrize(
    "args",
    [
        [str(SLICE), "--device", f"{MAC}=x"],  # --utc-start 없음
        [str(SLICE), "--device", f"{MAC}=x", "--utc-start", "2026-10-20T10:24:00", "--dry-run"],  # 시간대 없음
        [str(SLICE), "--device", f"{MAC}=x", "--utc-start", "2024-12-31T23:59:59Z", "--dry-run"],  # 2025 이전
        [str(SLICE), "--device", f"{MAC}=x", "--utc-start", "2099-01-01T00:00:00Z", "--dry-run"],  # 지금 + 1일 뒤
        [str(SLICE), "--device", "zz:72:a1:ac:a8:58=x", "--utc-start", "2026-10-20T10:24:00Z", "--dry-run"],
        [str(SLICE), "--device", "e1:72:a1:ac:a8:58=x", "--utc-start", "2026-10-20T10:24:00Z", "--dry-run"],
        [str(SLICE), "--device", MAC, "--utc-start", "2026-10-20T10:24:00Z"],  # 보내는데 토큰 없음
        ["no-such.jsonl", "--device", f"{MAC}=x", "--utc-start", "2026-10-20T10:24:00Z", "--dry-run"],
    ],
)
def test_cli_argument_errors(args: list[str]) -> None:
    assert run_cli(*args).returncode == 2


def test_cli_other_device_exit_2(tmp_path: Path) -> None:
    f = tmp_path / "x.jsonl"
    f.write_text(line(system_obj(1, 1_000_000, device="head-e072a1000099")))
    p = run_cli(str(f), "--device", f"{MAC}=x", "--utc-start", "2026-10-20T10:24:00Z", "--dry-run")
    assert p.returncode == 2 and "device_id" in p.stderr


# --- 5. 요청 나누기와 보내기 ----------------------------------------------------------------------------------


def test_batches_respect_count_and_size() -> None:
    small = [b"x" * 100] * 40
    assert [len(b) for b in iu.batches(small)] == [16, 16, 8]
    big = [b"y" * 11_284] * 20  # 11 × 11,288 = 124,168 ≤ 131,072 < 12 × 11,288
    sizes = [len(b) for b in iu.batches(big)]
    assert sizes == [11, 9]
    for b in iu.batches(big):
        assert len(fake_head.frame_request(b)) <= fake_head.MAX_REQUEST_BYTES


class _Resp:
    def __init__(self, status: int, content: bytes = b"") -> None:
        self.status_code = status
        self.content = content


class _Client:
    def __init__(self, replies: list) -> None:
        self.replies = replies
        self.bodies: list[bytes] = []

    def post(self, url, *, content, headers, timeout):
        assert url.endswith("/d/v1/chunks") and headers["Authorization"] == "Bearer tok"
        self.bodies.append(content)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_sender_retries_then_stops() -> None:
    ack = fake_head.encode_ack(NOW, [0, 1])
    slept: list[float] = []
    client = _Client([_Resp(503), TimeoutError("t"), _Resp(200, ack)])
    s = iu.Sender(client, "http://x", "tok", sleep=slept.append)
    assert s.send([b"a", b"b"]) == [0, 1]
    assert slept == [1, 2] and s.requests == 3
    assert fake_head.unframe_request(client.bodies[0]) == [b"a", b"b"]

    with pytest.raises(iu.SendError, match="401"):
        iu.Sender(_Client([_Resp(401)]), "http://x", "tok", sleep=slept.append).send([b"a"])
    slept.clear()
    with pytest.raises(iu.SendError):
        iu.Sender(_Client([_Resp(500)] * 7), "http://x", "tok", sleep=slept.append).send([b"a"])
    assert slept == [1, 2, 4, 8, 16, 30]


# --- 6. intake에 보내기 (DB) ----------------------------------------------------------------------------------


@pytest.mark.db
def test_send_twice_stored_then_duplicate(env, device, intake_client, capsys) -> None:
    args = [str(SLICE), "--device", f"{MAC}={device.token}", "--utc-start", "2026-10-20T10:24:00Z",
            "--url", "http://testserver"]  # fmt: skip
    n = len(build(SLICE.read_text().splitlines(keepends=True))[0])
    assert iu.main(args, client=intake_client) == 0
    out = capsys.readouterr().out
    assert f"응답 코드 0 stored {n}" in out
    assert env.scalar("SELECT count(*) FROM intake.chunk") == n
    assert env.scalar("SELECT count(*) FROM intake.system") == 1
    assert iu.main(args, client=intake_client) == 0
    assert f"응답 코드 1 duplicate {n}" in capsys.readouterr().out
    assert env.scalar("SELECT count(*) FROM intake.chunk") == n
