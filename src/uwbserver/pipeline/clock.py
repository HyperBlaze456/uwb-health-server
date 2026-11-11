"""현재 시각 (PL-4). 서버 코드는 시각을 모두 여기서 얻습니다.

SQL의 now()도 쓰지 않습니다. 시험이 시계를 바꿔 끼워 60초 침묵, 30일 삭제를 흉내 내려면
모든 시각이 이 모듈을 거쳐야 하기 때문입니다.
"""

import threading
import time
from datetime import UTC, date, datetime, timedelta, timezone


class SystemClock:
    def now_us(self) -> int:
        return time.time_ns() // 1000


class FakeClock:
    """시험용 시계. 스레드에서 같이 읽어도 됩니다."""

    def __init__(self, start: datetime | int) -> None:
        self._us = start if isinstance(start, int) else to_us(start)
        self._lock = threading.Lock()

    def now_us(self) -> int:
        with self._lock:
            return self._us

    def set(self, at: datetime | int) -> None:
        with self._lock:
            self._us = at if isinstance(at, int) else to_us(at)

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._us += round(seconds * 1_000_000)


_clock: SystemClock | FakeClock = SystemClock()


def use(clock: SystemClock | FakeClock | None) -> None:
    global _clock
    _clock = clock or SystemClock()


def now_us() -> int:
    return _clock.now_us()


def now() -> datetime:
    return from_us(_clock.now_us())


EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_us(at: datetime) -> int:
    d = at - EPOCH
    return (d.days * 86_400 + d.seconds) * 1_000_000 + d.microseconds


def from_us(us: int) -> datetime:
    return EPOCH + timedelta(microseconds=us)


# --- 한국 날짜 (SM-2) -------------------------------------------------------------------
# 일별 값의 "하루"는 Asia/Seoul 날짜입니다. 한국은 일광 절약 시간이 없으므로(1988년 이후) UTC+9 고정과 같습니다.
# 시스템의 시간대 자료(이미지에 없을 수 있음)에 기대지 않으려고 고정 offset으로 둡니다. 날짜를 정하는 곳은 여기 하나입니다.
SEOUL = timezone(timedelta(hours=9), "Asia/Seoul")


def local_date(at: datetime) -> date:
    """그 순간의 한국 날짜."""
    return at.astimezone(SEOUL).date()


def day_start(d: date) -> datetime:
    """한국 날짜 d가 시작하는 순간(그날 00:00 KST). 하루는 [day_start(d), day_start(d + 1일))."""
    return datetime(d.year, d.month, d.day, tzinfo=SEOUL)


def today() -> date:
    """지금의 한국 날짜."""
    return local_date(now())
