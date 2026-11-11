"""TL-11 (SM-4): tools/resummarize.py가 지난 날짜의 summary 작업을 넣음."""

from datetime import UTC, date, datetime

import pytest

from tests.summary.conftest import device, subject, wear
from tools import resummarize
from uwbserver.pipeline import clock

pytestmark = pytest.mark.db


def _queued(env) -> list[tuple]:
    rows = env.q("SELECT device_id, date_from, date_to FROM pipeline.job WHERE kind = 'summary' ORDER BY device_id")
    return [(r["device_id"], r["date_from"], r["date_to"]) for r in rows]


def test_enqueues_one_job_per_device_worn_since(env):
    a, b = subject(env, "A"), subject(env, "B")
    d1, d2, d3 = device(env, "head-aabbcc000001"), device(env, "head-aabbcc000002"), device(env, "head-aabbcc000003")
    wear(env, a, d1, datetime(2026, 9, 1, tzinfo=UTC))  # 지금도 착용
    wear(env, b, d2, datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 10, tzinfo=UTC))
    wear(env, b, d3, datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC))  # since 앞에 끝남
    since = date(2026, 10, 1)
    conn = env.conn("core")
    assert resummarize.enqueue(conn, since, None) == (2, 0)
    today = clock.today()
    assert _queued(env) == [(d1, since, today), (d2, since, today)]
    assert resummarize.enqueue(conn, since, None) == (0, 2)  # 이미 대기 중(PL-2)
    assert resummarize.enqueue(conn, since, d1) == (0, 1)


def test_device_option_limits_to_that_device(env):
    a = subject(env, "A")
    d1, d2 = device(env, "head-aabbcc000001"), device(env, "head-aabbcc000002")
    wear(env, a, d1, datetime(2026, 9, 1, tzinfo=UTC))
    wear(env, a, d2, datetime(2026, 9, 1, tzinfo=UTC))
    assert resummarize.enqueue(env.conn("core"), date(2026, 10, 1), d2) == (1, 0)
    assert [q[0] for q in _queued(env)] == [d2]


def test_since_in_the_future_is_an_argument_error(env):
    assert resummarize.main(["--since", "2099-01-01"]) == 2
