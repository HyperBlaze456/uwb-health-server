"""구간 규칙 (SG-2–SG-8). 순수 계산: 묶음 하나를 어느 구간에 어떻게 넣는지.

구간 = 한 장치·한 부팅 안에서 끊김 없이 이어진 묶음들. 묶음이 어느 구간에 들어가는지는 묶음 안의 값
(seq, Head 시각, Tag, 배치)으로 정하고, 도착 시각은 언제 닫을지만 정합니다.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from uwbserver.pipeline import clock, wire

Side = Literal["left", "right"]
SIDE_OF_TAG: dict[int, Side] = {0: "right", 1: "left"}  # tag_id 0 = T0 오른쪽, 1 = T1 왼쪽. tag_mask bit = 1 << tag_id
SOURCE = {0: "default", 1: "survey", 2: "manual"}
SECOND_US = 1_000_000


@dataclass(frozen=True, slots=True)
class ChunkIn:
    """segment가 보는 묶음 하나(intake.chunk 행 + 있으면 system 블록)."""

    id: int
    device_id: str
    head_boot_id: int
    seq: int
    received_at: datetime
    head_us_start: int
    utc_us_start: int
    geometry_hash: int
    flags: int
    n_range: int
    n_imu_frames: int
    dropped: int  # dropped_range + dropped_imu_frames
    tag_mask: int
    tags: dict[Side, tuple[str, int] | None]  # tag_mask에 있는 발 → (tag_mac, tag_boot_id) 또는 IMU 없음(None)
    system_block: bytes | None = None

    @property
    def framed(self) -> bool:
        """SG-2: 프레임이 하나라도 있는 묶음만 구간에 들어감."""
        return self.n_range > 0 or self.n_imu_frames > 0

    @property
    def utc_known(self) -> bool:
        return bool(self.flags & 1)


@dataclass(frozen=True, slots=True)
class Seg:
    """segment.segment 행. id가 None이면 아직 넣지 않은 새 구간."""

    id: int | None
    device_id: str
    head_boot_id: int
    geometry_hash: int
    installation_id: int | None
    sides: str
    first_seq: int
    last_seq: int
    n_chunks: int
    n_missing_chunks: int
    n_dropped_frames: int
    first_head_us: int
    last_head_us: int
    utc_offset_us: int
    time_source: str
    start_at: datetime
    end_at: datetime
    state: str
    closed_reason: str | None
    revision: int
    last_chunk_at: datetime


@dataclass(frozen=True, slots=True)
class SegTag:
    side: Side
    tag_mac: str | None
    tag_boot_id: int | None
    first_seen_at: datetime


@dataclass(frozen=True, slots=True)
class Settings:
    gap_s: int = 60
    max_s: int = 3600
    silence_s: int = 60


# ---------------------------------------------------------------- 시각 (SG-7)


def _with_times(seg: Seg) -> Seg:
    start = clock.from_us(seg.first_head_us + seg.utc_offset_us)
    end = clock.from_us(seg.last_head_us + seg.utc_offset_us + SECOND_US)
    return replace(seg, start_at=start, end_at=end)


def _time_from(c: ChunkIn) -> tuple[int, str]:
    if c.utc_known:
        return c.utc_us_start - c.head_us_start, "server"
    return clock.to_us(c.received_at) - SECOND_US - c.head_us_start, "arrival"


def _adopt_server_time(seg: Seg, c: ChunkIn) -> Seg:
    """SG-7: 처음 들어온 bit0 묶음이 시각 기준. 이미 'server'면 그대로."""
    if seg.time_source == "server" or not c.utc_known:
        return seg
    return replace(seg, utc_offset_us=c.utc_us_start - c.head_us_start, time_source="server")


# ---------------------------------------------------------------- Tag와 발 (SG-6)


def sides_of(tags: dict[Side, object]) -> str:
    present = set(tags)
    if present == {"left", "right"}:
        return "both"
    (only,) = present
    return only


@dataclass(frozen=True, slots=True)
class TagChange:
    side: Side
    action: Literal["insert", "fill"]
    tag_mac: str | None
    tag_boot_id: int | None
    first_seen_at: datetime


def tag_changes(c: ChunkIn, existing: dict[Side, SegTag]) -> tuple[list[TagChange], list[Side]]:
    """SG-6: 없는 발 자리는 넣고, MAC이 비어 있던 자리는 처음 IMU 프레임이 오면 채움.

    돌려주는 값: (바꿀 것, MAC이 달라 경고할 발).
    """
    changes: list[TagChange] = []
    conflicts: list[Side] = []
    for side, tag in c.tags.items():
        mac, boot = tag if tag else (None, None)
        old = existing.get(side)
        if old is None:
            changes.append(TagChange(side, "insert", mac, boot, c.received_at))
        elif old.tag_mac is None and mac is not None:
            changes.append(TagChange(side, "fill", mac, boot, old.first_seen_at))
        elif old.tag_mac is not None and mac is not None and (old.tag_mac, old.tag_boot_id) != (mac, boot):
            conflicts.append(side)
    return changes, conflicts


def apply_tag_changes(existing: dict[Side, SegTag], changes: list[TagChange]) -> dict[Side, SegTag]:
    out = dict(existing)
    for ch in changes:
        out[ch.side] = SegTag(ch.side, ch.tag_mac, ch.tag_boot_id, ch.first_seen_at)
    return out


# ---------------------------------------------------------------- 넣을 자리 (SG-3, SG-4)


def split_reason(c: ChunkIn, p: Seg, p_tags: dict[Side, SegTag], s: Settings) -> str | None:
    """SG-4: P 뒤에 올 때의 나눔 조건. 위에서부터 먼저 맞는 것."""
    gap_us = c.head_us_start - p.last_head_us - SECOND_US
    if gap_us >= s.gap_s * SECOND_US or c.head_us_start <= p.last_head_us:
        return "gap_60s"
    if c.geometry_hash != p.geometry_hash:
        return "geometry_changed"
    for side, tag in c.tags.items():
        old = p_tags.get(side)
        if tag is not None and old is not None and old.tag_mac is not None:
            if (old.tag_mac, old.tag_boot_id) != tag:
                return "tag_changed"
    if c.head_us_start - p.first_head_us >= s.max_s * SECOND_US:
        return "max_length"
    return None


def new_segment(c: ChunkIn, installation_id: int | None = None) -> Seg:
    offset, source = _time_from(c)
    sides = sides_of(dict.fromkeys(c.tags))  # 넣는 쪽이 segment_tag 행으로 다시 셈(SG-6)
    seg = Seg(
        id=None,
        device_id=c.device_id,
        head_boot_id=c.head_boot_id,
        geometry_hash=c.geometry_hash,
        installation_id=installation_id,
        sides=sides,
        first_seq=c.seq,
        last_seq=c.seq,
        n_chunks=1,
        n_missing_chunks=0,
        n_dropped_frames=c.dropped,
        first_head_us=c.head_us_start,
        last_head_us=c.head_us_start,
        utc_offset_us=offset,
        time_source=source,
        start_at=c.received_at,
        end_at=c.received_at,
        state="open",
        closed_reason=None,
        revision=0,
        last_chunk_at=c.received_at,
    )
    return _with_times(seg)


def add_inside(p: Seg, c: ChunkIn) -> Seg:
    """SG-3 1번: 늦게 온 묶음이 P 안의 빈자리를 메움."""
    seg = replace(
        p,
        n_chunks=p.n_chunks + 1,
        n_missing_chunks=p.n_missing_chunks - 1,
        n_dropped_frames=p.n_dropped_frames + c.dropped,
        revision=p.revision + 1,
        last_chunk_at=c.received_at,
        state="open",
        closed_reason=None,
    )
    return _with_times(_adopt_server_time(seg, c))


def append_after(p: Seg, c: ChunkIn) -> Seg:
    """SG-3 2번: 나눔 조건 없이 P 뒤에 이어 붙임."""
    seg = replace(
        p,
        last_seq=c.seq,
        last_head_us=c.head_us_start,
        n_chunks=p.n_chunks + 1,
        n_missing_chunks=p.n_missing_chunks + (c.seq - p.last_seq - 1),
        n_dropped_frames=p.n_dropped_frames + c.dropped,
        revision=p.revision + 1,
        last_chunk_at=c.received_at,
        state="open",
        closed_reason=None,
    )
    return _with_times(_adopt_server_time(seg, c))


def close(seg: Seg, reason: str) -> Seg:
    return replace(seg, state="closed", closed_reason=reason)


@dataclass(frozen=True, slots=True)
class Placement:
    """묶음 하나를 넣은 결과. store가 이대로 씁니다."""

    kind: Literal["inside", "append", "new"]
    seg: Seg  # 바뀐(또는 새) 구간
    closed_prev: Seg | None = None  # 나눔 때문에 닫은 P
    warnings: tuple[str, ...] = ()


def place(c: ChunkIn, p: Seg | None, p_tags: dict[Side, SegTag], s: Settings) -> Placement:
    """SG-3. p = 같은 (장치, 부팅)에서 first_seq ≤ c.seq인 구간 중 first_seq가 가장 큰 것.

    새 구간의 installation_id는 부르는 쪽이 채웁니다(SG-3 표).
    """
    if p is not None and c.seq <= p.last_seq:
        warnings: list[str] = []
        if c.geometry_hash != p.geometry_hash:
            warnings.append("inside_geometry_differs")
        _, conflicts = tag_changes(c, p_tags)
        if conflicts:
            warnings.append("inside_tag_differs:" + ",".join(conflicts))
        return Placement("inside", add_inside(p, c), warnings=tuple(warnings))
    if p is not None:
        reason = split_reason(c, p, p_tags, s)
        if reason is None:
            return Placement("append", append_after(p, c))
        warnings = ("head_clock_backwards",) if c.head_us_start <= p.last_head_us else ()
        closed = close(p, reason) if p.state == "open" else None
        return Placement("new", new_segment(c), closed_prev=closed, warnings=warnings)
    return Placement("new", new_segment(c))


def is_silent(seg_last_chunk_at: datetime, now: datetime, s: Settings) -> bool:
    return seg_last_chunk_at < now - timedelta(seconds=s.silence_s)


# ---------------------------------------------------------------- 앵커 배치 (SG-8)


@dataclass(frozen=True, slots=True)
class InstallationIn:
    device_id: str
    geometry_hash: int
    geometry_id: str
    source: str
    surveyed: bool
    anchors: list[dict]
    seen_at: datetime


def installation_from_system(c: ChunkIn) -> InstallationIn | None:
    """system 블록 → 배치. 좌표는 블록의 anchors, surveyed는 flags bit0, source는 geometry_source."""
    if c.system_block is None:
        return None
    d = wire.decode_system(c.system_block)
    anchors = [{"id": i, "xyz_m": a[:3], "bias_m": a[3]} for i, a in enumerate(d["anchors"])]  # 칸마다 [x, y, z, bias]
    return InstallationIn(
        device_id=c.device_id,
        geometry_hash=c.geometry_hash,
        geometry_id=d["geometry_id"],
        source=SOURCE[d["geometry_source"]],
        surveyed=bool(d["flags"] & 1),
        anchors=anchors,
        seen_at=c.received_at,
    )
