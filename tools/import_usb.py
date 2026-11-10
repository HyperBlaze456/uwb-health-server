"""USB 기록 넣기 (SPEC TL-7).

펌웨어의 Head USB 기록(../uwb-firmware/docs/protocol.md)을 wire v1 1초 묶음으로 바꿔 intake에 보냅니다.
Head 전송부가 생기기 전에 실제 기록을 서버에 넣는 길입니다. DB에는 접속하지 않고 HTTP만 씁니다(TL 끝 문단).

    uv run python tools/import_usb.py <기록.jsonl> --device MAC=TOKEN --utc-start 2026-10-20T10:24:00Z
        [--url http://localhost:8001] [--dry-run]

--utc-start는 기록 파일 첫 줄의 UTC(레코더를 시작한 시각)이고 시간대(Z, +09:00)를 꼭 붙입니다.
--dry-run은 묶음을 만들어 서버의 검사기로 검사만 하고 보내지 않습니다(토큰은 아무 값이나 됨).

변환 (protocol.md → wire.md). 파일을 두 번 차례로 읽고 전체를 메모리에 두지 않습니다.

1. 읽기. 줄마다 앞에 레코더(../uwb-firmware/scripts/record_usb.py)가 붙인 PC 단조 시각 µs와 TAB이 있습니다
   (`687740789780<TAB>{"schema":1,...}`). 이것이 없는 맨 JSON 줄도 받습니다. 공통 머리 schema(1), type, boot_id(8자리
   16진), packet_seq, head_rx_us를 봅니다. type이 range, imu_batch, system인 줄만 씁니다. health, imu(UWB DATA 경로),
   survey, identity와 모르는 type, '#'·'*wm:' 같은 콘솔 글, 두 줄이 붙어 깨진 줄, 필드가 틀린 줄은 건너뛰고 까닭별로
   셉니다. health 블록은 만들지 않습니다(TL-7). survey 블록도 TL-7 범위 밖입니다.
   같은 부팅에서 packet_seq가 1보다 크게 뛰면 USB 출력에서 빠진 이벤트(health.usb_dropped_packets)이므로 경고만
   남깁니다. 최근 4096줄 안에 이미 나온 packet_seq는 같은 줄이 두 번 기록된 것으로 보고 건너뜁니다. packet_seq는
   모든 type에 걸쳐 하나로 세므로 건너뛰는 health 줄도 빈자리 계산에 넣습니다.

2. 시각. USB 기록에는 벽시계가 없습니다. 첫 번째 읽기에서 부팅마다 (PC 시각 − head_rx_us)의 최솟값을 구해 Head
   시계를 PC 시계에 맞추고, 파일 첫 줄의 PC 시각을 --utc-start에 맞춥니다:
   utc_us_start = head_us_start + 그 최솟값 − 첫 줄 PC 시각 + utc_start. USB 지연은 늘 0 이상이라 최솟값이 가장
   덜 늦은 줄입니다. 그래서 레코더가 포트를 열 때 보드 버퍼에 남아 있던 옛 줄(실제 기록의 첫 4줄은 Head 시각으로
   53초 전 것)이나 다른 부팅이 있어도 시각이 맞습니다. PC 시각이 없는 맨 JSON 파일은 첫 이벤트의 head_rx_us를
   utc_start로 두며, 부팅이 둘 이상이면 거부합니다(두 번째 부팅의 시각을 알 길이 없음). 시각이 없으면 서버가
   수신 시각 − 1초를 묶음 시각으로 쓰므로(SG-7) --utc-start가 없거나 2025-01-01 이전이거나 지금 + 1일 뒤이면
   실행을 거부합니다. flags bit0 = 1입니다. 최솟값 하나로 맞추므로, 두 시계의 빠르기 차이(수십 ppm, 1시간에
   0.2초 안팎)만큼 그 줄에서 멀어질수록 시각이 어긋날 수 있습니다.

3. 부팅과 묶음 나누기. boot_id마다 따로 다룹니다: head_boot_id = int(boot_id, 16). Head가 다른 작업에서 찍은
   head_rx_us가 출력 순서와 조금 어긋날 수 있어, 부팅마다 2초 동안 모아 head_rx_us 순으로 다시 세웁니다. 그보다
   늦게 나타난 이벤트는 건너뛰고 셉니다(late). 그 부팅의 첫 이벤트 head_rx_us를 t0로 두고 1초 창
   [t0 + k초, t0 + k초 + 1초)로 나눕니다. head_us_start = 창의 시작입니다. 프레임도 system도 없는 창은 묶음을 만들지
   않고 chunk_seq도 쓰지 않습니다(wire.md 다시 보내기 규칙 1, 실제 Head와 같음). chunk_seq는 부팅마다 0부터 만든
   묶음마다 1씩 늘어납니다. range나 system의 geometry_id가 창 중간에 바뀌면 그 이벤트에서 묶음을 닫고 그 시각에서
   새 창을 시작합니다(wire.md: 한 묶음의 프레임은 모두 같은 배치). 머리 geometry_hash = 그 묶음 geometry_id ASCII의
   CRC-32입니다. 부팅 처음에 IMU만 있는 창은 그 부팅에서 처음 나오는 geometry_id를 씁니다.
   같은 파일과 같은 --utc-start는 늘 같은 바이트를 만듭니다. 그래서 다시 보내면 서버가 duplicate(1)로 걸러 냅니다.

4. range → 거리 프레임 24 B. rx_offset_us = head_rx_us − head_us_start. round_seq, slot_mask, tag_id 그대로.
   byte 9 = valid_mask | rssi_mask << 4. report_flags = reported_mask | explicit_anchor_ids << 4 |
   configuration_verified << 5(없으면 0). raw_cm[i] = ranges[i].raw_cm(null이면 0, ±32767로 포화).
   rssi_dbm[i] = ranges[i].rssi_dbm을 반올림한 정수(null이면 0, i8 범위 −128–127로 포화. 펌웨어는 −150까지 받음).
   position, imu_ref, corrected_m, weak_rssi, timing은 보내지 않습니다(서버·gaitfusion이 다시 계산).

5. imu_batch → IMU 프레임 24 + 21 × count B. rx_offset_us, tag_mac, tag_boot_id(8자리 16진 문자열 또는 정수),
   link_seq, tx_tag_ms, link_rssi, tag_id, 표본마다 (sample_seq, tag_ms, flags, raw[7]) 그대로. count는 samples 수와
   같아야 하고 1–10입니다. fs = AFS_SEL | FS_SEL << 4. accel_lsb_per_g 16384/8192/4096/2048 → 0/1/2/3,
   gyro_lsb_per_dps 131/65.5/32.8/16.4 → 0/1/2/3. 스케일 필드가 없으면 4096 LSB/g(protocol.md의 예전 기록),
   32.8 LSB/(°/s)(ESP-NOW 경로가 생긴 뒤 바뀐 적 없음)로 둡니다.
   주의: 이 경로는 아직 실제 기록으로 확인하지 못했습니다. 지금 있는 기록에는 imu_batch 줄이 없어, Head 펌웨어의
   출력 코드(uwb_anchor/HeadMain.h emitImuBatch)와 protocol.md로 만든 줄로만 시험했습니다.

6. system → system 블록 152 B(kind 1). at_offset_us = head_rx_us − head_us_start. head_mac은 --device의 MAC입니다.
   기록의 system에 device_id가 있으면 그것이 --device와 같아야 하고, 다르면 실행을 거부합니다. 예전 펌웨어의
   system에는 device_id, set_members, tag_sides가 없습니다. 이때 set_members는 모두 0입니다. firmware_id는 지금
   펌웨어의 system JSON에도 없어 보통 0입니다(있으면 16진 문자열 앞 8 바이트, unversioned → 0).
   flags = coordinates_surveyed | module_filter << 1 | imu_transport << 2(none 0 / espnow 1 / uwb_data 2).
   geometry_source는 default 0 / survey 1 / manual 2. anchors는 id 0–3의 xyz_m, range_bias_m.
   USB는 system을 10초마다 내지만 Head 규칙(wire.md "상태 블록을 넣는 때")대로 부팅 안의 첫 system, 내용(좌표나
   세트 구성 등)이 바뀐 system, 그리고 마지막으로 넣은 지 10분이 지난 system만 넣습니다. 한 창에 둘이면 나중 것만
   넣습니다. system만 있는 창도 묶음이 됩니다(서버는 프레임 없는 묶음을 구간에 넣지 않음, SG-2).

7. 묶음 머리. magic 0xC1, version 1, header_len 48, flags bit0 = 1, bit1 = 0, tag_mask = 프레임들의 tag_id 집합,
   status_len = 블록 길이 합, reserved 0, 끝에 CRC-32. 한 묶음의 거리 프레임이 128개, IMU 프레임이 32개를 넘으면
   앞의 것만 두고 나머지를 버려 dropped_range·dropped_imu_frames에 셉니다(Head와 같음).
   인코더는 tools/fake_head.py의 것(서버 해독기와 따로 쓴 구현)을 씁니다.

8. 보내기 전 검사. 묶음마다 서버의 검사기(uwbserver.pipeline.wire.check_chunk, 서버 시각 = 지금)를 돌려 거절될
   묶음을 코드와 필드별로 셉니다. 거절될 묶음도 보냅니다(서버가 30일 보관하고 고친 뒤 다시 넣을 수 있음, IN-10).

9. 보내기. POST /d/v1/chunks, Bearer = --device의 토큰. 요청 하나에 묶음 16개까지, 본문 131,072 B까지, 부팅별
   seq 순서로. 5xx·시간 초과·연결 실패는 1, 2, 4, 8, 16, 30초 뒤 다시 보내고(wire.md 다시 보내기 규칙), 그래도
   안 되면 멈춥니다. 401·400·413이면 멈춥니다. 멈춰도 다시 실행하면 이미 들어간 묶음은 duplicate가 됩니다.

출력: 표준 출력에 읽은 줄 수, 건너뛴 줄의 까닭별 수, 부팅별 묶음 수·seq·첫·끝 UTC, 프레임 수, 검사 결과,
응답 코드별 수. packet_seq 빈자리 경고는 표준 오류에.

주의: 같은 부팅을 실제 Head가 직접 올린 적이 있으면 묶음 경계가 달라도 키(부팅, seq)가 겹쳐 duplicate로
버려집니다. 지금은 Head 전송부가 없어 생기지 않지만, 그 뒤에는 둘 중 한 길만 씁니다.

종료 코드: 0 = 모든 묶음이 0·1(--dry-run은 검사에서 거절될 묶음이 없음), 1 = 거절된(될) 묶음이 있음,
2 = 인자 오류, 파일을 못 읽음, 기록이 --device와 다름, 보내기 실패.
"""

