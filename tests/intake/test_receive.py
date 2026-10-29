"""M1 받기 (IN, RG-1·2, TL-1·3·5). SPEC 11절 M1 시험 2–8."""

import threading

import pytest

from tests.support import auth, register
from tools import fake_head
from uwbserver.intake import run as intake_run
from uwbserver.pipeline import clock, files, wire
from uwbserver.registry import rules as registry_rules
from uwbserver.registry import store as registry_store

pytestmark = pytest.mark.db


def _post_all(env, client, token, requests):
    out = []
    for r in requests:
        env.clock.set(r.at_us)
        resp = client.post("/d/v1/chunks", content=r.body, headers=auth(token))
        out.append(resp)
    return out


def _raw_bytes(env) -> int:
    return sum(p.stat().st_size for p in (env.data_root / "raw").rglob("*.bin")) if (env.data_root / "raw").exists() else 0


def test_normal_10min_all_stored(env, device, intake_client):
    """M1-2: 보낸 묶음 수 = intake.chunk 행 수, 응답 코드 모두 0."""
    reqs = fake_head.scenario("normal", duration_s=600)
    resps = _post_all(env, intake_client, device.token, reqs)
    codes = []
    for resp in resps:
        assert resp.status_code == 200
        server_us, c = wire.decode_ack(resp.content)
        codes += c
    sent = sum(len(r.keys) for r in reqs)
    assert codes == [0] * sent
    assert env.scalar("SELECT count(*) FROM intake.chunk") == sent
    assert env.scalar("SELECT count(*) FROM intake.health") >= 9  # 60초마다
    assert env.scalar("SELECT count(*) FROM intake.system") >= 1  # 부팅 첫 묶음
    # 행의 머리 복사와 파일 위치가 맞음
    row = env.one("SELECT raw_path, raw_offset, raw_len, chunk_seq FROM intake.chunk ORDER BY id LIMIT 1")
    data = files.read_at(row["raw_path"], row["raw_offset"], row["raw_len"])
    assert wire.decode_chunk(data).header.chunk_seq == row["chunk_seq"]


def test_resend_is_duplicate(env, device, intake_client):
    """M1-3: 같은 요청을 다시 보내면 모두 1, 행 수와 raw 크기 그대로."""
    reqs = fake_head.scenario("normal", duration_s=30)
    _post_all(env, intake_client, device.token, reqs)
    n_rows, n_bytes = env.scalar("SELECT count(*) FROM intake.chunk"), _raw_bytes(env)
    for resp in _post_all(env, intake_client, device.token, reqs):
        assert wire.decode_ack(resp.content)[1] == [1] * len(wire.split_request(resp.request.content))
    assert env.scalar("SELECT count(*) FROM intake.chunk") == n_rows
    assert _raw_bytes(env) == n_bytes


def test_resend_scenario(env, device, intake_client):
    reqs = fake_head.scenario("resend")
    codes = [c for resp in _post_all(env, intake_client, device.token, reqs) for c in wire.decode_ack(resp.content)[1]]
    assert codes.count(1) >= 1 and set(codes) <= {0, 1}
    keys = {k for r in reqs for k in r.keys}
    assert env.scalar("SELECT count(*) FROM intake.chunk") == len(keys)


def test_unknown_and_revoked_token(env, device, intake_client):
    """M1-4 (IN-2, RG-2)."""
    body = fake_head.scenario("normal", duration_s=5)[0].body
    assert intake_client.post("/d/v1/chunks", content=body, headers=auth("x" * 43)).status_code == 401
    assert intake_client.post("/d/v1/chunks", content=body).status_code == 401
    api = env.conn("api")
    with api.transaction():
        registry_store.set_device_token(api, device.device.id, None, clock.now())
    assert intake_client.post("/d/v1/chunks", content=body, headers=auth(device.token)).status_code == 401
    # 재발급: 옛 토큰 401, 새 토큰 200
    new = registry_rules.new_token()
    with api.transaction():
        registry_store.set_device_token(api, device.device.id, registry_rules.token_hash(new), clock.now())
    assert intake_client.post("/d/v1/chunks", content=body, headers=auth(device.token)).status_code == 401
    assert env.scalar("SELECT count(*) FROM intake.chunk") == 0 and _raw_bytes(env) == 0
    assert intake_client.post("/d/v1/chunks", content=body, headers=auth(new)).status_code == 200


