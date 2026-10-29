"""M2 구간 (SG). SPEC 11절 M2 시험 1–6. 가짜 Head 시나리오를 시계대로 넣고 5초마다 segment를 돌림."""

import time
from datetime import timedelta

import pytest

from tests.support import Feeder
from tools import fake_head
from uwbserver.pipeline import clock
from uwbserver.pipeline import jobs as segment_jobs
from uwbserver.segment import run as segment_run

pytestmark = pytest.mark.db
MAC = fake_head.TEST_DEVICE_MAC

SEG_SQL = """
SELECT id, head_boot_id, first_seq, last_seq, n_chunks, n_missing_chunks, n_dropped_frames, state, closed_reason,
       sides, installation_id, time_source, start_at, end_at, geometry_hash, revision
FROM segment.segment ORDER BY head_boot_id <> (SELECT head_boot_id FROM segment.segment ORDER BY id LIMIT 1), first_seq
"""


def feed(env, device, name, *, settle_s=120, **opts):
    f = Feeder(env, {MAC: device})
    f.send(fake_head.scenario(name, **opts))
    f.idle(settle_s)  # 침묵 60초가 지나 마지막 구간이 닫히게
    return f


def segs(env):
    return env.q("SELECT * FROM segment.segment ORDER BY id")


def tags(env, seg_id):
    return env.q("SELECT side, tag_mac::text AS tag_mac, tag_boot_id FROM segment.segment_tag WHERE segment_id = %s ORDER BY side", (seg_id,))


@pytest.mark.slow
def test_normal_2h_max_length(env, device):
    feed(env, device, "normal_2h")
    s = segs(env)
    assert [x["closed_reason"] for x in s] == ["max_length", "silence_60s"]
    assert s[0]["n_chunks"] == 3600 and s[0]["n_missing_chunks"] == 0
    assert s[1]["first_seq"] == s[0]["last_seq"] + 1


def test_tag_off_realtime_is_silence(env, device):
    feed(env, device, "tag_off")
    s = segs(env)
    assert [x["closed_reason"] for x in s] == ["silence_60s", "silence_60s"]
    assert s[1]["first_seq"] > s[0]["last_seq"] + 1  # 사이의 묶음은 프레임 없는 health 묶음뿐


def test_tag_off_during_outage_is_gap(env, device):
    feed(env, device, "outage_tag_off")
    s = segs(env)
    assert [x["closed_reason"] for x in s] == ["gap_60s", "silence_60s"]


def test_reboot_closes_old_boot(env, device):
    feed(env, device, "reboot")
    s = segs(env)
    assert len(s) == 2 and s[0]["head_boot_id"] != s[1]["head_boot_id"]
    assert s[0]["closed_reason"] == "boot_changed"


def test_late_old_boot_reopens_only_old(env, device):
    f = Feeder(env, {MAC: device})
    f.send(fake_head.scenario("late_old_boot"))
    f.step()
    s = segs(env)
    boots = {x["head_boot_id"] for x in s}
    assert len(boots) == 2
    first_boot = s[0]["head_boot_id"]
    old = [x for x in s if x["head_boot_id"] == first_boot]
    new = [x for x in s if x["head_boot_id"] != first_boot]
    assert all(x["state"] == "open" for x in new), "새 부팅의 구간은 열린 채"
    assert old[-1]["revision"] > 0  # 늦은 묶음이 더해짐
    f.idle(120)
    assert all(x["state"] == "closed" for x in segs(env))


def test_tag_swap(env, device):
    feed(env, device, "tag_swap")
    s = segs(env)
    assert [x["closed_reason"] for x in s] == ["tag_changed", "silence_60s"]
    assert tags(env, s[0]["id"])[0]["tag_mac"] != tags(env, s[1]["id"])[0]["tag_mac"]


def test_t1_appears(env, device):
    feed(env, device, "t1_appears")
    s = segs(env)
    assert len(s) == 1 and s[0]["sides"] == "both"
    t = tags(env, s[0]["id"])
    assert [x["side"] for x in t] == ["left", "right"] and all(x["tag_mac"] for x in t)


