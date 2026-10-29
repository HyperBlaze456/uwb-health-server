"""현재 시각 (PL-4). 서버 코드는 시각을 모두 여기서 얻습니다.

SQL의 now()도 쓰지 않습니다. 시험이 시계를 바꿔 끼워 60초 침묵, 30일 삭제를 흉내 내려면
모든 시각이 이 모듈을 거쳐야 하기 때문입니다.
"""

import threading
import time
from datetime import UTC, datetime, timedelta


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
