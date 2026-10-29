"""SG-3–SG-7 순수 규칙 단위 시험 (DB 없음)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from uwbserver.pipeline import clock
from uwbserver.segment import rules
from uwbserver.segment.rules import ChunkIn, SegTag

T = datetime(2026, 10, 26, tzinfo=UTC)
S = rules.Settings(gap_s=60, max_s=3600, silence_s=60)
L = ("aa:00:00:00:00:01", 7)
UTC_OFF = clock.to_us(T) - 5_000_000


def chunk(seq, *, boot=1, utc=True, geo=10, tags=None, recv_s=None, head_s=None, dropped=0, n_range=95, n_imu=10):
    head_us = 5_000_000 + (seq if head_s is None else head_s) * 1_000_000
    recv = T + timedelta(seconds=(seq if recv_s is None else recv_s) + 1.05)
    return ChunkIn(
        id=seq + 1, device_id="head-x", head_boot_id=boot, seq=seq, received_at=recv,
        head_us_start=head_us, utc_us_start=head_us + UTC_OFF if utc else 0, geometry_hash=geo,
        flags=1 if utc else 0, n_range=n_range, n_imu_frames=n_imu, dropped=dropped,
        tag_mask=1, tags={"left": L} if tags is None else tags,
    )  # fmt: skip


def tags_left(mac=L):
    return {"left": SegTag("left", mac[0], mac[1], T)}


def test_new_segment_times_server():
    seg = rules.new_segment(chunk(0))
    assert (seg.first_seq, seg.last_seq, seg.n_chunks, seg.state, seg.time_source) == (0, 0, 1, "open", "server")
    assert seg.start_at == T and seg.end_at == T + timedelta(seconds=1)


def test_new_segment_times_arrival():
    c = chunk(0, utc=False)
    seg = rules.new_segment(c)
    assert seg.time_source == "arrival"
    assert seg.start_at == c.received_at - timedelta(seconds=1)


def test_append_counts_gap_and_drops():
    p = rules.new_segment(chunk(0))
    pl = rules.place(chunk(5, dropped=3), p, tags_left(), S)
    assert pl.kind == "append"
    assert (pl.seg.last_seq, pl.seg.n_chunks, pl.seg.n_missing_chunks, pl.seg.n_dropped_frames) == (5, 2, 4, 3)
    assert pl.seg.revision == 1 and pl.seg.end_at == T + timedelta(seconds=6)


def test_inside_fills_gap_and_reopens():
    p = rules.append_after(rules.new_segment(chunk(0)), chunk(5))
    p = rules.close(p, "silence_60s")
    pl = rules.place(chunk(3, recv_s=100), p, tags_left(), S)
    assert pl.kind == "inside"
    assert (pl.seg.n_missing_chunks, pl.seg.n_chunks, pl.seg.state, pl.seg.closed_reason) == (3, 3, "open", None)
    assert pl.seg.last_seq == 5


def test_inside_ignores_split_conditions_but_warns():
    p = rules.append_after(rules.new_segment(chunk(0)), chunk(5))
    pl = rules.place(chunk(3, geo=99, tags={"left": ("bb:00:00:00:00:02", 1)}), p, tags_left(), S)
    assert pl.kind == "inside"
    assert {w.split(":")[0] for w in pl.warnings} == {"inside_geometry_differs", "inside_tag_differs"}


def test_split_reasons_in_order():
    p = rules.new_segment(chunk(0))
    gap = chunk(1, head_s=61)
    assert rules.split_reason(gap, p, tags_left(), S) == "gap_60s"
    assert rules.split_reason(chunk(1, head_s=60), p, tags_left(), S) is None  # 1초 길이를 뺀 빈틈 59초
    assert rules.split_reason(chunk(1, head_s=-1), p, tags_left(), S) == "gap_60s"  # 시각이 거꾸로
    both = chunk(1, head_s=61, geo=11)
    assert rules.split_reason(both, p, tags_left(), S) == "gap_60s"
    assert rules.split_reason(chunk(1, geo=11), p, tags_left(), S) == "geometry_changed"
    swap = chunk(1, tags={"left": ("bb:00:00:00:00:02", 1)})
    assert rules.split_reason(swap, p, tags_left(), S) == "tag_changed"
    reboot_tag = chunk(1, tags={"left": (L[0], 8)})
    assert rules.split_reason(reboot_tag, p, tags_left(), S) == "tag_changed"
    # 새 발 자리가 나타나는 것은 나눔이 아님
    assert rules.split_reason(chunk(1, tags={"left": L, "right": ("cc:00:00:00:00:03", 2)}), p, tags_left(), S) is None
    # MAC 없는 자리(거리 프레임만)는 비교하지 않음
    assert rules.split_reason(swap, p, {"left": SegTag("left", None, None, T)}, S) is None
    long_p = rules.append_after(rules.new_segment(chunk(0)), chunk(3599))
    assert rules.split_reason(chunk(3600), long_p, tags_left(), S) == "max_length"
    assert rules.split_reason(chunk(3599 + 1, head_s=3599.5), long_p, tags_left(), S) is None


def test_split_closes_open_p_only():
    p = rules.new_segment(chunk(0))
    pl = rules.place(chunk(1, geo=11), p, tags_left(), S)
    assert pl.kind == "new" and pl.closed_prev.closed_reason == "geometry_changed"
    closed = rules.close(p, "silence_60s")
    pl = rules.place(chunk(1, geo=11), closed, tags_left(), S)
    assert pl.kind == "new" and pl.closed_prev is None


def test_time_switches_to_server():
    c0 = chunk(0, utc=False)
    p = rules.new_segment(c0)
    seg = rules.append_after(p, chunk(1))
    assert seg.time_source == "server" and seg.start_at == T
    again = rules.append_after(seg, replace(chunk(2), utc_us_start=chunk(2).utc_us_start + 999))
    assert again.utc_offset_us == seg.utc_offset_us  # 처음 들어온 bit0 묶음이 기준


def test_tag_changes_insert_fill_conflict():
    c = chunk(1, tags={"left": L, "right": None})
    changes, conflicts = rules.tag_changes(c, {"left": SegTag("left", None, None, T)})
    assert [(ch.side, ch.action) for ch in changes] == [("left", "fill"), ("right", "insert")]
    assert not conflicts
    merged = rules.apply_tag_changes({"left": SegTag("left", None, None, T)}, changes)
    assert rules.sides_of(merged) == "both" and merged["left"].first_seen_at == T
    _, conflicts = rules.tag_changes(chunk(1, tags={"left": ("bb:00:00:00:00:02", 1)}), tags_left())
    assert conflicts == ["left"]


def test_framed():
    assert not chunk(0, n_range=0, n_imu=0).framed
    assert chunk(0, n_range=0, n_imu=1).framed
