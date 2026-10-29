"""segment 단계 실행: core가 5초마다 부르는 step(conn) (SG-1).

묶음을 id 순으로 읽어 하나씩 구간에 넣고(SG-3–SG-8), 결과와 읽기 위치를 한 트랜잭션에 씁니다(PL-3).
밀린 묶음을 다 처리한 뒤 침묵 구간을 닫습니다(SG-5). 구간을 닫을 때마다 analysis 작업을 넣습니다.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import psycopg

from uwbserver.pipeline import alert, clock, jobs, log, settings
from uwbserver.segment import rules, store
from uwbserver.segment.rules import ChunkIn, Seg, SegTag, Side

BATCH = 1000


@dataclass
class StepStats:
    chunks: int = 0
    created: int = 0
    closed: int = 0
    skipped: int = 0  # 처리하다 실패해 건너뛴 묶음(알림을 보냄)


def rule_settings() -> rules.Settings:
    s = settings.get()
    return rules.Settings(gap_s=s.segment_gap_s, max_s=s.segment_max_s, silence_s=s.segment_silence_s)


class _Batch:
    """한 트랜잭션 안의 읽기 캐시. 쓰기는 바로 DB에 합니다(쓰기를 미루면 boot_changed 같은 SQL 닫기가 어긋남)."""

    def __init__(self, conn: psycopg.Connection, s: rules.Settings, now: datetime, stats: StepStats) -> None:
        self.conn, self.s, self.now, self.stats = conn, s, now, stats
        self.latest: dict[tuple[str, int], Seg | None] = {}  # (장치, 부팅) → first_seq가 가장 큰 구간
        self.tags: dict[int, dict[Side, SegTag]] = {}

    def _latest(self, device_id: str, boot: int) -> Seg | None:
        key = (device_id, boot)
        if key not in self.latest:
            self.latest[key] = store.latest_of_boot(self.conn, device_id, boot)
        return self.latest[key]

    def find_p(self, c: ChunkIn) -> Seg | None:
        latest = self._latest(c.device_id, c.head_boot_id)
        if latest is None or c.seq >= latest.first_seq:
            return latest
        return store.find_p(self.conn, c.device_id, c.head_boot_id, c.seq)

    def tags_of(self, seg_id: int) -> dict[Side, SegTag]:
        if seg_id not in self.tags:
            self.tags[seg_id] = store.tags(self.conn, seg_id)
        return self.tags[seg_id]

    def _remember(self, seg: Seg) -> None:
        key = (seg.device_id, seg.head_boot_id)
        latest = self.latest.get(key)
        if latest is None or seg.first_seq >= latest.first_seq:
            self.latest[key] = seg

    def _close(self, seg_ids: list[int]) -> None:
        for seg_id in seg_ids:
            jobs.enqueue_analysis(self.conn, seg_id, now=self.now)
        self.stats.closed += len(seg_ids)
        # 캐시의 상태도 맞춤
        for key, seg in list(self.latest.items()):
            if seg is not None and seg.id in seg_ids:
                self.latest[key] = store.get(self.conn, seg.id)

    def _write_tags(self, seg_id: int, changes: list[rules.TagChange]) -> dict[Side, SegTag]:
        for ch in changes:
            if ch.action == "insert":
                store.insert_tag(self.conn, seg_id, SegTag(ch.side, ch.tag_mac, ch.tag_boot_id, ch.first_seen_at))
            else:
                store.fill_tag(self.conn, seg_id, ch.side, ch.tag_mac, ch.tag_boot_id)
        merged = rules.apply_tag_changes(self.tags.get(seg_id, {}), changes)
        self.tags[seg_id] = merged
        return merged

    def add(self, c: ChunkIn) -> None:
        # SG-8: 같은 묶음 안에서는 배치를 구간보다 먼저
        inst = rules.installation_from_system(c)
        if inst is not None:
            inst_id = store.upsert_installation(self.conn, inst)
            if store.fill_installation(self.conn, c.device_id, c.geometry_hash, inst_id):
                for key, seg in list(self.latest.items()):
                    if seg is not None and seg.device_id == c.device_id and seg.geometry_hash == c.geometry_hash:
                        self.latest[key] = replace(seg, installation_id=seg.installation_id or inst_id)
        if not c.framed:  # SG-2
            return

        p = self.find_p(c)
        first_of_boot = p is None and self._latest(c.device_id, c.head_boot_id) is None
        p_tags = self.tags_of(p.id) if p is not None else {}
        placed = rules.place(c, p, p_tags, self.s)
        for w in placed.warnings:
            log.warning("segment_warning", warning=w, device=c.device_id, boot=c.head_boot_id, seq=c.seq, chunk_id=c.id)

        if placed.kind == "new":
            if placed.closed_prev is not None:
                store.update_segment(self.conn, placed.closed_prev)
                self._remember(placed.closed_prev)
                self._close([placed.closed_prev.id])
            seg = replace(placed.seg, installation_id=store.installation_id_for(self.conn, c.device_id, c.geometry_hash))
            changes, _ = rules.tag_changes(c, {})
            seg = replace(seg, sides=rules.sides_of(rules.apply_tag_changes({}, changes)))
            seg = replace(seg, id=store.insert_segment(self.conn, seg))
            self.tags[seg.id] = {}
            self._write_tags(seg.id, changes)
            self._remember(seg)
            self.stats.created += 1
            if first_of_boot:  # SG-5 boot_changed
                self._close(store.close_other_boots(self.conn, c.device_id, c.head_boot_id))
        else:
            changes, _ = rules.tag_changes(c, p_tags)
            merged = self._write_tags(p.id, changes) if changes else p_tags
            seg = replace(placed.seg, sides=rules.sides_of(merged))
            store.update_segment(self.conn, seg)
            self._remember(seg)


def _batch(conn: psycopg.Connection, s: rules.Settings, now: datetime, stats: StepStats, *, careful: bool) -> int:
    """묶음 최대 1000개를 한 트랜잭션에. careful이면 묶음마다 savepoint를 두고, 실패한 묶음만 건너뜀."""
    with conn.transaction():
        cursor = jobs.get_cursor(conn, lock=True)
        chunks = store.load_chunks(conn, cursor, BATCH)
        batch = _Batch(conn, s, now, stats)
        for c in chunks:
            if not careful:
                batch.add(c)
                continue
            try:
                with conn.transaction():  # savepoint
                    batch.add(c)
            except psycopg.OperationalError:
                raise
            except Exception as e:  # noqa: BLE001 - 묶음 하나 때문에 모든 장치의 구간 잇기가 멈추지 않게
                batch.latest.clear()
                batch.tags.clear()
                stats.skipped += 1
                # 건너뛴 묶음은 어느 구간에도 들지 않으므로 30일 삭제가 그 raw 파일을 지우지 않음(AR-6)
                alert.notify("segment_chunk_skipped", chunk_id=c.id, device=c.device_id, boot=c.head_boot_id,
                             seq=c.seq, error=f"{type(e).__name__}: {e}")  # fmt: skip
        if chunks:
            jobs.set_cursor(conn, chunks[-1].id)
    return len(chunks)


def step(conn: psycopg.Connection, *, now: datetime | None = None) -> StepStats:
    """밀린 묶음을 모두 처리(1000개씩 한 트랜잭션)한 뒤 침묵 구간을 닫습니다.

    한 묶음이 실패하면 그 1000개를 묶음마다 savepoint를 두고 다시 처리해, 실패한 묶음만 건너뛰고 알립니다.
    """
    s = rule_settings()
    stats = StepStats()
    while True:
        now_ = now or clock.now()
        try:
            n = _batch(conn, s, now_, stats, careful=False)
        except psycopg.OperationalError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("segment_batch_failed", error=f"{type(e).__name__}: {e}")
            n = _batch(conn, s, now_, stats, careful=True)
        stats.chunks += n
        if n < BATCH:
            break
    now_ = now or clock.now()
    with conn.transaction():
        closed = store.close_silent(conn, now_ - timedelta(seconds=s.silence_s))
        for seg_id in closed:
            jobs.enqueue_analysis(conn, seg_id, now=now_)
        stats.closed += len(closed)
    return stats
