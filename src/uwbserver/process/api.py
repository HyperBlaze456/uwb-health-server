"""api 프로세스: 모든 단계의 app_router (C3, 8절). OpenAPI는 /openapi.json, 문서는 /docs (AP-1).

    uvicorn uwbserver.process.api:app --port 8000
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from uwbserver.analysis import routes as analysis_routes
from uwbserver.feature import routes as feature_routes
from uwbserver.intake import routes as intake_routes
from uwbserver.pipeline import db, log, settings
from uwbserver.registry import routes as registry_routes
from uwbserver.segment import routes as segment_routes
from uwbserver.summary import routes as summary_routes


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.setup("api")
        app.state.pool = db.pool("api", max_size=10)
        try:
            yield
        finally:
            app.state.pool.close()

    app = FastAPI(
        title="uwb-health-server app API (C3)",
        version="1",
        description="SPEC.md 8절. 인증은 Authorization: Bearer <로그인 토큰>. 시각은 UTC ISO 8601.",
        lifespan=lifespan,
    )
    for r in (registry_routes, intake_routes, segment_routes, analysis_routes, feature_routes, summary_routes):
        app.include_router(r.app_router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> Response:
        """PL-6: DB 연결과 데이터 디렉토리(읽기 전용으로 붙음) 읽기 가능 여부."""
        try:
            with app.state.pool.connection() as conn:
                conn.execute("SELECT 1")
            ok = os.access(settings.get().data_root, os.R_OK)
        except Exception as e:  # noqa: BLE001
            log.warning("healthz_failed", error=str(e))
            return Response(status_code=503)
        return Response(status_code=200 if ok else 503)

    return app


app = create_app()