EXPECTED = {
    "bad_crc": (2, 6), "bad_magic": (3, 3), "bad_version": (4, 4), "bad_header_len": (5, 5),
    "too_large": (6, 2), "inconsistent_len": (7, 7), "inconsistent_status_len": (7, 7), "unknown_kind": (4, 7),
    "empty_chunk": (8, 8), "tag_mask_mismatch": (8, 8), "valid_not_reported": (8, 8),
    "geometry_hash_mismatch": (8, 8), "device_mismatch": (9, 9),
}  # fmt: skip


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_rejected_each_kind(env, device, intake_client, kind):
    """M1-5: 기대 코드, intake.rejected 행, rejected 파일 레코드. reingest는 같은 코드."""
    good = fake_head.scenario("normal", duration_s=3)[0]
    good_chunk = wire.split_request(good.body)[0]
    bad = fake_head.bad_chunk(kind)
    env.clock.set(good.at_us)
    resp = intake_client.post("/d/v1/chunks", content=wire.join_records([good_chunk, bad]), headers=auth(device.token))
    code, check = EXPECTED[kind]
    assert wire.decode_ack(resp.content)[1] == [0, code]
    row = env.one("SELECT code, check_step, detail, path, file_offset, len FROM intake.rejected")
    assert (row["code"], row["check_step"]) == (code, check) and row["detail"]
    recs, _ = files.scan_rejected(row["path"])
    assert [(r.code, r.check, r.data) for r in recs] == [(code, check, bad)]
    # IN-10: 고치지 않은 서버에서 다시 넣으면 같은 코드, rejected는 그대로
    counts = intake_run.reingest_rejected(env.conn("intake"), device.device, row["path"])
    assert dict(counts) == {code: 1}
    assert env.scalar("SELECT count(*) FROM intake.rejected") == 1


def test_framing_400_and_413(env, device, intake_client):
    body = fake_head.bad_request_framing()
    resp = intake_client.post("/d/v1/chunks", content=body, headers=auth(device.token))
    assert resp.status_code == 400
    row = env.one("SELECT code, check_step, len FROM intake.rejected")
    assert (row["code"], row["check_step"], row["len"]) == (255, 1, len(body))
    big = b"\0" * (wire.MAX_BODY_BYTES + 1)
    assert intake_client.post("/d/v1/chunks", content=big, headers=auth(device.token)).status_code == 413
    assert env.scalar("SELECT count(*) FROM intake.rejected") == 1


def test_reindex_rebuilds_rows(env, device, intake_client):
    """M1-6: intake.*를 비우고 다시 만들면 키, 머리 열, 블록 행이 처음과 같음."""
    _post_all(env, intake_client, device.token, fake_head.scenario("geometry_change"))
    q = {
        "chunk": "SELECT * FROM intake.chunk ORDER BY device_id, head_boot_id, chunk_seq",
        "health": "SELECT * FROM intake.health ORDER BY 1,2,3",
        "system": "SELECT * FROM intake.system ORDER BY 1,2,3",
        "survey": "SELECT * FROM intake.survey ORDER BY 1,2,3",
    }
    before = {k: env.q(v) for k, v in q.items()}
    assert before["survey"], "geometry_change 시나리오에 survey 블록이 있어야 함"
    env.q("TRUNCATE intake.chunk, intake.health, intake.system, intake.survey RESTART IDENTITY")
    stats = intake_run.reindex(env.conn("intake"))
    assert stats.inserted == len(before["chunk"])
    after = {k: env.q(v) for k, v in q.items()}
    strip = lambda rows: [{k: v for k, v in r.items() if k != "id"} for r in rows]  # noqa: E731
    assert strip(after["chunk"]) == strip(before["chunk"])
    for k in ("health", "system", "survey"):
        assert after[k] == before[k]


def test_startup_reindex_recovers_lost_rows_and_torn_tail(env, device, intake_client):
    """M1-7을 프로세스 안에서 흉내: 커밋이 디스크를 기다리지 않아 잃은 행 + 쓰다 만 꼬리."""
    reqs = fake_head.scenario("normal", duration_s=120)
    acked = set()
    for resp, r in zip(_post_all(env, intake_client, device.token, reqs), reqs, strict=True):
        for key, code in zip(r.keys, wire.decode_ack(resp.content)[1], strict=True):
            if code in (0, 1):
                acked.add(key)
    env.q("DELETE FROM intake.health WHERE chunk_seq >= 100")
    env.q("DELETE FROM intake.system WHERE chunk_seq >= 100")
    env.q("DELETE FROM intake.chunk WHERE chunk_seq >= 100")
    path = env.one("SELECT raw_path FROM intake.chunk ORDER BY id DESC LIMIT 1")["raw_path"]
    with open(files.resolve(path), "ab") as f:
        f.write(files.raw_record_head(4000, 0) + b"partial")
    stats = intake_run.startup_reindex(env.conn("intake"))
    assert stats.truncated_bytes == 12 + len(b"partial")
    rows = env.q("SELECT head_boot_id, chunk_seq FROM intake.chunk")
    assert {(r["head_boot_id"], r["chunk_seq"]) for r in rows} == acked


