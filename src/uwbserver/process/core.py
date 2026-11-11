"""core 프로세스: 5초 루프(segment) + 작업 스레드(analysis, archive, feature) + 매일 삭제(archive.purge).

    python -m uwbserver.process.core
"""

import signal
import sys
import threading
import time
from datetime import UTC, datetime

import httpx
import psycopg

from uwbserver.analysis import gaitfusion
from uwbserver.analysis import run as analysis_run
from uwbserver.archive import run as archive_run
from uwbserver.feature import run as feature_run
from uwbserver.pipeline import clock, db, jobs, log, settings
from uwbserver.segment import run as segment_run

LOOP_S = 5.0
C2_VERSION = 1


class Core:
    def __init__(self, client: gaitfusion.Client | None = None) -> None:
        s = settings.get()
        self.s = s
        self.client = client or gaitfusion.Client(s.gaitfusion_url, s.gaitfusion_timeout_s)
        self.stop = threading.Event()
        self.c2_ready = threading.Event()  # 7.4: /v1/version 확인 전에는 analysis 작업을 가져가지 않음
        self.gaitfusion_version: str | None = None
        self.last_purge_day: str | None = None

    # ------------------------------------------------------------ 5초 루프

    def loop_once(self, conn: psycopg.Connection) -> None:
        now = clock.now()
        with conn.transaction():
            revived = jobs.revive_stale(conn, now=now, stale_after_s=self.s.gaitfusion_timeout_s + 300)
        if revived:
            log.warning("jobs_revived", count=revived)
        stats = segment_run.step(conn)
        if stats.chunks or stats.closed:
            log.info("segment_step", chunks=stats.chunks, created=stats.created, closed=stats.closed)
        self._check_c2()
        self._daily(conn, now)
        self._alive()

    def _check_c2(self) -> None:
        if self.c2_ready.is_set():
            return
        try:
            v = self.client.version()
        except httpx.HTTPError as e:
            log.warning("gaitfusion_version_unreachable", error=str(e))
            return
        if v.get("c2_version") != C2_VERSION:
            log.error("gaitfusion_c2_version_mismatch", got=v)
            return
        self.gaitfusion_version = v.get("gaitfusion_version")
        self.c2_ready.set()
        log.info("gaitfusion_ready", **v)

    def _daily(self, conn: psycopg.Connection, now: datetime) -> None:
        """PURGE_AT_UTC가 지나면 하루 한 번: 30일 삭제(AR-6·7)와 done 작업 정리(PL-2)."""
        at = self.s.purge_at_utc
        today = now.astimezone(UTC).date().isoformat()
        due = now.astimezone(UTC).time() >= at
        if not due or self.last_purge_day == today:
            return
        self.last_purge_day = today
        archive_run.purge(conn)
        with conn.transaction():
            removed = jobs.cleanup(conn, now=now)
        log.info("jobs_cleanup", removed=removed)

    def _alive(self) -> None:
        """PL-6: 마지막 루프 시각."""
        path = self.s.data_root / "core.alive"
        try:
            path.write_text(clock.now().isoformat() + "\n")
        except OSError as e:
            log.warning("core_alive_write_failed", error=str(e))

    # ------------------------------------------------------------ 작업 스레드

    def work_once(self, conn: psycopg.Connection, kinds: tuple[str, ...], worker: str) -> str | None:
        """작업 하나를 가져가 처리. 없으면 None."""
        if "analysis" in kinds and not self.c2_ready.is_set():
            kinds = tuple(k for k in kinds if k != "analysis")
        if not kinds:
            return None
        with conn.transaction():
            job = jobs.claim(conn, kinds, worker=worker, now=clock.now())
        if job is None:
            return None
        if job.kind == "analysis":
            return analysis_run.handle(conn, job, self.client, gaitfusion_version=self.gaitfusion_version)
        if job.kind == "feature":
            return feature_run.handle(conn, job)
        return archive_run.handle(conn, job)

    def _worker(self, kinds: tuple[str, ...], name: str) -> None:
        conn = db.connect("core", retry_s=60.0)
        while not self.stop.is_set():
            try:
                if self.work_once(conn, kinds, name) is None:
                    self.stop.wait(1.0)
            except psycopg.OperationalError as e:
                log.error("worker_db_lost", worker=name, error=str(e))
                conn = _reconnect(conn)
            except Exception as e:  # noqa: BLE001 - 작업 하나의 버그가 스레드를 죽이지 않게
                log.error("worker_crashed", worker=name, error=f"{type(e).__name__}: {e}")
                self.stop.wait(5.0)
        conn.close()

    def run(self) -> None:
        conn = db.connect("core", retry_s=60.0)
        threads = [
            threading.Thread(target=self._worker, args=(("analysis",), f"analysis-{i}"), daemon=True)
            for i in range(self.s.analysis_workers)
        ]
        threads.append(threading.Thread(target=self._worker, args=(("archive",), "archive-0"), daemon=True))
        threads.append(threading.Thread(target=self._worker, args=(("feature",), "feature-0"), daemon=True))
        for t in threads:
            t.start()
        while not self.stop.is_set():
            t0 = time.monotonic()
            try:
                self.loop_once(conn)
            except psycopg.OperationalError as e:
                log.error("core_db_lost", error=str(e))
                conn = _reconnect(conn)
            except Exception as e:  # noqa: BLE001
                log.error("core_loop_crashed", error=f"{type(e).__name__}: {e}")
            self.stop.wait(max(0.0, LOOP_S - (time.monotonic() - t0)))
        for t in threads:
            t.join(timeout=30)
        conn.close()


def _reconnect(conn: psycopg.Connection) -> psycopg.Connection:
    try:
        conn.close()
    except psycopg.Error:
        pass
    return db.connect("core", retry_s=300.0)


def main() -> int:
    log.setup("core")
    core = Core()
    signal.signal(signal.SIGTERM, lambda *_: core.stop.set())
    signal.signal(signal.SIGINT, lambda *_: core.stop.set())
    log.info("core_start", workers=core.s.analysis_workers, purge_enabled=core.s.purge_enabled)
    core.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
