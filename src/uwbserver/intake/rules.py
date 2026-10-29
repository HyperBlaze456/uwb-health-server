"""받기의 규칙 (IN-3, IN-5, IN-7). 순수 계산: 검사 결과 코드와 행 만들기.

검사 자체는 pipeline/wire.py 하나가 합니다(IN-3). 여기서는 그 결과에 중복을 더하고(IN-5),
표에 넣을 값을 만듭니다(IN-7).
"""

from dataclasses import dataclass, replace
from datetime import datetime

from uwbserver.pipeline import clock, wire

AGE_NONE = 0xFFFF  # wire의 "부팅 뒤 없음" → NULL
STATUS_BIT = {wire.KIND_SYSTEM: 1, wire.KIND_HEALTH: 2, wire.KIND_SURVEY: 4}


@dataclass(frozen=True, slots=True)
class Verdict:
    """요청 안 묶음 하나의 결과."""

    data: bytes
    code: int  # wire.Code
    check: int | None = None  # 거절한 검사 단계(2–9)
    detail: str | None = None
    chunk: wire.Chunk | None = None  # 2–9를 통과했으면

    @property
    def key(self) -> tuple[int, int] | None:
        if self.chunk is None:
            return None
        h = self.chunk.header
        return (h.head_boot_id, h.chunk_seq)

    @property
    def stored(self) -> bool:
        return self.code == wire.Code.STORED

    @property
    def rejected(self) -> bool:
        return self.code >= wire.Code.CRC


def check_records(records: list[bytes], *, device_mac: bytes, server_utc_us: int) -> list[Verdict]:
    """wire.md 검사 2–9 (IN-3). 중복(10)은 mark_duplicates가."""
    out: list[Verdict] = []
    for data in records:
        r = wire.check_chunk(data, device_mac=device_mac, server_utc_us=server_utc_us)
        if isinstance(r, wire.Rejection):
            out.append(Verdict(data, int(r.code), r.check, r.detail))
        else:
            out.append(Verdict(data, int(wire.Code.STORED), chunk=r))
    return out


def mark_duplicates(verdicts: list[Verdict], existing: set[tuple[int, int]]) -> list[Verdict]:
    """IN-5: 이미 있는 키, 또는 같은 요청 안에서 앞에 나온 키는 duplicate(1)."""
    seen = set(existing)
    out: list[Verdict] = []
    for v in verdicts:
        k = v.key
        if v.stored and k is not None:
            if k in seen:
                v = replace(v, code=int(wire.Code.DUPLICATE))
            seen.add(k)
        out.append(v)
    return out


def chunk_time_us(header: wire.Header, received_us: int) -> int:
    """묶음 시각: flags bit0이면 utc_us_start, 아니면 수신 시각 − 1초 (IN-7, wire.md)."""
    return header.utc_us_start if header.flags & 1 else received_us - 1_000_000


def _first_tag(chunk: wire.Chunk, tag_id: int) -> tuple[str | None, int | None]:
    for f in chunk.imu_frames:
        if f.tag_id == tag_id:
            return wire.format_mac(f.tag_mac), f.tag_boot_id
    return None, None


def chunk_row(
    device_id: str, chunk: wire.Chunk, received_us: int, raw_path: str, raw_offset: int
) -> dict:
    """intake.chunk 행 (IN-7)."""
    h = chunk.header
    status_mask = 0
    for b in chunk.blocks:
        status_mask |= STATUS_BIT[b.kind]
    tag0_mac, tag0_boot = _first_tag(chunk, 0)
    tag1_mac, tag1_boot = _first_tag(chunk, 1)
    return {
        "device_id": device_id,
        "head_boot_id": h.head_boot_id,
        "chunk_seq": h.chunk_seq,
        "received_at": clock.from_us(received_us),
        "head_us_start": h.head_us_start,
        "utc_us_start": h.utc_us_start,
        "geometry_hash": h.geometry_hash,
        "flags": h.flags,
        "n_range": h.n_range,
        "n_imu_frames": h.n_imu_frames,
        "dropped_range": h.dropped_range,
        "dropped_imu_frames": h.dropped_imu_frames,
        "status_mask": status_mask,
        "tag_mask": h.tag_mask,
        "tag0_mac": tag0_mac,
        "tag0_boot_id": tag0_boot,
        "tag1_mac": tag1_mac,
        "tag1_boot_id": tag1_boot,
        "raw_path": raw_path,
        "raw_offset": raw_offset,
        "raw_len": len(chunk.data),
    }


def _age(value: int) -> int | None:
    return None if value == AGE_NONE else value


def block_rows(device_id: str, chunk: wire.Chunk, received_us: int) -> dict[str, dict]:
    """상태 블록 행들 (IN-7). 키는 'system'·'health'·'survey'. block은 payload 바이트 그대로."""
    h = chunk.header
    base_us = chunk_time_us(h, received_us)
    key = {"device_id": device_id, "head_boot_id": h.head_boot_id, "chunk_seq": h.chunk_seq}
    rows: dict[str, dict] = {}
    for b in chunk.blocks:
        at = clock.from_us(base_us + b.at_offset_us)
        if b.kind == wire.KIND_HEALTH:
            d = wire.decode_health(b.payload)
            rows["health"] = {
                **key,
                "at": at,
                "flags": d["flags"],
                "buffer_used": d["buffer_used"],
                "range_age_ms": _age(d["range_age_ms"]),
                "imu_age_ms": _age(d["imu_age_ms"]),
                "block": b.payload,
            }
        elif b.kind == wire.KIND_SYSTEM:
            d = wire.decode_system(b.payload)
            rows["system"] = {
                **key,
                "at": at,
                "tag_count": d["tag_count"],
                "geometry_hash": h.geometry_hash,
                "firmware_id": b.payload[112:120],
                "block": b.payload,
            }
        elif b.kind == wire.KIND_SURVEY:
            d = wire.decode_survey(b.payload)
            rows["survey"] = {
                **key,
                "at": at,
                "solved": d["status"] == 1,
                "survey_id": d["survey_id"],
                "revision": d["revision"],
                "block": b.payload,
            }
    return rows


def rejected_row(
    device_id: str,
    *,
    received_us: int,
    code: int,
    check: int | None,
    detail: str | None,
    data: bytes,
    path: str,
    offset: int,
) -> dict:
    """intake.rejected 행. 머리를 읽을 수 있었으면 키도 적습니다(IN-7)."""
    key = wire.peek_key(data) if code != wire.FRAMING_CODE else None
    return {
        "device_id": device_id,
        "received_at": clock.from_us(received_us),
        "code": code,
        "check_step": check,
        "detail": detail,
        "head_boot_id": key[0] if key else None,
        "chunk_seq": key[1] if key else None,
        "path": path,
        "file_offset": offset,
        "len": len(data),
    }


def reindex_since_hour(max_received_at: datetime | None) -> datetime | None:
    """IN-9: (최대 received_at − 1시간)의 hour. 표가 비어 있으면 None(= 전부)."""
    if max_received_at is None:
        return None
    t = clock.from_us(clock.to_us(max_received_at) - 3_600_000_000)
    return t.replace(minute=0, second=0, microsecond=0)
