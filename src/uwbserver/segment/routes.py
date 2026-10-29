"""segment의 앱 API (C3, 8절): 구간 목록·상세, 재분석 요청."""

from fastapi import APIRouter, HTTPException, Query

from uwbserver.pipeline import clock, jobs
from uwbserver.registry.access import AdminUser, Conn, PageParams, SpanParams, not_found
from uwbserver.segment import store

app_router = APIRouter(prefix="/v1", tags=["segments"])


@app_router.get("/segments")
def list_segments(_: AdminUser, conn: Conn, span: SpanParams, p: PageParams, device: str | None = Query(None)) -> list[dict]:
    return store.list_segments(conn, device, span.start, span.end, p.limit, p.before_id)


@app_router.get("/segments/{segment_id}")
def get_segment(segment_id: int, _: AdminUser, conn: Conn) -> dict:
    """구간 상세: Tag, 배치, 빈자리, run 목록."""
    seg = store.segment_detail(conn, segment_id)
    if seg is None:
        raise not_found()
    seg["tags"] = store.tag_rows(conn, segment_id)
    seg["runs"] = store.run_rows(conn, segment_id)
    return seg


@app_router.post("/segments/{segment_id}/reanalyze", status_code=202)
def reanalyze(segment_id: int, _: AdminUser, conn: Conn) -> dict:
    """AN-9: analysis 작업을 넣을 뿐. raw도 archive도 없으면 409. 이미 대기 중이면 queued = false."""
    with conn.transaction():
        if store.get(conn, segment_id) is None:
            raise not_found()
        if not store.has_input(conn, segment_id):
            raise HTTPException(409, "no raw or archive input for this segment")
        queued = jobs.enqueue_analysis(conn, segment_id, now=clock.now())
    return {"segment_id": segment_id, "queued": queued}
