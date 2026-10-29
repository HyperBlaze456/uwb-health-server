"""PL-2 작업 큐, PL-3 읽기 위치."""

from datetime import timedelta

import pytest

from uwbserver.pipeline import clock, jobs

pytestmark = pytest.mark.db


def _core(env):
    c = env.conn("core")
    c.autocommit = True
    return c


def test_one_queued_per_target(env):
    c = _core(env)
    now = clock.now()
    assert jobs.enqueue_analysis(c, 7, now=now)
    assert not jobs.enqueue_analysis(c, 7, now=now)
    job = jobs.claim(c, ("analysis",), worker="w", now=now)
    assert job and job.segment_id == 7
    assert not jobs.enqueue_analysis(c, 7, now=now)  # running도 하나로 셈
    jobs.finish(c, job.id, now=now)
    assert jobs.enqueue_analysis(c, 7, now=now)


def test_backoff_then_failed(env):
    c = _core(env)
    now = clock.now()
    jobs.enqueue_analysis(c, 1, now=now)
    waits = []
    for k in range(1, 5):
        job = jobs.claim(c, ("analysis",), worker="w", now=now)
        assert job is not None, k
        state = jobs.retry_or_fail(c, job, f"boom{k}", now=now)
        row = env.one("SELECT * FROM pipeline.job WHERE id = %s", (job.id,))
        assert row["attempts"] == k
        if k <= 3:
            assert state == "queued"
            waits.append(row["run_after"] - now)
            assert jobs.claim(c, ("analysis",), worker="w", now=now) is None  # 아직 이름
            now = row["run_after"]
        else:
            assert state == "failed"
    assert waits == [timedelta(minutes=2), timedelta(minutes=4), timedelta(minutes=8)]


def test_revive_stale_counts_as_failure(env):
    c = _core(env)
    now = clock.now()
    jobs.enqueue_archive(c, 3, now=now)
    job = jobs.claim(c, ("archive",), worker="w", now=now)
    assert jobs.revive_stale(c, now=now + timedelta(seconds=899), stale_after_s=900) == 0
    assert jobs.revive_stale(c, now=now + timedelta(seconds=901), stale_after_s=900) == 1
    row = env.one("SELECT state, attempts, last_error FROM pipeline.job WHERE id = %s", (job.id,))
    assert row == {"state": "queued", "attempts": 1, "last_error": "stale_running"}


def test_defer_keeps_attempts(env):
    c = _core(env)
    now = clock.now()
    jobs.enqueue_analysis(c, 2, now=now)
    job = jobs.claim(c, ("analysis",), worker="w", now=now)
    jobs.defer(c, job.id, until=now + timedelta(minutes=10), note="waiting_installation")
    row = env.one("SELECT state, attempts, last_error FROM pipeline.job WHERE id = %s", (job.id,))
    assert row == {"state": "queued", "attempts": 0, "last_error": "waiting_installation"}


def test_cleanup_only_old_done(env):
    """30일 지난 done만 지우되, 구간마다 가장 최근 analysis 작업은 남김(AR-6이 봄)."""
    c = _core(env)
    now = clock.now()
    # 구간 1: failed 뒤에 done. 구간 2: done 둘. 구간 3: 대기
    jobs.enqueue_analysis(c, 1, now=now)
    jobs.fail(c, jobs.claim(c, ("analysis",), worker="w", now=now).id, "x", now=now)
    for seg in (1, 2, 2):
        jobs.enqueue_analysis(c, seg, now=now)
        jobs.finish(c, jobs.claim(c, ("analysis",), worker="w", now=now).id, now=now)
    jobs.enqueue_analysis(c, 3, now=now)
    jobs.enqueue_archive(c, 9, now=now)
    jobs.finish(c, jobs.claim(c, ("archive",), worker="w", now=now).id, now=now)
    assert jobs.cleanup(c, now=now + timedelta(days=29)) == 0
    assert jobs.cleanup(c, now=now + timedelta(days=31)) == 2  # 구간 2의 옛 done 하나, archive done 하나
    left = env.q("SELECT segment_id, state FROM pipeline.job WHERE kind = 'analysis' ORDER BY id")
    assert left == [
        {"segment_id": 1, "state": "failed"}, {"segment_id": 1, "state": "done"},
        {"segment_id": 2, "state": "done"}, {"segment_id": 3, "state": "queued"},
    ]


def test_cursor(env):
    c = _core(env)
    assert jobs.get_cursor(c) == 0
    jobs.set_cursor(c, 42)
    assert jobs.get_cursor(c) == 42
