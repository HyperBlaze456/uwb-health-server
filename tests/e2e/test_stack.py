"""전 구간 시험 (M6-1, M1-7): docker compose로 전부 띄우고 HTTP로 받기 → 구간 → 분석까지.

이미지를 빌드하므로 기본으로는 건너뜁니다:  UWB_E2E=1 uv run pytest tests/e2e -s
개발 DB 옆에서 돌도록 프로젝트 이름과 호스트 포트를 따로 씁니다.
"""

import os
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

from tools import fake_head

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(os.environ.get("UWB_E2E") != "1", reason="UWB_E2E=1일 때만(이미지 빌드)"),
]

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "uwbe2e"
PORTS = {"DEV_DB_PORT": "55432", "DEV_INTAKE_PORT": "58001", "DEV_API_PORT": "58000"}
INTAKE = f"http://localhost:{PORTS['DEV_INTAKE_PORT']}"
API = f"http://localhost:{PORTS['DEV_API_PORT']}"


def compose(*args: str, env: dict, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", "compose.yaml", "-f", "compose.dev.yaml", *args]
    return subprocess.run(cmd, cwd=ROOT, env=env, check=check, capture_output=capture, text=True)


def psql(env: dict, sql: str) -> str:
    owner = _dotenv().get("DB_OWNER", "uwb")
    r = compose("exec", "-T", "db", "psql", "-U", owner, "-d", "uwb", "-At", "-c", sql, env=env)
    return r.stdout.strip()


def _dotenv() -> dict:
    out = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def wait_ok(url: str, timeout_s: float = 120) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise TimeoutError(url)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    data = tmp_path_factory.mktemp("uwb-data")
    for d in ("raw", "rejected", "archive", "result", "trajectory"):
        (data / d).mkdir()
        os.chmod(data / d, 0o777)
    os.chmod(data, 0o777)
    env = {**os.environ, **PORTS, "DATA_DIR": str(data)}
    compose("up", "-d", "--build", "--wait", "db", "intake", "api", "core", "gaitfusion", env=env, capture=False)
    try:
        wait_ok(f"{INTAKE}/healthz")
        wait_ok(f"{API}/healthz")
        yield env, data
    finally:
        if os.environ.get("UWB_E2E_KEEP") != "1":
            compose("down", "-v", "--remove-orphans", env=env, check=False)


def register_device(env: dict) -> str:
    r = compose("exec", "-T", "api", "python", "tools/admin.py", "register-device", fake_head.TEST_DEVICE_MAC, env=env)
    return r.stdout.split("token")[1].split()[0]


def send_until_acked(client: httpx.Client, token: str, body: bytes, timeout_s: float = 180) -> list[int]:
    """Head처럼: 확인을 못 받으면 다시 보냄(wire.md 다시 보내기 규칙)."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            r = client.post(f"{INTAKE}/d/v1/chunks", content=body,
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"})
            if r.status_code == 200:
                return fake_head.decode_ack(r.content)[1]
            assert r.status_code >= 500, r.status_code
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError("intake did not come back")
        time.sleep(0.5)


def test_whole_stack(stack):
    env, data = stack
    token = register_device(env)
    reqs = fake_head.scenario("normal", duration_s=240, start_utc_us=time.time_ns() // 1000 - 300_000_000)
    acked: set[tuple[int, int]] = set()
    go = threading.Event()
    killed = threading.Event()

    def killer():
        go.wait()
        subprocess.run(["docker", "kill", "--signal", "KILL", f"{PROJECT}-intake-1", f"{PROJECT}-db-1"],
                       capture_output=True, check=False)
        killed.set()
        time.sleep(2)
        compose("up", "-d", "db", "intake", env=env, check=False)  # 재시작(intake는 받기 전에 재색인)

    k = threading.Thread(target=killer, daemon=True)
    k.start()
    with httpx.Client(timeout=10) as client:
        for i, r in enumerate(reqs):
            if i == len(reqs) // 3:
                go.set()  # 보내는 도중에 죽임: 다음 요청들이 날아가는 사이에 intake와 db가 kill -9
            codes = send_until_acked(client, token, r.body)
            acked |= {k_ for k_, c in zip(r.keys, codes, strict=True) if c in (0, 1)}
    k.join(timeout=120)
    assert killed.is_set()

    # M1-7: 0·1을 받은 모든 키가 intake.chunk에 있음(재시작 때 재색인, IN-9)
    stored = {tuple(map(int, line.split("|"))) for line in psql(env, "SELECT head_boot_id, chunk_seq FROM intake.chunk").splitlines()}
    assert acked <= stored, sorted(acked - stored)[:10]
    assert len(acked) == sum(len(r.keys) for r in reqs)

    # core: 구간 → 침묵 60초 → 분석 → 보관
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        if psql(env, "SELECT count(*) FROM archive.cut") not in ("", "0"):
            break
        time.sleep(5)
    seg = psql(env, "SELECT state || ',' || n_chunks FROM segment.segment ORDER BY id LIMIT 1")
    assert seg.startswith("closed,"), seg
    run = psql(env, "SELECT status FROM analysis.run ORDER BY id LIMIT 1")
    assert run == "done"
    assert int(psql(env, "SELECT count(*) FROM analysis.bout")) >= 1
    assert int(psql(env, "SELECT count(*) FROM archive.range")) >= 1
    assert (data / "core.alive").exists()
    assert list((data / "archive").rglob("*.bin"))