def test_concurrent_same_request(env, device):
    """M1-8: 같은 요청 둘을 동시에 → 한쪽만 0, 다른 쪽 1, raw에 한 번만(IN-4)."""
    body = fake_head.scenario("normal", duration_s=3)[0].body
    replies = []
    barrier = threading.Barrier(2)

    def go():
        conn = env.conn("intake")
        barrier.wait()
        replies.append(intake_run.receive(conn, device.device, body))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.codes for r in replies) == [(0,) * len(replies[0].codes), (1,) * len(replies[0].codes)]
    assert env.scalar("SELECT count(*) FROM intake.chunk") == len(replies[0].codes)
    assert _raw_bytes(env) == 12 * len(replies[0].codes) + sum(len(c) for c in wire.split_request(body))


def test_id_order_equals_commit_order(env):
    """M1-8 (PL-3): 받는 동안 id > cursor를 반복해 읽는 쪽이, 이미 본 최대 id보다 작은 id를 나중에 새로 보지 않음."""
    macs = fake_head.device_macs(4)
    regs = {m: register(env.conn("api"), m) for m in macs}
    streams = fake_head.multi_device(4, "normal", duration_s=60)
    stop = threading.Event()
    violations = []

    def reader():
        conn = env.conn("core")
        seen_max, seen = 0, set()
        while not stop.is_set():
            ids = [r["id"] for r in conn.execute("SELECT id FROM intake.chunk WHERE id > %s ORDER BY id", (0,)).fetchall()]
            new = [i for i in ids if i not in seen]
            if new and min(new) < seen_max:
                violations.append((min(new), seen_max))
            seen.update(new)
            if ids:
                seen_max = max(seen_max, max(ids))

    def writer(mac):
        conn = env.conn("intake")
        for r in streams[mac]:
            intake_run.receive(conn, regs[mac].device, r.body, received_us=r.at_us)

    rt = threading.Thread(target=reader)
    rt.start()
    ws = [threading.Thread(target=writer, args=(m,)) for m in macs]
    for w in ws:
        w.start()
    for w in ws:
        w.join()
    stop.set()
    rt.join()
    assert not violations
    assert env.scalar("SELECT count(*) FROM intake.chunk") == sum(len(r.keys) for s in streams.values() for r in s)



def test_reindex_after_db_only_restart(env, device, intake_client):
    """IN-9 보강: DB만 재시작해 받기 행을 잃으면(intake는 그대로) intake가 DB 재시작을 보고 재색인."""
    reqs = fake_head.scenario("normal", duration_s=60)
    _post_all(env, intake_client, device.token, reqs)
    n = env.scalar("SELECT count(*) FROM intake.chunk")
    conn = env.conn("intake")
    state: dict = {}
    assert intake_run.reindex_if_db_restarted(conn, state) is None  # 처음 본 시작 시각을 적음
    env.q("DELETE FROM intake.health WHERE chunk_seq >= 50")
    env.q("DELETE FROM intake.system WHERE chunk_seq >= 50")
    env.q("DELETE FROM intake.chunk WHERE chunk_seq >= 50")
    assert intake_run.reindex_if_db_restarted(conn, state) is None  # 시작 시각이 같으면 아무것도 안 함
    state["db_started"] = state["db_started"].replace(year=2000)  # DB가 다시 뜬 것처럼
    stats = intake_run.reindex_if_db_restarted(conn, state)
    assert stats is not None and stats.inserted == n - 50
    assert env.scalar("SELECT count(*) FROM intake.chunk") == n


def test_reindex_skips_files_purge_processed(env, device, intake_client):
    """검토 10: 삭제가 행을 지우고 파일만 남은 경우, 재색인이 그 행을 다시 넣어 구간에 두 번 세지 않음."""
    _post_all(env, intake_client, device.token, fake_head.scenario("normal", duration_s=20))
    path = env.one("SELECT raw_path FROM intake.chunk LIMIT 1")["raw_path"]
    n = env.scalar("SELECT count(*) FROM intake.chunk")
    env.q("DELETE FROM intake.health; DELETE FROM intake.system")
    env.q("DELETE FROM intake.chunk")
    env.q("INSERT INTO archive.purged VALUES (%s, %s, %s, now())", (path, n, n))
    assert intake_run.reindex(env.conn("intake")).inserted == 0
    env.q("DELETE FROM archive.purged")
    assert intake_run.reindex(env.conn("intake")).inserted == n
