"""intake 프로세스: C1 받기(device_router) + 시작 때 재색인(IN-9).

    python -m uwbserver.process.intake reindex      # 받기를 열기 전에 한 번
    uvicorn uwbserver.process.intake:app --port 8001
"""

import os
import sys
import threading
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Response

from uwbserver.intake import routes, run
from uwbserver.pipeline import alert, db, log, settings


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.setup("intake")
        app.state.pool = db.pool("intake", max_size=8)
        app.state.replies = alert.ReplyWatch()
        stop = threading.Event()
        watcher = threading.Thread(target=_watch_db_restart, args=(stop,), daemon=True)
        watcher.start()
        try:
            yield
        finally:
            stop.set()
            app.state.pool.close()

    app = FastAPI(title="uwb intake", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(routes.device_router)

    @app.get("/healthz")
    def healthz() -> Response:
        """PL-6: DB 연결과 데이터 디렉토리 쓰기 가능 여부."""
        root = settings.get().data_root
        try:
            with app.state.pool.connection() as conn:
                conn.execute("SELECT 1")
            ok_dirs = all(os.access(root / d, os.W_OK) for d in ("raw", "rejected"))
        except Exception as e:  # noqa: BLE001 - 상태 확인은 어떤 실패든 503
            log.warning("healthz_failed", error=str(e))
            return Response(status_code=503)
        return Response(status_code=200 if ok_dirs else 503)

    return app


def _watch_db_restart(stop: threading.Event, every_s: float = 30.0) -> None:
    """DB만 재시작했으면 재색인(run.reindex_if_db_restarted). DB가 내려가 있는 동안은 다음 차례에 다시 봄."""
    state: dict = {}
    conn = None
    while not stop.wait(0 if conn is None and not state else every_s):
        try:
            if conn is None or conn.closed:
                conn = db.connect("intake")
            run.reindex_if_db_restarted(conn, state)
        except psycopg.Error as e:
            log.warning("db_restart_watch_failed", error=str(e).strip())
            if conn is not None:
                conn.close()
            conn = None
    if conn is not None:
        conn.close()


app = create_app()


def main(argv: list[str]) -> int:
    log.setup("intake")
    if argv[1:] != ["reindex"]:
        print(__doc__)
        return 2
    root = settings.get().data_root
    for d in ("raw", "rejected"):
        (root / d).mkdir(parents=True, exist_ok=True)
    with db.connect("intake", retry_s=60.0) as conn:
        run.startup_reindex(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
