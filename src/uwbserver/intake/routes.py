"""intake의 HTTP. device_router = C1 받기(IN-1), app_router = C3 중 intake 부분(8절)."""

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from uwbserver.intake import run
from uwbserver.intake import store as intake_store
from uwbserver.pipeline import wire
from uwbserver.registry.access import AdminUser, Conn, CurrentUser, PageParams, SpanParams, check_device
from uwbserver.registry import rules as registry_rules
from uwbserver.registry import store as registry_store

device_router = APIRouter()

OCTET = "application/octet-stream"


def _device_for(pool, token: str):
    with pool.connection() as conn:
        return registry_store.device_by_token_hash(conn, registry_rules.token_hash(token))


def _receive(pool, device, body: bytes) -> run.Reply:
    with pool.connection() as conn:
        return run.receive(conn, device, body)


async def _read_limited(request: Request, limit: int) -> bytes | None:
    """본문을 limit 바이트까지만 읽습니다. 넘으면 None(IN-6 413)."""
    body = bytearray()
    async for part in request.stream():
        body += part
        if len(body) > limit:
            return None
    return bytes(body)


@device_router.post("/d/v1/chunks")
async def post_chunks(request: Request) -> Response:
    pool = request.app.state.pool
    # IN-2: 토큰으로만 장치를 정함. 없거나 폐기되었으면 401, 아무것도 쓰지 않음.
    token = registry_rules.bearer_token(request.headers.get("authorization"))
    device = await run_in_threadpool(_device_for, pool, token) if token else None
    if device is None:
        return Response(status_code=401)
    # IN-6 413: Content-Length가 한도를 넘으면 읽기 전에, 없으면 한도 + 1 바이트까지 읽어 봄.
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > wire.MAX_BODY_BYTES:
        return Response(status_code=413)
    body = await _read_limited(request, wire.MAX_BODY_BYTES)
    if body is None:
        return Response(status_code=413)
    reply = await run_in_threadpool(_receive, pool, device, body)
    request.app.state.replies.record(reply.status)
    if reply.status == 200:
        return Response(content=reply.body, status_code=200, media_type=OCTET)
    return Response(content=reply.body, status_code=reply.status, media_type="text/plain")


# ---------------------------------------------------------------- 앱 API (C3, 8절)

app_router = APIRouter(prefix="/v1", tags=["devices"])


def _block_out(row: dict, decode) -> dict:
    """AP-3: block 바이트를 wire.md 필드 이름 그대로 풂."""
    return {"at": row["at"], "head_boot_id": row["head_boot_id"], "chunk_seq": row["chunk_seq"], **decode(bytes(row["block"]))}


@app_router.get("/devices/{device_id}/health")
def device_health(device_id: str, user: CurrentUser, conn: Conn, span: SpanParams, p: PageParams) -> list[dict]:
    """1분 기록(최근 30일), 풀어서."""
    check_device(conn, user, device_id)
    rows = intake_store.health_rows(conn, device_id, span.start, span.end, p.limit)
    return [_block_out(r, wire.decode_health) for r in rows]


@app_router.get("/devices/{device_id}/surveys")
def device_surveys(device_id: str, _: AdminUser, conn: Conn, p: PageParams) -> list[dict]:
    check_device(conn, _, device_id)
    return [_block_out(r, wire.decode_survey) for r in intake_store.survey_rows(conn, device_id, p.limit, None)]


@app_router.get("/devices/{device_id}/rejected")
def device_rejected(device_id: str, _: AdminUser, conn: Conn, span: SpanParams, p: PageParams) -> list[dict]:
    """거절한 묶음: 코드, 검사 단계, detail."""
    check_device(conn, _, device_id)
    return intake_store.rejected_rows(conn, device_id, span.start, span.end, p.limit, p.before_id)
