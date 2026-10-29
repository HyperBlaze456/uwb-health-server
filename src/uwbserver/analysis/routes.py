"""analysis의 앱 API (C3, 8절): run, 결과 원문, 착용자의 걷기 구간, 궤적."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response

from uwbserver.analysis import rules, store
from uwbserver.pipeline import files
from uwbserver.registry.access import AdminUser, Conn, CurrentUser, PageParams, SpanParams, check_subject, not_found

app_router = APIRouter(prefix="/v1", tags=["analysis"])


@app_router.get("/runs/{run_id}")
def get_run(run_id: int, _: AdminUser, conn: Conn) -> dict:
    """run과 걷기 구간 목록."""
    run = store.run(conn, run_id)
    if run is None:
        raise not_found()
    run["bouts"] = store.bouts_of_run(conn, run_id)
    return run


@app_router.get("/runs/{run_id}/result", response_class=Response)
def get_run_result(run_id: int, _: AdminUser, conn: Conn) -> Response:
    """결과 원문(AN-10): gzip 그대로, Content-Encoding: gzip."""
    run = store.run(conn, run_id)
    if run is None or run["result_path"] is None:
        raise not_found()
    try:
        data = files.resolve(run["result_path"]).read_bytes()
    except FileNotFoundError:
        raise not_found() from None
    return Response(data, media_type="application/json", headers={"Content-Encoding": "gzip"})


@app_router.get("/subjects/{subject_id}/bouts")
def subject_bouts(subject_id: int, user: CurrentUser, conn: Conn, span: SpanParams, p: PageParams) -> list[dict]:
    """현재 run의 걷기 구간 목록(시작, 끝, 발, 모드). 귀속은 SG-9."""
    check_subject(user, subject_id)
    return store.subject_bouts(conn, subject_id, span.start, span.end, p.limit, p.before_id)


@app_router.get("/bouts/{bout_id}/trajectory")
def bout_trajectory(
    bout_id: int,
    user: CurrentUser,
    conn: Conn,
    side: Literal["left", "right"] | None = None,
    rate: int = Query(25, description="25 · 50 · 100 Hz"),
) -> dict:
    """한 발 또는 있는 발 전부의 궤적(AP-4). 해석·압축 해제 없이 필요한 채널만 건너뛰며 읽음.

    응답: {"bout_id", "trajectories": [{side, rate_hz, t0, n, channels: {px: [...], ...}}]}. 값이 NaN이면 null.
    캐시가 지워졌으면 404.
    """
    if rate not in (25, 50, 100):
        raise HTTPException(422, "rate must be 25, 50 or 100")
    bout = store.bout(conn, bout_id)
    if bout is None:
        raise not_found()
    if not user.admin and (bout["subject_id"] is None or bout["subject_id"] != user.subject_id):
        raise not_found()
    rows = [t for t in store.trajectories_of_bout(conn, bout_id) if side is None or t["side"] == side]
    if not rows:
        raise not_found()
    out = []
    for t in rows:
        try:
            data = files.resolve(t["path"]).read_bytes()
        except FileNotFoundError:
            raise not_found() from None
        step = rules.rate_step(t["rate_hz"], rate)
        channels = rules.read_channels(data, step=step, digits=6)
        out.append(
            {
                "side": t["side"],
                "rate_hz": t["rate_hz"] // step,
                "t0": t["t0"],
                "n": rules.stepped_n(t["n"], step),
                "channels": channels,
            }
        )
    return {"bout_id": bout_id, "trajectories": out}

