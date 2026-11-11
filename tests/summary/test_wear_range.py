"""SM-1: 착용 기록을 만들거나 고칠 때 다시 계산할 한국 날짜 범위(registry.rules.summary_dates). DB 없는 순수 시험."""

from datetime import UTC, date, datetime

from uwbserver.registry import rules

NOW = datetime(2026, 10, 26, 0, 0, tzinfo=UTC)  # 한국 10-26 09:00


def test_open_ended_wear_ends_today_in_korea():
    assert rules.summary_dates([(datetime(2026, 10, 20, 15, 0, tzinfo=UTC), None)], NOW) == (date(2026, 10, 21), date(2026, 10, 26))


def test_before_and_after_are_merged():
    before = (datetime(2026, 10, 1, 0, 0, tzinfo=UTC), None)
    after = (datetime(2026, 10, 5, 0, 0, tzinfo=UTC), datetime(2026, 10, 10, 0, 0, tzinfo=UTC))
    assert rules.summary_dates([before, after], NOW) == (date(2026, 10, 1), date(2026, 10, 26))


def test_end_is_exclusive_at_korean_midnight():
    """끝 시각은 착용 기간에 들지 않음: 한국 자정에 끝나면 그 전날까지."""
    span = (datetime(2026, 10, 1, 3, 0, tzinfo=UTC), datetime(2026, 10, 4, 15, 0, tzinfo=UTC))  # 10-05 00:00 KST
    assert rules.summary_dates([span], NOW) == (date(2026, 10, 1), date(2026, 10, 4))


def test_future_start_gives_a_one_day_range():
    span = (datetime(2026, 11, 1, 0, 0, tzinfo=UTC), None)
    assert rules.summary_dates([span], NOW) == (date(2026, 11, 1), date(2026, 11, 1))
