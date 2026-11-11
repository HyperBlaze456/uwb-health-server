"""PL-2 작업 큐, PL-3 읽기 위치."""

from datetime import date, timedelta

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


def test_summary_one_queued_but_another_while_running(env):
    """SM-1: 같은 대상(장치, 날짜 범위)의 대기 작업은 하나. 도는 중에는 하나 더 대기할 수 있음."""
    c = _core(env)
    now = clock.now()
    d1, d2 = date(2026, 10, 26), date(2026, 10, 27)
    assert jobs.enqueue_summary(c, "head-a", d1, d2, now=now)
    assert not jobs.enqueue_summary(c, "head-a", d1, d2, now=now)
    assert jobs.enqueue_summary(c, "head-a", d1, d1, now=now)  # 다른 범위는 다른 대상
    assert jobs.enqueue_summary(c, "head-b", d1, d2, now=now)
    job = jobs.claim(c, ("summary",), worker="w", now=now)
    assert job is not None and job.kind == "summary"
    assert (job.device_id, job.date_from, job.date_to) == ("head-a", d1, d2)
    assert job.segment_id is None and job.run_id is None
    assert jobs.enqueue_summary(c, "head-a", d1, d2, now=now)  # 도는 중이면 하나 더
    assert not jobs.enqueue_summary(c, "head-a", d1, d2, now=now)


def test_summary_retry_with_queued_twin_is_closed(env):
    """SM-1의 queued만 고유한 색인: 도는 summary 작업이 실패해 대기로 돌아가려는데 같은 대상이 이미 대기 중이면,
    고유 색인을 어기지 않고 끝냄(대기 중인 것이 같은 일을 다시 함). 되살리기(revive_stale)도 같은 길."""
    c = _core(env)
    now = clock.now()
    d = date(2026, 10, 26)
    jobs.enqueue_summary(c, "head-a", d, d, now=now)
    first = jobs.claim(c, ("summary",), worker="w", now=now)
    jobs.enqueue_summary(c, "head-a", d, d, now=now)
    assert jobs.retry_or_fail(c, first, "boom", now=now) == "superseded"
    row = env.one("SELECT state, last_error FROM pipeline.job WHERE id = %s", (first.id,))
    assert row["state"] == "done" and "boom" in row["last_error"]

    second = jobs.claim(c, ("summary",), worker="w", now=now)
    jobs.enqueue_summary(c, "head-a", d, d, now=now)
    later = now + timedelta(hours=1)
    assert jobs.revive_stale(c, now=later, stale_after_s=60) == 1
    assert env.one("SELECT state FROM pipeline.job WHERE id = %s", (second.id,))["state"] == "done"
    # 쌍둥이가 없으면 보통의 재시도
    third = jobs.claim(c, ("summary",), worker="w", now=later)
    assert jobs.retry_or_fail(c, third, "boom", now=later) == "queued"


def test_api_may_enqueue_summary(env):
    """4.2: api_rw는 pipeline.job INSERT(착용 기록을 고칠 때 SM-1)."""
    c = env.conn("api")
    with c.transaction():
        assert jobs.enqueue_summary(c, "head-a", date(2026, 10, 26), date(2026, 10, 26), now=clock.now())


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
