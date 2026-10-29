"""앱 API의 로그인 확인과 권한 (AP-2, RG-6). 모든 단계의 app_router가 이것을 씁니다.

- member: 자기 착용자 하나와, 그 착용 기간에 귀속된 구간·걷기 구간만. 남의 것을 요청하면 404(있는지 알리지 않음).
- admin: 전부. admin 전용 엔드포인트를 member가 부르면 403.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException, Query, Request

from uwbserver.pipeline import clock, db
from uwbserver.registry import rules, store

Conn = Annotated[psycopg.Connection, Depends(db.request_conn)]


@dataclass(frozen=True, slots=True)
class User:
    id: int
    email: str
    role: str
    subject_id: int | None
    token_hash: bytes

    @property
    def admin(self) -> bool:
        return self.role == "admin"


def current_user(request: Request, conn: Conn) -> User:
    token = rules.bearer_token(request.headers.get("authorization"))
    if token is None:
        raise HTTPException(401, "login required", headers={"WWW-Authenticate": "Bearer"})
    h = rules.token_hash(token)
    row = store.user_by_token_hash(conn, h, clock.now())
    if row is None:
        raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    return User(row["id"], row["email"], row["role"], row["subject_id"], h)


CurrentUser = Annotated[User, Depends(current_user)]


def admin_user(user: CurrentUser) -> User:
    if not user.admin:
        raise HTTPException(403, "admin only")
    return user


AdminUser = Annotated[User, Depends(admin_user)]


def not_found() -> HTTPException:
    return HTTPException(404, "not found")


def check_subject(user: User, subject_id: int) -> None:
    """본인 착용자가 아니면 404."""
    if not user.admin and user.subject_id != subject_id:
        raise not_found()


def check_device(conn: psycopg.Connection, user: User, device_id: str) -> None:
    """member는 자기 착용자가 찬 적 있는 장치만."""
    if user.admin:
        if store.device_by_id(conn, device_id) is None:
            raise not_found()
        return
    if user.subject_id is None or device_id not in store.device_ids_worn_by(conn, user.subject_id):
        raise not_found()


@dataclass(frozen=True, slots=True)
class Page:
    """목록 나누기(8절): ?limit=(기본 100, 최대 1000)&before_id="""

    limit: int
    before_id: int | None


def page(
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> Page:
    return Page(limit, before_id)


PageParams = Annotated[Page, Depends(page)]


@dataclass(frozen=True, slots=True)
class Span:
    start: datetime
    end: datetime


def span_params(default_days: int = 30):
    """?from=&to= (UTC ISO 8601). 없으면 [지금 − default_days, 지금 + 1일)."""

    def dep(
        from_: Annotated[datetime | None, Query(alias="from")] = None,
        to: Annotated[datetime | None, Query()] = None,
    ) -> Span:
        now = clock.now()
        start = from_ or now - timedelta(days=default_days)
        end = to or now + timedelta(days=1)
        if start.tzinfo is None or end.tzinfo is None:
            raise HTTPException(422, "from/to need a UTC offset (e.g. 2026-10-26T00:00:00Z)")
        if end <= start:
            raise HTTPException(422, "to must be after from")
        return Span(start, end)

    return dep


SpanParams = Annotated[Span, Depends(span_params())]
