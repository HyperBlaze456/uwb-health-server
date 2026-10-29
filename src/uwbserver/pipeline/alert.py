"""알림 (OP-2). 로그에 level=error, event=alert로 남기고, ALERT_WEBHOOK_URL이 있으면 그곳에 POST합니다."""

import collections
import threading

import httpx

from uwbserver.pipeline import clock, log, settings


def notify(reason: str, **fields) -> None:
    log.error("alert", reason=reason, **fields)
    url = settings.get().alert_webhook_url
    if not url:
        return

    def post() -> None:
        try:
            httpx.post(url, json={"reason": reason, **fields}, timeout=10.0)
        except httpx.HTTPError as e:
            log.error("alert_webhook_failed", error=str(e))

    threading.Thread(target=post, daemon=True).start()


class ReplyWatch:
    """받기의 503이 1분에 limit번을 넘으면 알립니다(OP-2). 1분에 한 번까지만 알림."""

    def __init__(self, limit: int = 10, window_s: float = 60.0) -> None:
        self.limit = limit
        self.window_us = int(window_s * 1_000_000)
        self._times: collections.deque[int] = collections.deque()
        self._last_alert_us = -(10**18)
        self._lock = threading.Lock()

    def record(self, status: int) -> None:
        if status != 503:
            return
        now = clock.now_us()
        with self._lock:
            self._times.append(now)
            while self._times and self._times[0] < now - self.window_us:
                self._times.popleft()
            fire = len(self._times) > self.limit and now - self._last_alert_us >= self.window_us
            if fire:
                self._last_alert_us = now
            count = len(self._times)
        if fire:
            notify("intake_503_burst", count_last_minute=count)
