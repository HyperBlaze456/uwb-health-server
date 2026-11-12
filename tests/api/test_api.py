"""M5 앱 API (AP, RG, TL-6). SPEC 11절 M5 시험 1–5."""

from datetime import datetime


import pytest
from fastapi.testclient import TestClient

from tests.support import auth, closed_segment, make_core, run_jobs
from tools import fake_head
from uwbserver.analysis import rules as analysis_rules
from uwbserver.pipeline import clock
from uwbserver.process.api import create_app
from uwbserver.registry import rules, store

pytestmark = pytest.mark.db


@pytest.fixture
def api(env):
    with TestClient(create_app()) as c:
        yield c


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _user(env, email, role, subject_id=None, password="password-123"):
    conn = env.conn("api")
    with conn.transaction():
        store.insert_user(conn, email=email, password_hash=rules.hash_password(password), role=role,
                          subject_id=subject_id, now=clock.now())
    return email, password


def _login(api, email, password="password-123"):
    r = api.post("/v1/auth/token", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
def admin(env, api):
    _user(env, "admin@x.org", "admin")
    return _bearer(_login(api, "admin@x.org"))


@pytest.fixture
def people(env, api, admin):
    """착용자 둘(A, B)과 각자의 member 계정."""
    a = api.post("/v1/subjects", json={"code": "A01", "height_m": 1.72, "leg_length_m": 0.9}, headers=admin).json()
    b = api.post("/v1/subjects", json={"code": "B01", "paretic_side": "left"}, headers=admin).json()
    for email, sid in (("a@x.org", a["id"]), ("b@x.org", b["id"])):
        assert api.post("/v1/users", json={"email": email, "password": "password-123", "role": "member",
                                           "subject_id": sid}, headers=admin).status_code == 201
    return {"A": a["id"], "B": b["id"], "a": _bearer(_login(api, "a@x.org")), "b": _bearer(_login(api, "b@x.org"))}


def test_login_logout_and_expiry(env, api, admin):
    assert api.post("/v1/auth/token", json={"email": "admin@x.org", "password": "nope-nope"}).status_code == 401
    assert api.get("/v1/me").status_code == 401
    me = api.get("/v1/me", headers=admin).json()
    assert me["role"] == "admin" and me["subject"] is None
    token = _login(api, "admin@x.org")
    assert api.delete("/v1/auth/token", headers=_bearer(token)).status_code == 204
    assert api.get("/v1/me", headers=_bearer(token)).status_code == 401
    token = _login(api, "admin@x.org")
    env.clock.advance(31 * 86400)
    assert api.get("/v1/me", headers=_bearer(token)).status_code == 401
    _login(api, "admin@x.org")  # 로그인할 때 만료 토큰 정리(RG-6)
    assert env.scalar("SELECT count(*) FROM registry.app_token WHERE expires_at <= %s", (clock.now(),)) == 0


def test_device_register_rotate_revoke(env, api, admin, intake_client):
    """M5-2: 장치 토큰은 만들 때만 보이고, 재발급 뒤 이전 토큰으로 받기가 401."""
    r = api.post("/v1/devices", json={"mac": "E0-72-A1-AC-A8-58", "note": "lab"}, headers=admin)
    assert r.status_code == 201
    did, token = r.json()["id"], r.json()["token"]
    assert did == "head-e072a1aca858" and len(token) == 43
    assert api.post("/v1/devices", json={"mac": "e0:72:a1:ac:a8:58"}, headers=admin).status_code == 409
    assert "token" not in api.get(f"/v1/devices/{did}", headers=admin).json()
    body = fake_head.scenario("normal", duration_s=3)[0].body
    assert intake_client.post("/d/v1/chunks", content=body, headers=auth(token)).status_code == 200
    new = api.post(f"/v1/devices/{did}/token", headers=admin).json()["token"]
    assert intake_client.post("/d/v1/chunks", content=body, headers=auth(token)).status_code == 401
    assert intake_client.post("/d/v1/chunks", content=body, headers=auth(new)).status_code == 200
    assert api.delete(f"/v1/devices/{did}/token", headers=admin).status_code == 204
    assert intake_client.post("/d/v1/chunks", content=body, headers=auth(new)).status_code == 401
    assert api.post("/v1/devices", json={"mac": "zz"}, headers=admin).status_code == 422


def test_bad_input_422(api, admin, people):
    assert api.post("/v1/subjects", json={"code": ""}, headers=admin).status_code == 422
    assert api.post("/v1/subjects", json={"code": "C", "paretic_side": "up"}, headers=admin).status_code == 422
    assert api.post("/v1/users", json={"email": "m@x.org", "password": "password-123", "role": "member"}, headers=admin).status_code == 422
    assert api.post("/v1/wear", json={"subject_id": people["A"], "device_id": "nope", "started_at": "2026-10-26T00:00:00Z"}, headers=admin).status_code == 422
    assert api.get("/v1/segments?limit=0", headers=admin).status_code == 422
    assert api.get("/v1/segments?limit=1001", headers=admin).status_code == 422
    assert api.get("/v1/segments?from=2026-10-26T00:00:00", headers=admin).status_code == 422  # 시간대 없음
    assert api.post("/v1/subjects", json={"code": "A01"}, headers=admin).status_code == 409


def test_member_sees_only_own(env, api, admin, people, device):
    """AP-2: 남의 것은 404, admin 전용은 403."""
    a, b = people["a"], people["b"]
    assert api.get(f"/v1/subjects/{people['A']}", headers=a).status_code == 200
    assert api.get(f"/v1/subjects/{people['B']}", headers=a).status_code == 404
    assert api.get(f"/v1/subjects/{people['B']}/wear", headers=a).status_code == 404
    assert api.get(f"/v1/subjects/{people['B']}/bouts", headers=a).status_code == 404
    assert api.post("/v1/subjects", json={"code": "Z"}, headers=a).status_code == 403
    assert api.get("/v1/segments", headers=a).status_code == 403
    assert api.get(f"/v1/devices/{device.device.id}", headers=a).status_code == 404
    assert api.get("/v1/devices", headers=a).json() == []
    api.post("/v1/wear", json={"subject_id": people["A"], "device_id": device.device.id,
                               "started_at": "2026-10-01T00:00:00Z"}, headers=admin)
    assert [d["id"] for d in api.get("/v1/devices", headers=a).json()] == [device.device.id]
    assert api.get(f"/v1/devices/{device.device.id}", headers=a).status_code == 200
    assert api.get(f"/v1/devices/{device.device.id}", headers=b).status_code == 404
    assert api.get(f"/v1/devices/{device.device.id}/rejected", headers=a).status_code == 403


def test_device_detail_and_health_decoded(env, api, admin, device, intake_client):
    """M5-3 (AP-3): health 조회가 wire.md 필드 이름으로 풀려 나옴."""
    for r in fake_head.scenario("normal", duration_s=130):
        env.clock.set(r.at_us)
        intake_client.post("/d/v1/chunks", content=r.body, headers=auth(device.token))
    d = api.get(f"/v1/devices/{device.device.id}", headers=admin).json()
    assert d["system"]["head_mac"] == fake_head.TEST_DEVICE_MAC and d["system"]["geometry_id"] == "default"
    assert "counters" in d["health"] and "buffer_used" in d["health"]
    h = api.get(f"/v1/devices/{device.device.id}/health?from=2026-10-25T00:00:00Z", headers=admin).json()
    assert len(h) >= 2
    assert set(h[0]) >= {"at", "flags", "wifi_disconnect_reason", "tag_count", "buffer_used", "buffer_capacity",
                         "counters", "range_age_ms", "imu_age_ms", "anchors", "tags"}
    assert h[0]["counters"].keys() >= {"range_count", "chunks_dropped_overflow", "upload_failures"}


def test_segments_runs_bouts_trajectory(env, api, admin, people, device):
    """구간·run·결과 원문·걷기 구간·궤적(AP-4: rate=25는 100 Hz의 4개마다 1개)."""
    api.post("/v1/wear", json={"subject_id": people["A"], "device_id": device.device.id,
                               "started_at": "2026-10-25T00:00:00Z"}, headers=admin)
    seg = closed_segment(env, device)
    run_jobs(env, make_core())
    segs = api.get(f"/v1/segments?device={device.device.id}&from=2026-10-25T00:00:00Z", headers=admin).json()
    assert [s["id"] for s in segs] == [seg]
    detail = api.get(f"/v1/segments/{seg}", headers=admin).json()
    assert detail["subject_id"] == people["A"] and detail["tags"] and len(detail["runs"]) == 1
    run_id = detail["runs"][0]["id"]
    run = api.get(f"/v1/runs/{run_id}", headers=admin).json()
    assert run["is_current"] and run["bouts"] and run["result_status"] == "complete"
    assert detail["runs"][0]["result_status"] == "complete"
    res = api.get(f"/v1/runs/{run_id}/result", headers=admin)
    assert res.status_code == 200 and res.json()["c2_version"] == 1  # 클라이언트가 gzip을 풂
    bouts = api.get(f"/v1/subjects/{people['A']}/bouts?from=2026-10-25T00:00:00Z", headers=people["a"]).json()
    assert len(bouts) == len(run["bouts"]) and {b["result_status"] for b in bouts} == {"complete"}
    assert api.get(f"/v1/subjects/{people['A']}/bouts?from=2026-10-25T00:00:00Z", headers=people["b"]).status_code == 404
    bid = bouts[0]["id"]
    t25 = api.get(f"/v1/bouts/{bid}/trajectory", headers=people["a"]).json()["trajectories"][0]
    t100 = api.get(f"/v1/bouts/{bid}/trajectory?rate=100&side=right", headers=people["a"]).json()["trajectories"][0]
    assert t25["rate_hz"] == 25 and t100["rate_hz"] == 100
    assert t25["n"] == (t100["n"] - 1) // 4 + 1
    for ch in analysis_rules.TRJ_CHANNELS:
        assert t25["channels"][ch] == t100["channels"][ch][::4]
    assert api.get(f"/v1/bouts/{bid}/trajectory", headers=people["b"]).status_code == 404
    assert api.get(f"/v1/bouts/{bid}/trajectory?side=left", headers=people["a"]).status_code == 404
    # 재분석 요청: 202, 옛 궤적은 지워져 404
    r = api.post(f"/v1/segments/{seg}/reanalyze", headers=admin)
    assert r.status_code == 202 and r.json()["queued"]
    run_jobs(env, make_core())
    assert api.get(f"/v1/bouts/{bid}/trajectory", headers=people["a"]).status_code == 404
    # raw도 archive도 없으면 409
    env.q("DELETE FROM archive.range")
    env.q("DELETE FROM intake.health; DELETE FROM intake.system; DELETE FROM intake.chunk")
    assert api.post(f"/v1/segments/{seg}/reanalyze", headers=admin).status_code == 409


def test_partial_run_visible(env, api, admin, people, device):
    """gaitfusion이 partial로 답한 run: done이고 현재 run, result_status가 run·구간 상세·걷기 구간 목록에 보임.
    궤적이 빠진 걷기 구간의 궤적 조회는 404(15절 정할 것 7)."""
    api.post("/v1/wear", json={"subject_id": people["A"], "device_id": device.device.id,
                               "started_at": "2026-10-25T00:00:00Z"}, headers=admin)
    seg = closed_segment(env, device)
    run_jobs(env, make_core("partial"))
    (summary,) = api.get(f"/v1/segments/{seg}", headers=admin).json()["runs"]
    assert (summary["status"], summary["result_status"]) == ("done", "partial")
    run = api.get(f"/v1/runs/{summary['id']}", headers=admin).json()
    assert run["status"] == "done" and run["is_current"] and run["result_status"] == "partial"
    assert run["bouts"][-1]["trajectory_sides"] == [] and run["bouts"][0]["trajectory_sides"] == ["right"]
    bouts = api.get(f"/v1/subjects/{people['A']}/bouts?from=2026-10-25T00:00:00Z", headers=people["a"]).json()
    assert {b["result_status"] for b in bouts} == {"partial"}
    last = max(bouts, key=lambda b: b["start_at"])
    assert api.get(f"/v1/bouts/{last['id']}/trajectory", headers=people["a"]).status_code == 404


def test_wear_edit_moves_bout_owner(env, api, admin, people, device):
    """M5-5 (SG-9): 착용 기록을 고치면 다음 조회부터 걷기 구간의 주인이 바뀜."""
    w = api.post("/v1/wear", json={"subject_id": people["A"], "device_id": device.device.id,
                                   "started_at": "2026-10-25T00:00:00Z"}, headers=admin).json()
    closed_segment(env, device)
    run_jobs(env, make_core())
    q = "/v1/subjects/{}/bouts?from=2026-10-25T00:00:00Z"
    n = len(api.get(q.format(people["A"]), headers=admin).json())
    assert n > 0 and api.get(q.format(people["B"]), headers=admin).json() == []
    # A의 착용을 시작 전에 끝내고 B의 착용으로 바꿈
    assert api.patch(f"/v1/wear/{w['id']}", json={"ended_at": "2026-10-25T12:00:00Z"}, headers=admin).status_code == 200
    api.post("/v1/wear", json={"subject_id": people["B"], "device_id": device.device.id,
                               "started_at": "2026-10-25T12:00:00Z"}, headers=admin)
    assert api.get(q.format(people["A"]), headers=admin).json() == []
    assert len(api.get(q.format(people["B"]), headers=admin).json()) == n
    assert api.patch(f"/v1/wear/{w['id']}", json={"ended_at": "2026-10-24T00:00:00Z"}, headers=admin).status_code == 422


def test_admin_tool_same_store(env, capsys):
    from tools import admin as admin_tool

    env.clock.set(clock.to_us(clock.now()))
    import os

    os.environ["ADMIN_PASSWORD"] = "password-123"
    try:
        assert admin_tool.main(["create-admin", "root@x.org"]) == 0
        assert admin_tool.main(["create-admin", "root@x.org"]) == 1
        assert admin_tool.main(["register-device", "aa:bb:cc:dd:ee:00"]) == 0
        out = capsys.readouterr().out
        token = out.split("token")[1].split()[0]
        assert store.device_by_token_hash(env.conn("intake"), rules.token_hash(token)).id == "head-aabbccddee00"
        assert admin_tool.main(["rotate-token", "head-aabbccddee00"]) == 0
        assert store.device_by_token_hash(env.conn("intake"), rules.token_hash(token)) is None
        assert admin_tool.main(["reanalyze", "999"]) == 1
    finally:
        os.environ.pop("ADMIN_PASSWORD")



def test_member_sees_device_health_only_in_own_wear(env, api, admin, people, device, intake_client):
    """검토 11(AP-2): 같은 장치를 B가 찬 동안의 health는 A에게 보이지 않음."""
    for r in fake_head.scenario("normal", duration_s=250):
        env.clock.set(r.at_us)
        intake_client.post("/d/v1/chunks", content=r.body, headers=auth(device.token))
    at = env.q("SELECT at FROM intake.health ORDER BY at")
    assert len(at) >= 4
    split = at[2]["at"]  # 앞 두 기록은 A, 그 뒤는 B
    api.post("/v1/wear", json={"subject_id": people["A"], "device_id": device.device.id,
                               "started_at": "2026-10-25T00:00:00Z", "ended_at": split.isoformat()}, headers=admin)
    api.post("/v1/wear", json={"subject_id": people["B"], "device_id": device.device.id,
                               "started_at": split.isoformat()}, headers=admin)
    q = f"/v1/devices/{device.device.id}/health?from=2026-10-25T00:00:00Z"
    assert len(api.get(q, headers=people["a"]).json()) == 2
    assert len(api.get(q, headers=people["b"]).json()) == len(at) - 2
    assert len(api.get(q, headers=admin).json()) == len(at)
    a_detail = api.get(f"/v1/devices/{device.device.id}", headers=people["a"]).json()
    assert datetime.fromisoformat(a_detail["health"]["at"]) < split  # A에게는 자기 착용 기간의 최신 기록
