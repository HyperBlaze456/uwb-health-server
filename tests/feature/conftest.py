"""feature 시험 공용: 결과를 고쳐 내는 가짜 gaitfusion, 앱 API 준비물."""

import dataclasses
import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from tests.support import fake_c2
from uwbserver.analysis.gaitfusion import Part
from uwbserver.pipeline import clock
from uwbserver.process.api import create_app
from uwbserver.registry import rules, store


class PatchedC2:
    """가짜 gaitfusion의 응답에서 result 파트만 patch(doc)로 고침(MC-4 표의 "gaitfusion에서 바뀐 것")."""

    def __init__(self, patch: Callable[[dict], None], mode: str = "ok") -> None:
        self.inner = fake_c2(mode)
        self.patch = patch

    def version(self) -> dict:
        return self.inner.version()

    def analyze(self, context: dict, chunks: bytes):
        reply = self.inner.analyze(context, chunks)
        if not reply.parts:
            return reply
        doc = json.loads(reply.parts[0].data)  # 첫 파트가 result(7.2)
        self.patch(doc)
        used = {e["part"] for b in doc["bouts"] for e in b["trajectories"]}  # 걷기 구간을 지웠으면 그 궤적 파트도 뺌
        parts = [Part("result", reply.parts[0].content_type, json.dumps(doc).encode())]
        parts += [p for p in reply.parts[1:] if p.name in used]
        return dataclasses.replace(reply, parts=parts)


def patched_core(patch: Callable[[dict], None]):
    from uwbserver.process.core import Core

    core = Core(client=PatchedC2(patch))
    core._check_c2()
    assert core.c2_ready.is_set()
    return core


@pytest.fixture
def api(env):
    with TestClient(create_app()) as c:
        yield c


def _bearer(api, env, email: str, role: str, subject_id: int | None = None) -> dict:
    conn = env.conn("api")
    with conn.transaction():
        store.insert_user(conn, email=email, password_hash=rules.hash_password("password-123"), role=role,
                          subject_id=subject_id, now=clock.now())  # fmt: skip
    r = api.post("/v1/auth/token", json={"email": email, "password": "password-123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def admin(env, api) -> dict:
    return _bearer(api, env, "admin@x.org", "admin")


@pytest.fixture
def people(env, api, admin, device) -> dict:
    """착용자 둘(A, B)과 각자의 member 계정. A가 device를 참."""
    a = api.post("/v1/subjects", json={"code": "A01"}, headers=admin).json()["id"]
    b = api.post("/v1/subjects", json={"code": "B01"}, headers=admin).json()["id"]
    r = api.post("/v1/wear", json={"subject_id": a, "device_id": device.device.id,
                                   "started_at": "2026-10-25T00:00:00Z"}, headers=admin)  # fmt: skip
    assert r.status_code == 201, r.text
    return {"A": a, "B": b, "a": _bearer(api, env, "a@x.org", "member", a), "b": _bearer(api, env, "b@x.org", "member", b)}