def test_geometry_change(env, device):
    feed(env, device, "geometry_change")
    s = segs(env)
    assert [x["closed_reason"] for x in s] == ["geometry_changed", "silence_60s"]
    assert s[1]["installation_id"] is not None, "새 구간은 만들 때부터 배치가 있음"
    assert s[0]["installation_id"] != s[1]["installation_id"]
    assert env.scalar("SELECT count(*) FROM segment.installation") == 2


def test_overflow_counts_missing(env, device):
    feed(env, device, "overflow")
    s = segs(env)
    keys = env.q("SELECT chunk_seq FROM intake.chunk ORDER BY chunk_seq")
    seqs = [k["chunk_seq"] for k in keys]
    framed_missing = sum(x["n_missing_chunks"] for x in s)
    assert framed_missing > 0
    assert sum(x["n_dropped_frames"] for x in s) > 0
    assert sum(x["n_chunks"] for x in s) <= len(seqs)


def test_frameless_only_no_segment(env, device):
    feed(env, device, "frameless_only", duration_s=300)
    assert env.scalar("SELECT count(*) FROM intake.chunk") > 0
    assert segs(env) == []


def test_outage_reopens_and_queues_one_analysis(env, device):
    """M2-2: 침묵으로 닫혔다 다시 열리고, 최종으로 닫힌 뒤 analysis 작업이 정확히 1개 대기."""
    f = feed(env, device, "outage")
    s = segs(env)
    assert len(s) == 1 and s[0]["state"] == "closed" and s[0]["revision"] > 0
    jobs_ = env.q("SELECT segment_id, state FROM pipeline.job WHERE kind = 'analysis'")
    assert jobs_ == [{"segment_id": s[0]["id"], "state": "queued"}]
    assert f.replies and all(r.status == 200 for r in f.replies)


BOUNDARY = (
    "device_id", "head_boot_id", "geometry_hash", "installation_id", "sides", "first_seq", "last_seq", "n_chunks",
    "n_missing_chunks", "n_dropped_frames", "first_head_us", "last_head_us", "utc_offset_us", "time_source",
    "start_at", "end_at", "revision",
)  # fmt: skip


@pytest.mark.parametrize("name", ["reboot", "tag_swap", "geometry_change", "outage_tag_off", "t1_appears"])
def test_determinism(env, device, name):
    """M2-3 (SG-10): 같은 묶음을 다시 처리하면 구간 표가 같음(id·열림/닫힘 제외)."""
    feed(env, device, name)
    cols = ", ".join(BOUNDARY)
    before = env.q(f"SELECT {cols} FROM segment.segment ORDER BY head_boot_id, first_seq")
    before_tags = env.q("SELECT s.first_seq, t.side, t.tag_mac::text, t.tag_boot_id FROM segment.segment_tag t JOIN segment.segment s ON s.id = t.segment_id ORDER BY 1, 2")
    env.q("TRUNCATE segment.segment_tag, segment.segment, segment.installation, pipeline.job RESTART IDENTITY CASCADE")
    env.q("UPDATE pipeline.cursor SET last_id = 0")
    segment_run.step(env.conn("core"))
    assert env.q(f"SELECT {cols} FROM segment.segment ORDER BY head_boot_id, first_seq") == before
    assert env.q("SELECT s.first_seq, t.side, t.tag_mac::text, t.tag_boot_id FROM segment.segment_tag t JOIN segment.segment s ON s.id = t.segment_id ORDER BY 1, 2") == before_tags


def test_installation_from_system_blocks(env, device):
    """M2-4 (SG-8): system 블록마다 행이 생기거나 last_seen_at이 바뀌고, 비어 있던 installation_id가 채워짐."""
    reqs = fake_head.scenario("normal", duration_s=1300)  # system 블록: 부팅, 10분, 20분
    f = Feeder(env, {MAC: device})
    f.send(reqs[:30])
    f.step()
    inst = env.one("SELECT id, first_seen_at, last_seen_at, surveyed, anchors FROM segment.installation")
    assert len(inst["anchors"]) == 4 and inst["anchors"][1]["xyz_m"][0] == pytest.approx(4.1)
    f.send(reqs[30:])
    f.step()
    later = env.one("SELECT id, first_seen_at, last_seen_at FROM segment.installation")
    assert later["id"] == inst["id"] and later["last_seen_at"] > inst["last_seen_at"]
    # 배치를 아직 못 본 구간 → 블록이 오면 채움
    env.q("UPDATE segment.segment SET installation_id = NULL")
    env.q("DELETE FROM segment.installation")
    env.q("UPDATE pipeline.cursor SET last_id = 0")
    env.q("TRUNCATE segment.segment_tag, segment.segment, pipeline.job RESTART IDENTITY CASCADE")
    sys_rows = env.q("SELECT * FROM intake.system ORDER BY chunk_seq")
    first_sys = sys_rows[0]["chunk_seq"]
    env.q("DELETE FROM intake.system WHERE chunk_seq = %s", (first_sys,))
    segment_run.step(env.conn("core"))
    assert env.scalar("SELECT count(*) FROM segment.segment WHERE installation_id IS NULL") == 0


