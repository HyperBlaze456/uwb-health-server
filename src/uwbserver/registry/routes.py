"""registry의 앱 API (C3, 8절): 로그인, 계정, 착용자, 착용 기록, 장치 등록."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from uwbserver.pipeline import clock, jobs, settings, wire
from uwbserver.registry import rules, store
from uwbserver.registry.access import AdminUser, Conn, CurrentUser, PageParams, check_device, check_subject, not_found

app_router = APIRouter(prefix="/v1")

Side = Literal["left", "right"]


# ---------------------------------------------------------------- 모델


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    token: str
    expires_at: datetime


class SubjectIn(BaseModel):
    code: str = Field(min_length=1, max_length=64, description="표시명. 실명 아님")
    height_m: float | None = Field(default=None, gt=0, lt=3)
    leg_length_m: float | None = Field(default=None, gt=0, lt=2)
    paretic_side: Side | None = None


class SubjectPatch(BaseModel):
    height_m: float | None = Field(default=None, gt=0, lt=3)
    leg_length_m: float | None = Field(default=None, gt=0, lt=2)
    paretic_side: Side | None = None


class SubjectOut(BaseModel):
    id: int
    code: str
    height_m: float | None
    leg_length_m: float | None
    paretic_side: Side | None


class UserIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    role: Literal["member", "admin"]
    subject_id: int | None = None


class MeOut(BaseModel):
    id: int
    email: str
    role: str
    subject: SubjectOut | None


class WearIn(BaseModel):
    subject_id: int
    device_id: str
    tag_mac: str | None = None
    started_at: datetime
    ended_at: datetime | None = None

    @field_validator("tag_mac")
    @classmethod
    def _mac(cls, v: str | None) -> str | None:
        return None if v is None else rules.normalize_mac(v)


class WearPatch(BaseModel):
    started_at: datetime | None = None
    ended_at: datetime | None = None


class WearOut(BaseModel):
    id: int
    subject_id: int
    device_id: str
    tag_mac: str | None
    started_at: datetime
    ended_at: datetime | None


class DeviceIn(BaseModel):
    mac: str
    note: str | None = None

    @field_validator("mac")
    @classmethod
    def _mac(cls, v: str) -> str:
        return rules.normalize_mac(v)


class DeviceTokenOut(BaseModel):
    id: str
    token: str = Field(description="장치 토큰. 이 응답에서 한 번만 보임(RG-1)")


class DeviceOut(BaseModel):
    id: str
    mac: str
    registered_at: datetime
    token_issued_at: datetime | None
    active: bool
    note: str | None


class DeviceDetailOut(DeviceOut):
    system: dict | None = Field(description="최신 system 블록(wire.md 필드 이름)")
    health: dict | None = Field(description="최신 health 블록(wire.md 필드 이름)")


def _tz(at: datetime | None) -> None:
    if at is not None and at.tzinfo is None:
        raise HTTPException(422, "timestamps need a UTC offset")


# ---------------------------------------------------------------- 로그인 (RG-6)


@app_router.post("/auth/token", response_model=TokenOut, tags=["auth"])
def login(body: LoginIn, conn: Conn) -> TokenOut:
    now = clock.now()
    with conn.transaction():
        store.delete_expired_app_tokens(conn, now)
        user = store.user_by_email(conn, body.email)
        if user is None or not rules.verify_password(user["password_hash"], body.password):
            raise HTTPException(401, "wrong email or password")
        token = rules.new_token()
        expires = rules.token_expiry(now, settings.get().app_token_ttl_days)
        store.insert_app_token(conn, rules.token_hash(token), user["id"], expires, now)
    return TokenOut(token=token, expires_at=expires)


@app_router.delete("/auth/token", status_code=204, tags=["auth"])
def logout(user: CurrentUser, conn: Conn) -> Response:
    with conn.transaction():
        store.revoke_app_token(conn, user.token_hash, clock.now())
    return Response(status_code=204)


@app_router.get("/me", response_model=MeOut, tags=["auth"])
def me(user: CurrentUser, conn: Conn) -> MeOut:
    subj = store.subject(conn, user.subject_id) if user.subject_id else None
    return MeOut(id=user.id, email=user.email, role=user.role, subject=SubjectOut(**subj) if subj else None)


@app_router.post("/users", status_code=201, tags=["admin"])
def create_user(body: UserIn, _: AdminUser, conn: Conn) -> dict:
    if body.role == "member" and body.subject_id is None:
        raise HTTPException(422, "member needs subject_id")
    with conn.transaction():
        if body.subject_id is not None and store.subject(conn, body.subject_id) is None:
            raise HTTPException(422, "unknown subject_id")
        uid = store.insert_user(
            conn, email=body.email, password_hash=rules.hash_password(body.password), role=body.role,
            subject_id=body.subject_id, now=clock.now(),
        )  # fmt: skip
    if uid is None:
        raise HTTPException(409, "email already exists")
    return {"id": uid}


# ---------------------------------------------------------------- 착용자 (RG-3)


@app_router.post("/subjects", status_code=201, response_model=SubjectOut, tags=["subjects"])
def create_subject(body: SubjectIn, _: AdminUser, conn: Conn) -> SubjectOut:
    with conn.transaction():
        sid = store.insert_subject(conn, **body.model_dump())
    if sid is None:
        raise HTTPException(409, "code already exists")
    return SubjectOut(id=sid, **body.model_dump())


@app_router.patch("/subjects/{subject_id}", response_model=SubjectOut, tags=["subjects"])
def patch_subject(subject_id: int, body: SubjectPatch, _: AdminUser, conn: Conn) -> SubjectOut:
    with conn.transaction():
        if not store.update_subject(conn, subject_id, body.model_dump(exclude_unset=True)):
            raise not_found()
        return SubjectOut(**store.subject(conn, subject_id))


@app_router.get("/subjects/{subject_id}", response_model=SubjectOut, tags=["subjects"])
def get_subject(subject_id: int, user: CurrentUser, conn: Conn) -> SubjectOut:
    check_subject(user, subject_id)
    row = store.subject(conn, subject_id)
    if row is None:
        raise not_found()
    return SubjectOut(**row)


# ---------------------------------------------------------------- 착용 기록 (RG-4)


@app_router.post("/wear", status_code=201, response_model=WearOut, tags=["wear"])
def create_wear(body: WearIn, _: AdminUser, conn: Conn) -> WearOut:
    _tz(body.started_at)
    _tz(body.ended_at)
    if body.ended_at is not None and body.ended_at <= body.started_at:
        raise HTTPException(422, "ended_at must be after started_at")
    with conn.transaction():
        if store.subject(conn, body.subject_id) is None:
            raise HTTPException(422, "unknown subject_id")
        if store.device_by_id(conn, body.device_id) is None:
            raise HTTPException(422, "unknown device_id")
        wid = store.insert_wear(conn, **body.model_dump())
        _enqueue_summary(conn, body.device_id, [(body.started_at, body.ended_at)])
        return WearOut(**store.wear(conn, wid))


@app_router.patch("/wear/{wear_id}", response_model=WearOut, tags=["wear"])
def patch_wear(wear_id: int, body: WearPatch, _: AdminUser, conn: Conn) -> WearOut:
    """시작과 끝만 고침. 착용자나 장치를 바꾸려면 끝내고 새로 만듦(RG-4). 결과 표는 고치지 않음(SG-9).
    고치기 전·후 기간의 일별 값은 다시 계산하게 함(SM-1)."""
    _tz(body.started_at)
    _tz(body.ended_at)
    with conn.transaction():
        row = store.wear(conn, wear_id)
        if row is None:
            raise not_found()
        changes = body.model_dump(exclude_unset=True)
        started = changes.get("started_at", row["started_at"])
        ended = changes.get("ended_at", row["ended_at"])
        if started is None or (ended is not None and ended <= started):
            raise HTTPException(422, "ended_at must be after started_at")
        store.update_wear_times(conn, wear_id, started, ended)
        _enqueue_summary(conn, row["device_id"], [(row["started_at"], row["ended_at"]), (started, ended)])
        return WearOut(**store.wear(conn, wear_id))


def _enqueue_summary(conn, device_id: str, spans: list) -> None:
    """SM-1 (AP-5를 바꿈): 착용 기록을 만들거나 고치면, 고치기 전·후 기간을 합친 날짜의 일별 값을 다시 계산하게 함.
    그 장치를 찬 착용자 전원이 다시 계산되므로 기간을 잃은 착용자와 얻은 착용자가 함께 고쳐짐."""
    first, last = rules.summary_dates(spans, clock.now())
    jobs.enqueue_summary(conn, device_id, first, last, now=clock.now())


@app_router.get("/subjects/{subject_id}/wear", response_model=list[WearOut], tags=["wear"])
def list_wear(subject_id: int, user: CurrentUser, conn: Conn, p: PageParams) -> list[WearOut]:
    check_subject(user, subject_id)
    if store.subject(conn, subject_id) is None:
        raise not_found()
    return [WearOut(**r) for r in store.wear_of_subject(conn, subject_id, p.limit, p.before_id)]


# ---------------------------------------------------------------- 장치 (RG-1, RG-2)


@app_router.post("/devices", status_code=201, response_model=DeviceTokenOut, tags=["devices"])
def register_device(body: DeviceIn, _: AdminUser, conn: Conn) -> DeviceTokenOut:
    token = rules.new_token()
    did = rules.device_id(body.mac)
    with conn.transaction():
        ok = store.insert_device(
            conn, device_id=did, mac=body.mac, token_hash=rules.token_hash(token), now=clock.now(), note=body.note
        )
    if not ok:
        raise HTTPException(409, "device already registered")
    return DeviceTokenOut(id=did, token=token)


@app_router.post("/devices/{device_id}/token", tags=["devices"])
def rotate_device_token(device_id: str, _: AdminUser, conn: Conn) -> dict:
    """재발급하면 이전 토큰은 그 즉시 안 됨(RG-2)."""
    token = rules.new_token()
    with conn.transaction():
        if not store.set_device_token(conn, device_id, rules.token_hash(token), clock.now()):
            raise not_found()
    return {"token": token}


@app_router.delete("/devices/{device_id}/token", status_code=204, tags=["devices"])
def revoke_device_token(device_id: str, _: AdminUser, conn: Conn) -> Response:
    with conn.transaction():
        if not store.set_device_token(conn, device_id, None, clock.now()):
            raise not_found()
    return Response(status_code=204)


@app_router.get("/devices", response_model=list[DeviceOut], tags=["devices"])
def list_devices(user: CurrentUser, conn: Conn) -> list[DeviceOut]:
    ids = None if user.admin else (store.device_ids_worn_by(conn, user.subject_id) if user.subject_id else [])
    return [DeviceOut(**r) for r in store.list_devices(conn, ids)]


@app_router.get("/devices/{device_id}", response_model=DeviceDetailOut, tags=["devices"])
def get_device(device_id: str, user: CurrentUser, conn: Conn) -> DeviceDetailOut:
    check_device(conn, user, device_id)
    (row,) = store.list_devices(conn, [device_id])
    blocks = store.latest_blocks(conn, device_id, None if user.admin else user.subject_id)
    decoded = {}
    for kind, b in blocks.items():
        decoded[kind] = None if b is None else {
            "at": b["at"].isoformat(), "head_boot_id": b["head_boot_id"], "chunk_seq": b["chunk_seq"],
            **(wire.decode_system(bytes(b["block"])) if kind == "system" else wire.decode_health(bytes(b["block"]))),
        }  # fmt: skip
    return DeviceDetailOut(**row, **decoded)

