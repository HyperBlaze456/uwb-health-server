"""summary의 앱 API (14.8절): 지표 목록, 착용자의 일별 값 추세. 계산하지 않고 읽기만 합니다(SM-5)."""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from uwbserver.pipeline import clock, metrics
from uwbserver.registry import store as registry_store
from uwbserver.registry.access import Conn, CurrentUser, check_subject, not_found
from uwbserver.summary import store

app_router = APIRouter(prefix="/v1", tags=["summary"])

MAX_METRICS = 10
MAX_DAYS = 366  # 날짜 범위(양 끝 포함)의 최대 날 수
METRIC_KEYS = ("id", "level", "label", "unit", "meaning", "trust", "needs", "show")
SIDE_ORDER = {None: 0, "left": 1, "right": 2}


@app_router.get("/metrics")
def list_metrics(_: CurrentUser) -> dict:
    """지표 목록(MC-1): 지표마다 {id, level, label, unit, meaning, trust, needs, show}와 목록의 판(catalog_version).
    앱은 show로 착용자 화면에 보일 지표를 고르고(basic), 이름·단위·믿을 만한 정도를 여기서 받음(SM-5).
    요청마다 파일을 읽으므로 목록을 고치면 다시 시작하지 않아도 다음 요청부터 그대로 보임."""
    cat = metrics.load()
    return {"catalog_version": cat.version, "metrics": [{k: getattr(m, k) for k in METRIC_KEYS} for m in cat.metrics]}


@app_router.get("/subjects/{subject_id}/trend")
def subject_trend(
    subject_id: int,
    user: CurrentUser,
    conn: Conn,
    metric_list: Annotated[str, Query(alias="metrics", description="쉼표로 이은 period.* 지표 id, 최대 10개")],
    first: Annotated[date | None, Query(alias="from", description="한국 날짜. 없으면 to − 365일")] = None,
    last: Annotated[date | None, Query(alias="to", description="한국 날짜(포함). 없으면 오늘")] = None,
) -> list[dict]:
    """착용자의 일별 값(SM-5). 지표마다·발마다 {metric, side, points: [{date, value, n, sufficient}]}, points는 날짜 순.

    값이 있는 지표·발만 나옴(걷기가 없던 날, 값을 내지 못한 날은 points에 없음). side = null은 발 구분 없는 값.
    날짜는 한국 날짜, 범위는 양 끝 포함 최대 366일. 모르는 지표, period가 아닌 지표, 11개 이상, 잘못된 범위는 422.
    남의 착용자는 404.
    """
    check_subject(user, subject_id)
    if registry_store.subject(conn, subject_id) is None:
        raise not_found()
    ids = _metric_ids(metric_list)
    last = last or clock.today()
    first = first or last - timedelta(days=MAX_DAYS - 1)
    if first > last:
        raise HTTPException(422, "from must not be after to")
    if (last - first).days + 1 > MAX_DAYS:
        raise HTTPException(422, f"date range is at most {MAX_DAYS} days")

    series: dict[tuple[str, str | None], list[dict]] = {}
    for r in store.trend(conn, subject_id, ids, first, last):
        series.setdefault((r["metric"], r["side"]), []).append(
            {"date": r["date"], "value": r["value"], "n": r["n"], "sufficient": r["sufficient"]}
        )
    order = {m: k for k, m in enumerate(ids)}
    keys = sorted(series, key=lambda k: (order[k[0]], SIDE_ORDER[k[1]]))
    return [{"metric": m, "side": side, "points": series[(m, side)]} for m, side in keys]


def _metric_ids(text: str) -> list[str]:
    ids = list(dict.fromkeys(x.strip() for x in text.split(",") if x.strip()))
    if not ids:
        raise HTTPException(422, "metrics is empty")
    if len(ids) > MAX_METRICS:
        raise HTTPException(422, f"at most {MAX_METRICS} metrics")
    cat = metrics.load()
    bad = [i for i in ids if (m := cat.metric(i)) is None or m.level != metrics.PERIOD]
    if bad:
        raise HTTPException(422, f"unknown or non-period metrics: {bad}")
    return ids