import argparse
import heapq
import json
import math
import re
import struct
import sys
import time
import zlib
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

try:
    from tools import fake_head
except ImportError:  # 스크립트로 돌 때는 tools/가 sys.path[0]
    import fake_head  # type: ignore[no-redef]

from uwbserver.pipeline import wire

US = 1_000_000
REORDER_US = 2 * US  # head_rx_us 순으로 다시 세울 때 기다리는 폭
RECENT_SEQS = 4096  # 반복된 줄을 찾을 때 기억하는 packet_seq 수(부팅마다)
SYSTEM_EVERY_US = 600 * US  # system 블록을 다시 넣는 간격(wire.md: 10분마다)
UTC_MIN_US = wire.UTC_MIN_US
UTC_AHEAD_US = wire.UTC_AHEAD_US

_PREFIX = re.compile(r"(\d+)\t(.*)", re.DOTALL)
_HEX8 = re.compile(r"[0-9a-fA-F]{8}")
_U32 = struct.Struct("<I")

ACCEL_FS = {16384: 0, 8192: 1, 4096: 2, 2048: 3}
GYRO_FS = {131.0: 0, 65.5: 1, 32.8: 2, 16.4: 3}
OLD_ACCEL_LSB_PER_G = 4096  # protocol.md: 스케일 필드가 없는 예전 기록
OLD_GYRO_LSB_PER_DPS = 32.8
IMU_TRANSPORT = {"none": 0, "espnow": 1, "uwb_data": 2}
GEOMETRY_SOURCE = {"default": 0, "survey": 1, "manual": 2}
USED_TYPES = ("range", "imu_batch", "system")


