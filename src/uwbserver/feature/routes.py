"""feature의 앱 API (14.8절): 걷기 구간 하나의 줄. 값은 지표 id로 옮긴 것(FT-4)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from uwbserver.feature import store
from uwbserver.pipeline import metrics
from uwbserver.registry.access import Conn, CurrentUser, not_found

app_router = APIRouter(prefix="/v1", tags=["feature"])


def catalog() -> metrics.Catalog:
    """지표 목록. 요청마다 파일을 읽으므로 목록을 고치면 다시 시작하지 않아도 다음 요청부터 그대로 읽힘(MC-4)."""
    return metrics.load()


Catalog = Annotated[metrics.Catalog, Depends(catalog)]


@app_router.get("/bouts/{bout_id}/records")
def bout_records(
    bout_id: int,
    user: CurrentUser,
    conn: Conn,
    cat: Catalog,
    kind: str = Query("stride", description="줄의 종류(지표 목록 [[table]]의 level): stride · step · turn · window · event"),
) -> dict:
    """걷기 구간 하나의 줄(FT-4). 줄마다 {idx, side, start_at, end_at, values: {지표 id: 값}}.

    values는 지표 목록으로 풀이한 것: 이름 옮기기, 단위 환산, 형과 범위가 틀린 값과 null은 빠짐. 형식은 그 run을 풀 때
    응답이 밝힌 것(format). 그 run이 아직 풀리지 않았으면 unpacked = false, records = []. 남의 걷기 구간은 404.
    """
    table = cat.table_by_level(kind)
    if table is None:
        raise HTTPException(422, f"unknown kind {kind!r}; one of {[t.level for t in cat.tables]}")
    bout = store.bout(conn, bout_id)
    if bout is None:
        raise not_found()
    if not user.admin and (bout["subject_id"] is None or bout["subject_id"] != user.subject_id):
        raise not_found()
    out = {"bout_id": bout_id, "run_id": bout["run_id"], "kind": kind, "unpacked": bout["formats"] is not None,
           "format": None, "records": []}  # fmt: skip
    if bout["formats"] is None:
        return out
    fmt = metrics.formats_of({"formats": bout["formats"]}, cat)[table.field]
    out["format"] = fmt
    out["records"] = [
        {"idx": r["idx"], "side": r["side"], "start_at": r["start_at"], "end_at": r["end_at"],
         "values": metrics.normalize(cat, kind, fmt, r["v"])}  # fmt: skip
        for r in store.records_of_bout(conn, bout_id, kind)
    ]
    return out