def test_utc_late_switches_to_server(env, device):
    """M2-5 (SG-7)."""
    f = Feeder(env, {MAC: device})
    reqs = fake_head.scenario("utc_late")
    f.send(reqs)
    f.step()
    s = segs(env)
    assert s[0]["time_source"] == "server"
    first = env.one(
        "SELECT head_us_start, utc_us_start FROM intake.chunk WHERE flags & 1 = 1 ORDER BY id LIMIT 1"
    )
    offset = first["utc_us_start"] - first["head_us_start"]
    assert s[0]["utc_offset_us"] == offset
    assert s[0]["start_at"] == clock.from_us(s[0]["first_head_us"] + offset)


@pytest.mark.slow
def test_speed_100k_chunks(env, device):
    """M2-6: 묶음 10만 개를 묶음당 0.5 ms 이하로. 행은 SQL로 바로 만듦(segment는 raw를 읽지 않음)."""
    n = 100_000
    env.q(
        """
        INSERT INTO intake.chunk (device_id, head_boot_id, chunk_seq, received_at, head_us_start, utc_us_start,
            geometry_hash, flags, n_range, n_imu_frames, dropped_range, dropped_imu_frames, status_mask, tag_mask,
            tag0_mac, tag0_boot_id, raw_path, raw_offset, raw_len)
        SELECT %s, 1, g, %s::timestamptz + g * interval '1 second', 5000000 + g * 1000000::bigint,
               %s::bigint + g * 1000000::bigint, 7, 1, 95, 10, 0, 0, 0, 1, 'aa:00:00:00:00:01', 3,
               '/data/raw/x/00000001/2026-10-26T00.bin', 0, 4792
        FROM generate_series(0, %s - 1) g
        """,
        (device.device.id, clock.now(), clock.to_us(clock.now()), n),
    )
    t0 = time.perf_counter()
    stats = segment_run.step(env.conn("core"), now=clock.now() + timedelta(days=2))
    per_ms = (time.perf_counter() - t0) * 1000 / n
    assert stats.chunks == n
    print(f"segment: {per_ms:.3f} ms/chunk")
    assert per_ms <= 0.5


def test_feeder_smoke(env, device):
    f = Feeder(env, {MAC: device})
    f.send(fake_head.scenario("normal", duration_s=20))
    f.step()
    assert env.scalar("SELECT count(*) FROM segment.segment") == 1
    assert segment_jobs.get_cursor(env.conn("core")) == env.scalar("SELECT max(id) FROM intake.chunk")


def test_bad_chunk_is_skipped_not_blocking(env, device, capsys):
    """검토 8: 처리하다 예외가 나는 묶음 하나가 모든 장치의 구간 잇기를 멈추면 안 됨. 그 묶음만 건너뛰고 알림."""
    f = Feeder(env, {MAC: device}, step_every_s=None)
    f.send(fake_head.scenario("normal", duration_s=30))
    # seq가 크게 뛴 묶음: n_missing_chunks(int4)가 넘쳐 DB 오류가 남
    env.q("UPDATE intake.chunk SET chunk_seq = 4000000000 WHERE chunk_seq = 20")
    stats = f.step()
    assert stats.skipped == 1 and stats.chunks == env.scalar("SELECT count(*) FROM intake.chunk")
    assert segment_jobs.get_cursor(env.conn("core")) == env.scalar("SELECT max(id) FROM intake.chunk")
    assert env.scalar("SELECT sum(n_chunks) FROM segment.segment") == stats.chunks - 1
    assert '"segment_chunk_skipped"' in capsys.readouterr().out