class Refused(Exception):
    """실행을 멈추는 오류(종료 코드 2). 글은 사용자에게 그대로 보입니다."""


# ------------------------------------------------------------------------------------------------------------
# 1. 줄 하나 읽기 (순수)
# ------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Event:
    """쓰는 이벤트 하나. body: range·imu_batch는 rx_offset_us = 0으로 만든 프레임 바이트, system은 payload."""

    boot_id: int
    packet_seq: int
    head_rx_us: int
    pc_us: int | None
    kind: str  # "range" | "imu_batch" | "system"
    geometry_id: str | None  # range·system에만
    body: bytes


@dataclass(frozen=True, slots=True)
class Line:
    """줄 하나를 읽은 결과. event가 None이면 skip이 까닭. seq: 공통 머리를 읽을 수 있었으면 (부팅, packet_seq)
    (쓰지 않는 type의 줄도. packet_seq 빈자리는 모든 type에 걸쳐 셈)."""

    pc_us: int | None
    event: Event | None = None
    skip: str | None = None
    seq: tuple[int, int] | None = None


class _Bad(ValueError):
    pass


def _int(v: object, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise _Bad(name)
    if isinstance(v, float):
        if not v.is_integer():
            raise _Bad(name)
        v = int(v)
    return v


def _num(v: object, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise _Bad(name)
    return float(v)


def _sat(v: int, lo: int, hi: int) -> int:
    return max(lo, min(v, hi))


def _fs(accel: object, gyro: object) -> int:
    a = OLD_ACCEL_LSB_PER_G if accel is None else _num(accel, "accel_lsb_per_g")
    g = OLD_GYRO_LSB_PER_DPS if gyro is None else round(_num(gyro, "gyro_lsb_per_dps"), 1)
    if a not in ACCEL_FS or g not in GYRO_FS:
        raise _Bad("scale")
    return ACCEL_FS[int(a)] | (GYRO_FS[g] << 4)


def range_frame(o: dict) -> bytes:
    """protocol.md range → 거리 프레임(rx_offset_us = 0)."""
    ranges = o["ranges"]
    if not isinstance(ranges, list) or len(ranges) != 4:
        raise _Bad("ranges")
    raw = []
    rssi = []
    for r in ranges:
        cm = r.get("raw_cm")
        raw.append(0 if cm is None else _sat(_int(cm, "raw_cm"), -32767, 32767))
        db = r.get("rssi_dbm")
        rssi.append(0 if db is None else _sat(round(_num(db, "rssi_dbm")), -128, 127))
    return fake_head.encode_range(
        0,
        tag_id=_int(o["tag_id"], "tag_id"),
        round_seq=_int(o["round_seq"], "round_seq"),
        raw_cm=raw,
        rssi_dbm=rssi,
        reported_mask=_int(o["reported_mask"], "reported_mask"),
        valid_mask=_int(o["valid_mask"], "valid_mask"),
        rssi_mask=_int(o["rssi_mask"], "rssi_mask"),
        slot_mask=_int(o["slot_mask"], "slot_mask"),
        explicit_anchor_ids=bool(o.get("explicit_anchor_ids", False)),
        configuration_verified=bool(o.get("configuration_verified", False)),
    )


def imu_frame(o: dict) -> bytes:
    """protocol.md imu_batch → IMU 프레임(rx_offset_us = 0)."""
    samples = o["samples"]
    if not isinstance(samples, list) or not 1 <= len(samples) <= wire.MAX_SAMPLES:
        raise _Bad("samples")
    if "count" in o and _int(o["count"], "count") != len(samples):
        raise _Bad("count")
    enc = []
    for s in samples:
        raw = s["raw"]
        if not isinstance(raw, list) or len(raw) != 7:
            raise _Bad("raw")
        enc.append(
            fake_head.encode_sample(
                _int(s["sample_seq"], "sample_seq"),
                _int(s["tag_ms"], "tag_ms"),
                [_int(v, "raw") for v in raw],
                flags=_int(s["flags"], "flags"),
            )
        )
    tbid = o["tag_boot_id"]
    if isinstance(tbid, str):
        if not _HEX8.fullmatch(tbid):
            raise _Bad("tag_boot_id")
        tbid = int(tbid, 16)
    return fake_head.encode_imu_frame(
        0,
        tag_mac=fake_head.mac_bytes(str(o["tag_mac"])),
        tag_boot_id=_int(tbid, "tag_boot_id"),
        link_seq=_int(o["link_seq"], "link_seq"),
        tx_tag_ms=_int(o["tx_tag_ms"], "tx_tag_ms"),
        samples=enc,
        tag_id=_int(o["tag_id"], "tag_id"),
        link_rssi=_int(o["link_rssi"], "link_rssi"),
        fs=_fs(o.get("accel_lsb_per_g"), o.get("gyro_lsb_per_dps")),
    )


def _members(m: object) -> list[str | None]:
    """set_members {"anchors": [A1, A2, A3], "tags": [T0, T1]} → 5칸. 없으면(예전 펌웨어) 모두 빈 칸."""
    if m is None:
        return [None] * 5
    if not isinstance(m, dict):
        raise _Bad("set_members")
    anchors = list(m.get("anchors") or []) + [None] * 3
    tags = list(m.get("tags") or []) + [None] * 2
    return [*anchors[:3], *tags[:2]]


def _firmware_id(v: object) -> bytes:
    if v is None or v == "unversioned":
        return bytes(8)
    if not isinstance(v, str) or len(v) < 16:
        raise _Bad("firmware_id")
    return bytes.fromhex(v[:16])


def system_payload(o: dict, head_mac: bytes) -> bytes:
    """protocol.md system(지금 모양과 device_id·set_members가 없는 예전 모양) → system payload 152 B."""
    anchors = sorted(o["anchors"], key=lambda a: _int(a["id"], "anchors.id"))
    if [a["id"] for a in anchors] != [0, 1, 2, 3]:
        raise _Bad("anchors")
    xyzb = []
    for a in anchors:
        xyz = a["xyz_m"]
        if not isinstance(xyz, list) or len(xyz) != 3:
            raise _Bad("xyz_m")
        xyzb.append((*(_num(v, "xyz_m") for v in xyz), _num(a.get("range_bias_m", 0.0), "range_bias_m")))
    return fake_head.encode_system(
        head_mac=head_mac,
        tag_count=_int(o["tag_count"], "tag_count"),
        geometry_id=str(o["geometry_id"]),
        anchors=xyzb,
        set_members=_members(o.get("set_members")),
        surveyed=bool(o["coordinates_surveyed"]),
        geometry_source=GEOMETRY_SOURCE[o["geometry_source"]],
        slot_ms=_int(o["slot_ms"], "slot_ms"),
        position_dimensions=_int(o["position_dimensions"], "position_dimensions"),
        module_filter=bool(_int(o["module_filter"], "module_filter")),
        imu_transport=IMU_TRANSPORT[o["imu_transport"]],
        fs=_fs(o.get("accel_lsb_per_g"), o.get("gyro_lsb_per_dps")),
        imu_acquisition_hz=_int(o["imu_acquisition_hz"], "imu_acquisition_hz"),
        imu_transport_hz=_int(o["imu_transport_hz"], "imu_transport_hz"),
        firmware_id=_firmware_id(o.get("firmware_id")),
    )


def parse_line(text: str, *, head_mac: bytes) -> Line:
    """기록 한 줄 → Line. 파일·네트워크를 모름. head_mac: --device의 MAC 6 바이트."""
    text = text.rstrip("\r\n")
    m = _PREFIX.fullmatch(text)
    pc_us = int(m.group(1)) if m else None
    body = m.group(2) if m else text
    stripped = body.strip()
    if not stripped:
        return Line(pc_us, skip="empty")
    if not stripped.startswith("{"):
        return Line(pc_us, skip="console_text")
    try:
        o = json.loads(stripped)
    except (ValueError, RecursionError):
        return Line(pc_us, skip="broken_json")
    if not isinstance(o, dict):
        return Line(pc_us, skip="broken_json")
    kind = o.get("type")
    envelope: tuple[int, int, int] | None = None  # (부팅, packet_seq, head_rx_us)
    boot = o.get("boot_id")
    try:
        if o.get("schema") == 1 and isinstance(boot, str) and _HEX8.fullmatch(boot):
            seq = _int(o.get("packet_seq"), "packet_seq")
            rx = _int(o.get("head_rx_us"), "head_rx_us")
            if seq >= 0 and rx >= 0:
                envelope = (int(boot, 16), seq, rx)
    except _Bad:
        pass
    if kind not in USED_TYPES:
        return Line(pc_us, skip=f"type:{kind}" if isinstance(kind, str) else "bad_envelope",
                    seq=envelope[:2] if envelope else None)  # fmt: skip
    if o.get("schema") != 1:
        return Line(pc_us, skip="schema")
    if envelope is None:
        return Line(pc_us, skip="bad_envelope")
    boot_id, seq, rx = envelope
    line_seq = (boot_id, seq)
    geometry = None
    try:
        if kind == "range":
            body_bytes = range_frame(o)
            geometry = str(o["geometry_id"])
        elif kind == "imu_batch":
            body_bytes = imu_frame(o)
        else:
            did = o.get("device_id")
            if did is not None and did != "head-" + head_mac.hex():
                return Line(pc_us, skip="other_device", seq=line_seq)
            body_bytes = system_payload(o, head_mac)
            geometry = str(o["geometry_id"])
    except (_Bad, KeyError, TypeError, ValueError, struct.error, OverflowError):
        return Line(pc_us, skip=f"bad_{kind}", seq=line_seq)
    return Line(pc_us, Event(boot_id, seq, rx, pc_us, kind, geometry, body_bytes), seq=line_seq)


# ------------------------------------------------------------------------------------------------------------
# 2. 시각 맞추기 (순수)
# ------------------------------------------------------------------------------------------------------------


@dataclass
class Scan:
    """첫 번째 읽기의 결과: 시각 맞추기에 필요한 것과 멈출 까닭."""

    first_pc_us: int | None = None  # 파일 첫 줄의 PC 시각(첫 줄에 없으면 None → 맨 JSON 모드)
    first_line: bool = True
    first_event_rx: dict[int, int] = field(default_factory=dict)  # 부팅 → 파일 순서로 첫 이벤트의 head_rx_us
    min_pc_minus_rx: dict[int, int] = field(default_factory=dict)  # 부팅 → min(PC 시각 − head_rx_us)
    other_device: int = 0
    events: int = 0

    def add(self, line: Line) -> None:
        if self.first_line and not (line.skip == "empty" and line.pc_us is None):
            self.first_line = False
            self.first_pc_us = line.pc_us
        if line.skip == "other_device":
            self.other_device += 1
        ev = line.event
        if ev is None:
            return
        self.events += 1
        self.first_event_rx.setdefault(ev.boot_id, ev.head_rx_us)
        if ev.pc_us is not None:
            d = ev.pc_us - ev.head_rx_us
            old = self.min_pc_minus_rx.get(ev.boot_id)
            if old is None or d < old:
                self.min_pc_minus_rx[ev.boot_id] = d


def utc_offsets(scan: Scan, utc_start_us: int) -> dict[int, int]:
    """부팅 → (UTC µs − Head µs). Refused로 멈출 까닭을 알림."""
    if not scan.first_event_rx:
        raise Refused("쓸 이벤트(range, imu_batch, system)가 하나도 없습니다")
    if scan.first_pc_us is None:
        if len(scan.first_event_rx) > 1:
            raise Refused(
                "줄 앞에 PC 시각이 없는 기록에 부팅이 둘 이상 있습니다. 두 번째 부팅부터의 시각을 알 수 없어 넣지 않습니다"
            )
        ((boot, rx),) = scan.first_event_rx.items()
        return {boot: utc_start_us - rx}
    out = {}
    for boot in scan.first_event_rx:
        d = scan.min_pc_minus_rx.get(boot)
        if d is None:
            raise Refused(f"부팅 {boot:08x}의 줄에 PC 시각이 없어 시각을 맞출 수 없습니다")
        out[boot] = utc_start_us + d - scan.first_pc_us
    return out


# ------------------------------------------------------------------------------------------------------------
# 3. 1초 묶음 만들기 (순수)
# ------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Built:
    """만든 묶음 하나."""

    data: bytes
    boot_id: int
    seq: int
    head_us_start: int
    utc_us_start: int
    n_range: int
    n_imu: int
    dropped_range: int
    dropped_imu: int
    has_system: bool


@dataclass
class _Window:
    start: int
    ranges: list[tuple[int, bytes]] = field(default_factory=list)
    imus: list[tuple[int, bytes]] = field(default_factory=list)
    system: tuple[int, bytes] | None = None  # (head_rx_us, payload)

    def empty(self) -> bool:
        return not self.ranges and not self.imus and self.system is None


@dataclass
class _Closed:
    window: _Window
    seq: int


@dataclass
class _Boot:
    boot_id: int
    utc_offset: int
    heap: list = field(default_factory=list)  # (head_rx_us, 들어온 순서, Event)
    max_rx: int = -1
    released_rx: int = -1
    window: _Window | None = None
    geometry: str | None = None
    seq: int = 0
    held: list[_Closed] = field(default_factory=list)  # geometry_id를 아직 모르는 닫힌 창
    last_system: bytes | None = None
    last_system_rx: int = 0


@dataclass
class Stats:
    lines: int = 0
    skipped: Counter = field(default_factory=Counter)
    events: Counter = field(default_factory=Counter)
    seq_gaps: list[tuple[int, int, int]] = field(default_factory=list)  # (부팅, 앞 packet_seq, 뒤 packet_seq)
    seq_backwards: int = 0
    late: int = 0
    no_geometry: int = 0  # geometry_id를 끝까지 모른 묶음(머리 geometry_hash = CRC-32(""))
    system_events: int = 0
    system_blocks: int = 0


class Converter:
    """이벤트(파일 순서) → 묶음. 파일·네트워크를 모름. feed()가 그 이벤트로 닫힌 묶음을 돌려줍니다."""

    def __init__(self, utc_offsets: dict[int, int], stats: Stats | None = None) -> None:
        self._offsets = utc_offsets
        self._boots: dict[int, _Boot] = {}
        self._order = 0
        self._last_seq: dict[int, int] = {}
        self._recent: dict[int, tuple[deque[int], set[int]]] = {}
        self.stats = stats if stats is not None else Stats()

    # -- 파일 순서 단계: packet_seq 확인, 다시 세우기 ---------------------------------------------------------

    def feed_line(self, line: Line) -> list[Built]:
        """줄 하나(파일 순서). packet_seq를 모든 type에 걸쳐 확인하고, 쓰는 이벤트면 feed()로 넘깁니다."""
        if line.seq is not None:
            boot, seq = line.seq
            recent = self._recent.setdefault(boot, (deque(), set()))
            if seq in recent[1]:
                self.stats.skipped["repeated_packet_seq"] += 1  # 같은 줄이 두 번 기록됨
                return []
            last = self._last_seq.get(boot)
            if last is not None and seq > last + 1:
                self.stats.seq_gaps.append((boot, last, seq))
            elif last is not None and seq < last:
                self.stats.seq_backwards += 1  # 순서만 바뀐 줄: 쓰고 셈
            if last is None or seq > last:
                self._last_seq[boot] = seq
            recent[0].append(seq)
            recent[1].add(seq)
            if len(recent[0]) > RECENT_SEQS:
                recent[1].discard(recent[0].popleft())
        if line.event is None:
            self.stats.skipped[line.skip or "?"] += 1
            return []
        return self.feed(line.event)

    def feed(self, ev: Event) -> list[Built]:
        b = self._boots.get(ev.boot_id)
        if b is None:
            b = self._boots[ev.boot_id] = _Boot(ev.boot_id, self._offsets[ev.boot_id])
        if ev.head_rx_us < b.released_rx:
            self.stats.late += 1
            return []
        self.stats.events[ev.kind] += 1
        self._order += 1
        heapq.heappush(b.heap, (ev.head_rx_us, self._order, ev))
        b.max_rx = max(b.max_rx, ev.head_rx_us)
        out: list[Built] = []
        while b.heap and b.heap[0][0] <= b.max_rx - REORDER_US:
            rx, _, e = heapq.heappop(b.heap)
            b.released_rx = rx
            self._place(b, e, out)
        return out

    def finish(self) -> list[Built]:
        out: list[Built] = []
        for b in self._boots.values():
            while b.heap:
                rx, _, e = heapq.heappop(b.heap)
                b.released_rx = rx
                self._place(b, e, out)
            if b.window is not None and not b.window.empty():
                self._close(b, out)
            if b.held:
                self.stats.no_geometry += len(b.held)
                self._flush_held(b, "", out)
        return out

    # -- head_rx_us 순서 단계: 창 -------------------------------------------------------------------------

    def _place(self, b: _Boot, ev: Event, out: list[Built]) -> None:
        rx = ev.head_rx_us
        w = b.window
        if w is None:
            w = b.window = _Window(rx)
        elif rx >= w.start + US:
            if not w.empty():
                self._close(b, out)
            w = b.window = _Window(w.start + (rx - w.start) // US * US)
        g = ev.geometry_id
        if g is not None and g != b.geometry:
            if b.geometry is not None and not w.empty():
                self._close(b, out)  # 배치가 바뀜: 이 이벤트에서 닫고 이 시각에서 새 창
                w = b.window = _Window(rx)
            first = b.geometry is None
            b.geometry = g
            if first and b.held:
                self._flush_held(b, g, out)
        if ev.kind == "range":
            w.ranges.append((rx, ev.body))
        elif ev.kind == "imu_batch":
            w.imus.append((rx, ev.body))
        else:
            self.stats.system_events += 1
            if (
                w.system is not None
                or b.last_system != ev.body
                or rx - b.last_system_rx >= SYSTEM_EVERY_US
            ):
                w.system = (rx, ev.body)

    def _close(self, b: _Boot, out: list[Built]) -> None:
        w = b.window
        assert w is not None
        closed = _Closed(w, b.seq)
        b.seq += 1
        b.window = None
        if w.system is not None:
            b.last_system, b.last_system_rx = w.system[1], w.system[0]
        if b.geometry is None:
            b.held.append(closed)
        else:
            out.append(self._encode(b, closed, b.geometry))

    def _flush_held(self, b: _Boot, geometry: str, out: list[Built]) -> None:
        for c in b.held:
            out.append(self._encode(b, c, geometry))
        b.held.clear()

    def _encode(self, b: _Boot, c: _Closed, geometry: str) -> Built:
        w = c.window
        ranges = w.ranges[: wire.MAX_RANGE]
        imus = w.imus[: wire.MAX_IMU_FRAMES]
        dropped_range = len(w.ranges) - len(ranges)
        dropped_imu = len(w.imus) - len(imus)
        blocks = []
        if w.system is not None:
            self.stats.system_blocks += 1
            blocks.append(
                fake_head.encode_block(fake_head.KIND_SYSTEM, w.system[1], at_offset_us=w.system[0] - w.start)
            )
        utc = w.start + b.utc_offset
        data = fake_head.encode_chunk(
            boot_id=b.boot_id,
            seq=c.seq,
            head_us_start=w.start,
            geometry_hash=zlib.crc32(geometry.encode("ascii", "replace")),
            ranges=[_U32.pack(rx - w.start) + f[4:] for rx, f in ranges],
            imus=[_U32.pack(rx - w.start) + f[4:] for rx, f in imus],
            blocks=blocks,
            utc_us_start=utc,
            flags=fake_head.FLAG_UTC,
            dropped_range=min(dropped_range, 0xFFFF),
            dropped_imu_frames=min(dropped_imu, 0xFFFF),
        )
        return Built(data, b.boot_id, c.seq, w.start, utc, len(ranges), len(imus), dropped_range, dropped_imu,
                     w.system is not None)  # fmt: skip


def convert(lines: Iterable[str], *, head_mac: bytes, utc_offsets: dict[int, int], stats: Stats) -> Iterator[Built]:
    """기록의 줄들 → 묶음들(부팅마다 seq 순서). 순수: 같은 줄이면 같은 바이트."""
    conv = Converter(utc_offsets, stats)
    for text in lines:
        stats.lines += 1
        yield from conv.feed_line(parse_line(text, head_mac=head_mac))
    yield from conv.finish()


def scan_lines(lines: Iterable[str], *, head_mac: bytes) -> Scan:
    scan = Scan()
    for text in lines:
        scan.add(parse_line(text, head_mac=head_mac))
    return scan


def batches(chunks: Iterable[bytes]) -> Iterator[list[bytes]]:
    """요청 하나에 묶음 16개, 본문 131,072 B까지(wire.md 요청 본문 프레이밍)."""
    batch: list[bytes] = []
    size = 0
    for c in chunks:
        n = 4 + len(c)
        if batch and (len(batch) == fake_head.MAX_REQUEST_CHUNKS or size + n > fake_head.MAX_REQUEST_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(c)
        size += n
    if batch:
        yield batch


# ------------------------------------------------------------------------------------------------------------
# 4. 바깥 일: 파일 읽기, 보내기
# ------------------------------------------------------------------------------------------------------------


def read_lines(path: Path) -> Iterator[str]:
    """한 줄씩. 깨진 UTF-8은 바꿔 읽음(그 줄은 JSON이 아니게 되어 건너뜀)."""
    with path.open("rb") as f:
        for raw in f:
            yield raw.decode("utf-8", errors="replace")


class SendError(Exception):
    pass


class Sender:
    """POST /d/v1/chunks. client는 httpx.Client와 같은 post(url, content=, headers=, timeout=)를 가진 것."""

    def __init__(self, client, url: str, token: str, *, timeout: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
        self.client = client
        self.url = url.rstrip("/") + "/d/v1/chunks"
        self.token = token
        self.timeout = timeout
        self.sleep = sleep
        self.requests = 0
        self.http: Counter = Counter()

    def send(self, batch: list[bytes]) -> list[int]:
        body = fake_head.frame_request(batch)
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/octet-stream"}
        for delay in (*fake_head.BACKOFF_S, None):
            self.requests += 1
            try:
                resp = self.client.post(self.url, content=body, headers=headers, timeout=self.timeout)
            except Exception as e:  # 연결 실패·시간 초과: 확인 못 받음 → 다시 보냄
                self.http[type(e).__name__] += 1
                status = None
            else:
                status = resp.status_code
                self.http[status] += 1
                if status == 200:
                    try:
                        _, codes = fake_head.decode_ack(resp.content)
                    except ValueError as e:
                        raise SendError(f"응답 본문이 wire.md의 응답 형식이 아님: {e}") from e
                    if len(codes) != len(batch):
                        raise SendError(f"응답 코드 {len(codes)}개, 보낸 묶음 {len(batch)}개")
                    return codes
                if status == 401:
                    raise SendError("401: 토큰이 틀렸거나 폐기됨")
                if status in (400, 413):
                    raise SendError(f"{status}: 요청 본문을 서버가 받지 않음(이 도구의 잘못)")
                if status < 500:
                    raise SendError(f"HTTP {status}")
            if delay is None:
                raise SendError(f"다시 보내기를 모두 실패함(마지막 {status if status else '연결 실패'})")
            self.sleep(delay)
        raise AssertionError("unreachable")


# ------------------------------------------------------------------------------------------------------------
# 5. 한 번 돌리기와 CLI
# ------------------------------------------------------------------------------------------------------------


@dataclass
class BootSummary:
    chunks: int = 0
    first_seq: int = 0
    last_seq: int = 0
    first_utc_us: int = 0
    last_utc_us: int = 0


@dataclass
class Report:
    stats: Stats
    boots: dict[int, BootSummary] = field(default_factory=dict)
    chunks: int = 0
    frameless: int = 0
    n_range: int = 0
    n_imu: int = 0
    dropped_range: int = 0
    dropped_imu: int = 0
    bytes: int = 0
    check: Counter = field(default_factory=Counter)  # "0 ok" 또는 "8 bad_field header.utc_us_start"
    sent_codes: Counter = field(default_factory=Counter)
    requests: int = 0
    http: Counter = field(default_factory=Counter)
    send_error: str | None = None

    @property
    def rejected_by_check(self) -> int:
        return sum(n for k, n in self.check.items() if k != "ok")

    @property
    def rejected_by_server(self) -> int:
        return sum(n for c, n in self.sent_codes.items() if c >= 2)


def _check_key(r: wire.Chunk | wire.Rejection) -> str:
    if isinstance(r, wire.Chunk):
        return "ok"
    return f"{int(r.code)} {r.code.name.lower()} {r.detail}"


def import_lines(read: Callable[[], Iterable[str]], *, head_mac: bytes, utc_start_us: int,
                 server_utc_us: int, sender: Sender | None) -> Report:  # fmt: skip
    """기록을 두 번 읽어(read()를 두 번 부름) 묶음을 만들고 검사하고, sender가 있으면 보냅니다."""
    scan = scan_lines(read(), head_mac=head_mac)
    if scan.other_device:
        raise Refused(
            f"기록의 system device_id가 --device({wire.format_mac(head_mac)})와 다릅니다({scan.other_device}줄)"
        )
    offsets = utc_offsets(scan, utc_start_us)
    report = Report(Stats())

    def built() -> Iterator[bytes]:
        for c in convert(read(), head_mac=head_mac, utc_offsets=offsets, stats=report.stats):
            report.chunks += 1
            report.bytes += len(c.data)
            report.n_range += c.n_range
            report.n_imu += c.n_imu
            report.frameless += c.n_range == 0 and c.n_imu == 0
            report.dropped_range += c.dropped_range
            report.dropped_imu += c.dropped_imu
            s = report.boots.get(c.boot_id)
            if s is None:
                s = report.boots[c.boot_id] = BootSummary(first_seq=c.seq, first_utc_us=c.utc_us_start)
            s.chunks += 1
            s.last_seq = c.seq
            s.last_utc_us = c.utc_us_start
            report.check[_check_key(wire.check_chunk(c.data, device_mac=head_mac, server_utc_us=server_utc_us))] += 1
            yield c.data

    if sender is None:
        for _ in built():
            pass
        return report
    try:
        for batch in batches(built()):
            report.sent_codes.update(sender.send(batch))
    except SendError as e:
        report.send_error = str(e)
    finally:
        report.requests = sender.requests
        report.http = sender.http
    return report


def _iso(us: int) -> str:
    return datetime.fromtimestamp(us / US, tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_report(r: Report, *, dry_run: bool) -> str:
    s = r.stats
    used = sum(s.events.values())
    lines = [
        f"읽은 줄 {s.lines}, 쓴 이벤트 {used} (" + ", ".join(f"{k} {s.events[k]}" for k in USED_TYPES if s.events[k]) + ")",
        "건너뛴 줄: " + (", ".join(f"{k} {n}" for k, n in sorted(s.skipped.items())) or "없음"),
        f"늦게 나타나 건너뛴 이벤트(late) {s.late}",
        (
            f"packet_seq 빈자리 {len(s.seq_gaps)}곳, 빠진 이벤트 {sum(b - a - 1 for _, a, b in s.seq_gaps)}개, "
            f"뒤로 간 곳 {s.seq_backwards}"
        ),
        f"부팅 {len(r.boots)}개",
    ]
    for boot, b in r.boots.items():
        lines.append(
            f"  {boot:08x}: 묶음 {b.chunks}, seq {b.first_seq}–{b.last_seq}, "
            f"UTC {_iso(b.first_utc_us)} – {_iso(b.last_utc_us)}"
        )
    lines += [
        (
            f"묶음 {r.chunks} ({r.bytes} B), 프레임 없는 묶음 {r.frameless}, system 블록 {s.system_blocks}"
            f"(system 이벤트 {s.system_events}개 중)"
        ),
        f"거리 프레임 {r.n_range}, IMU 프레임 {r.n_imu}, 한도를 넘어 버린 거리 {r.dropped_range}·IMU {r.dropped_imu}",
    ]
    if s.no_geometry:
        lines.append(f"geometry_id를 끝까지 몰라 geometry_hash = 0으로 둔 묶음 {s.no_geometry}")
    lines.append("검사(check_chunk): " + ", ".join(f"{k} {n}" for k, n in sorted(r.check.items())))
    if dry_run:
        lines.append("--dry-run: 보내지 않음")
    else:
        names = {c.value: c.name.lower() for c in wire.Code}
        codes = ", ".join(f"{c} {names.get(c, '?')} {n}" for c, n in sorted(r.sent_codes.items())) or "없음"
        http = ", ".join(f"{k} {n}" for k, n in sorted(r.http.items(), key=str))
        lines.append(f"보냄: 요청 {r.requests}번(HTTP {http}), 응답 코드 {codes}")
        if r.send_error:
            lines.append(f"보내기 멈춤: {r.send_error}")
    return "\n".join(lines)


def parse_utc(text: str) -> int:
    """ISO 8601 시각(시간대 필수) → UTC µs."""
    try:
        t = datetime.fromisoformat(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"ISO 8601 시각이 아님: {text!r}") from e
    if t.tzinfo is None:
        raise argparse.ArgumentTypeError(f"시간대가 없음: {text!r}. 2026-10-20T10:24:00Z나 +09:00처럼 붙이세요")
    return int(t.timestamp()) * US + t.microsecond


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="import_usb.py",
        description="Head USB 기록(JSONL)을 wire v1 1초 묶음으로 바꿔 intake에 보냅니다(TL-7).",
        epilog="종료 코드: 0 모든 묶음이 stored·duplicate(--dry-run은 검사 통과), 1 거절된(될) 묶음이 있음, "
        "2 인자 오류·파일 오류·기록이 --device와 다름·보내기 실패.",
    )
    p.add_argument("file", type=Path, help="Head USB 기록 파일(record_usb.py의 <PC µs>TAB<줄> 또는 맨 JSONL)")
    p.add_argument("--device", required=True, metavar="MAC=TOKEN", help="Head MAC과 장치 토큰")
    p.add_argument("--utc-start", required=True, type=parse_utc, metavar="ISO",
                   help="기록 첫 줄의 UTC 시각(레코더를 시작한 시각). 시간대 필수")  # fmt: skip
    p.add_argument("--url", default="http://localhost:8001", help="intake 주소(기본 %(default)s)")
    p.add_argument("--timeout", type=float, default=30.0, help="요청 하나의 HTTP 시간 한도 초(기본 %(default)s)")
    p.add_argument("--dry-run", action="store_true", help="묶음을 만들어 검사만 하고 보내지 않음")
    return p


def main(argv: Sequence[str] | None = None, *, client=None) -> int:
    """client: 시험에서 httpx.Client 대신 넣는 것(예: FastAPI TestClient)."""
    p = build_parser()
    args = p.parse_args(argv)
    mac_text, _, token = args.device.partition("=")
    try:
        head_mac = fake_head.mac_bytes(mac_text)
    except ValueError:
        p.error(f"--device의 MAC이 틀림: {args.device!r}")
    if head_mac[0] & 1:
        p.error(f"--device의 MAC이 멀티캐스트 주소임: {mac_text!r}")
    if not token and not args.dry_run:
        p.error(f"--device는 MAC=TOKEN 꼴이어야 함: {args.device!r}")
    now_us = time.time_ns() // 1000
    if not UTC_MIN_US <= args.utc_start <= now_us + UTC_AHEAD_US:
        p.error(f"--utc-start {_iso(args.utc_start)}가 2025-01-01 이후, 지금 + 1일 이전이 아님(wire.md utc_us_start)")
    if not args.file.is_file():
        p.error(f"파일이 없음: {args.file}")

    def run(sender: Sender | None) -> Report:
        return import_lines(lambda: read_lines(args.file), head_mac=head_mac, utc_start_us=args.utc_start,
                            server_utc_us=now_us, sender=sender)  # fmt: skip

    try:
        if args.dry_run:
            report = run(None)
        elif client is not None:
            report = run(Sender(client, args.url, token, timeout=args.timeout))
        else:
            import httpx

            with httpx.Client(timeout=args.timeout) as c:
                report = run(Sender(c, args.url, token, timeout=args.timeout))
    except Refused as e:
        print(f"import_usb: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"import_usb: 파일을 읽지 못함: {e}", file=sys.stderr)
        return 2

    for boot, a, b in report.stats.seq_gaps[:20]:
        print(f"경고: 부팅 {boot:08x} packet_seq {a} → {b} ({b - a - 1}개 빠짐, USB 출력 생략)", file=sys.stderr)
    if len(report.stats.seq_gaps) > 20:
        print(f"경고: packet_seq 빈자리 {len(report.stats.seq_gaps) - 20}곳 더", file=sys.stderr)
    print(format_report(report, dry_run=args.dry_run))
    if report.send_error:
        return 2
    if args.dry_run:
        return 1 if report.rejected_by_check else 0
    return 1 if report.rejected_by_server else 0


if __name__ == "__main__":
    sys.exit(main())
