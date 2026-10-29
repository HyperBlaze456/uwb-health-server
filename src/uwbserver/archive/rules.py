"""원본 보관과 삭제의 규칙 (AR-2, AR-3, AR-6). 순수 계산.

- 보관 범위: 걷기 구간 ± ARCHIVE_PAD_S를 seq 범위로 바꾸고, 겹치거나 맞닿으면 합치고, 이미 보관한 seq는 뺌.
- 30일 삭제: raw 파일 하나는 그 안의 모든 묶음이 조건을 만족할 때만 지움.

나중에 AI 모델이 확정되어 삭제를 켤 때(PURGE_ENABLED) 바뀌는 것은 이 파일의 판단이 아니라 스위치 하나뿐입니다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

SECOND_US = 1_000_000


@dataclass(frozen=True, slots=True, order=True)
class SeqRange:
    first: int
    last: int


# ---------------------------------------------------------------- 보관 범위 (AR-2, AR-3)


def bout_windows(bouts: list[tuple[int, int]], pad_s: int) -> list[tuple[int, int]]:
    """걷기 구간 (start_us, end_us) → [start − pad, end + pad] (UTC µs)."""
    pad = pad_s * SECOND_US
    return [(s - pad, e + pad) for s, e in bouts]


def overlaps(chunk_start_us: int, lo_us: int, hi_us: int) -> bool:
    """묶음 시간 [start, start + 1초)가 [lo, hi]와 겹치는가."""
    return chunk_start_us <= hi_us and chunk_start_us + SECOND_US > lo_us


def ranges_for_bouts(chunks: list[tuple[int, int]], windows: list[tuple[int, int]]) -> list[SeqRange]:
    """AR-2: 창마다 겹치는 묶음의 최소·최대 seq. chunks = (seq, 묶음 시작 UTC µs)."""
    out: list[SeqRange] = []
    for lo, hi in windows:
        seqs = [seq for seq, start in chunks if overlaps(start, lo, hi)]
        if seqs:
            out.append(SeqRange(min(seqs), max(seqs)))
    return merge(out)


def merge(ranges: list[SeqRange]) -> list[SeqRange]:
    """겹치거나 맞닿는(다음 first = last + 1) 범위를 합침."""
    out: list[SeqRange] = []
    for r in sorted(ranges):
        if out and r.first <= out[-1].last + 1:
            out[-1] = SeqRange(out[-1].first, max(out[-1].last, r.last))
        else:
            out.append(r)
    return out


def subtract(ranges: list[SeqRange], covered: list[SeqRange]) -> list[SeqRange]:
    """AR-3: 이미 archive.range가 덮는 seq를 뺌."""
    out: list[SeqRange] = []
    cov = merge(covered)
    for r in merge(ranges):
        pieces = [r]
        for c in cov:
            nxt: list[SeqRange] = []
            for p in pieces:
                if c.last < p.first or c.first > p.last:
                    nxt.append(p)
                    continue
                if p.first < c.first:
                    nxt.append(SeqRange(p.first, c.first - 1))
                if p.last > c.last:
                    nxt.append(SeqRange(c.last + 1, p.last))
            pieces = nxt
        out.extend(pieces)
    return sorted(out)


def trim_to_present(ranges: list[SeqRange], present: list[int]) -> list[tuple[SeqRange, list[int]]]:
    """raw에 남아 있는 seq로 범위 양 끝을 좁힘(AR-3: 이미 지워진 seq는 뺌). 빈 범위는 버림.

    돌려주는 값: (좁힌 범위, 그 안의 seq들). 원래 없던 seq(빈자리)는 파일에도 없음.
    """
    present = sorted(present)
    out: list[tuple[SeqRange, list[int]]] = []
    for r in ranges:
        seqs = [s for s in present if r.first <= s <= r.last]
        if seqs:
            out.append((SeqRange(seqs[0], seqs[-1]), seqs))
    return out


# ---------------------------------------------------------------- 30일 삭제 (AR-6)


@dataclass(frozen=True, slots=True)
class ChunkState:
    id: int
    framed: bool
    segment_id: int | None  # 프레임 있는 묶음이 속한 구간


@dataclass(frozen=True, slots=True)
class SegmentState:
    id: int
    state: str
    n_runs: int
    n_runs_uncut: int  # archive.cut이 없는 run 수
    analysis_pending: bool  # 이 구간의 analysis 작업이 queued·running
    archive_pending: bool  # 이 구간 run들의 archive 작업이 queued·running
    last_analysis_state: str | None  # 가장 최근(id가 가장 큰) analysis 작업의 상태


def segment_settled(s: SegmentState) -> str | None:
    """구간의 원본을 지워도 되는가. 안 되면 이유."""
    if s.state != "closed":
        return "segment_open"
    if s.n_runs == 0:
        return "no_run"
    if s.n_runs_uncut:
        return "run_not_archived"
    if s.analysis_pending:
        return "analysis_pending"
    if s.archive_pending:
        return "archive_pending"
    if s.last_analysis_state == "failed":
        return "analysis_failed"
    return None


@dataclass(frozen=True, slots=True)
class FileVerdict:
    deletable: bool
    reason: str | None = None  # 지우지 않는 첫 이유
    orphan: bool = False  # 프레임 있는 묶음이 어느 구간에도 없음(버그 신호, 오류 로그)


def file_verdict(chunks: list[ChunkState], segments: dict[int, SegmentState], cursor: int) -> FileVerdict:
    """AR-6: raw 파일 하나를 지워도 되는가."""
    for c in chunks:
        if c.id > cursor:
            return FileVerdict(False, "not_segmented")
        if not c.framed:
            continue
        if c.segment_id is None:
            return FileVerdict(False, "chunk_without_segment", orphan=True)
        reason = segment_settled(segments[c.segment_id])
        if reason:
            return FileVerdict(False, reason)
    return FileVerdict(True)


def raw_file_expired(hour: datetime, now: datetime, retention_days: int) -> bool:
    """수신 hour 파일의 모든 수신이 보존 기간보다 이전인가(hour + 1시간 ≤ now − 보존 기간)."""
    return hour + timedelta(hours=1) <= now - timedelta(days=retention_days)


def rejected_file_expired(day: datetime, now: datetime, retention_days: int) -> bool:
    return day + timedelta(days=1) <= now - timedelta(days=retention_days)
