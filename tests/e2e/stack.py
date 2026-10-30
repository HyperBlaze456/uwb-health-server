"""전 구간 시험(tests/e2e)의 공용 부분: docker compose로 스택을 띄우고 내리기, DB 조회, 장치·관리자 만들기,
Head처럼 확인을 받을 때까지 다시 보내기.

시험마다 프로젝트 이름과 호스트 포트를 따로 써서 개발 DB 옆에서, 그리고 서로 겹치지 않게 돕니다.
"""

import os
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import httpx

from tools import fake_head

ROOT = Path(__file__).resolve().parents[2]
DATA_DIRS = ("raw", "rejected", "archive", "result", "trajectory")


def dotenv() -> dict[str, str]:
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


def wait_until(check: Callable[[], bool], timeout_s: float, what: str, every_s: float = 3) -> None:
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() > deadline:
            raise TimeoutError(what)
        time.sleep(every_s)


class Stack:
    """compose 프로젝트 하나. ports: DEV_DB_PORT·DEV_INTAKE_PORT·DEV_API_PORT. data: 호스트의 DATA_DIR."""

    def __init__(self, project: str, ports: dict[str, str], data: Path, *,
                 extra_files: Sequence[Path] = (), extra_env: dict[str, str] | None = None) -> None:
        self.project = project
        self.data = data
        self.files = [ROOT / "compose.yaml", ROOT / "compose.dev.yaml", *extra_files]
        self.env = {**os.environ, **ports, "DATA_DIR": str(data), **(extra_env or {})}
        self.intake = f"http://localhost:{ports['DEV_INTAKE_PORT']}"
        self.api = f"http://localhost:{ports['DEV_API_PORT']}"

    def compose(self, *args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
        cmd = ["docker", "compose", "-p", self.project]
        for f in self.files:
            cmd += ["-f", str(f)]
        return subprocess.run([*cmd, *args], cwd=ROOT, env=self.env, check=check, capture_output=capture, text=True)

    def psql(self, sql: str) -> str:
        """DB 주인으로 SQL 하나. 결과는 `-At` 글자(열은 |, 행은 줄바꿈)."""
        owner = dotenv().get("DB_OWNER", "uwb")
        r = self.compose("exec", "-T", "db", "psql", "-U", owner, "-d", "uwb", "-At", "-v", "ON_ERROR_STOP=1",
                         "-c", sql)
        return r.stdout.strip()

    def rows(self, sql: str) -> list[list[str]]:
        out = self.psql(sql)
        return [line.split("|") for line in out.splitlines()] if out else []

    def register_device(self, mac: str) -> str:
        r = self.compose("exec", "-T", "api", "python", "tools/admin.py", "register-device", mac)
        return r.stdout.split("token")[1].split()[0]

    def create_admin(self, email: str, password: str) -> None:
        self.compose("exec", "-T", "-e", f"ADMIN_PASSWORD={password}", "api", "python", "tools/admin.py",
                     "create-admin", email)

    def login(self, email: str, password: str) -> dict[str, str]:
        r = httpx.post(f"{self.api}/v1/auth/token", json={"email": email, "password": password}, timeout=10)
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['token']}"}


@contextmanager
def running(project: str, ports: dict[str, str], data: Path, *, extra_files: Sequence[Path] = (),
            extra_env: dict[str, str] | None = None) -> Iterator[Stack]:
    """빈 DATA_DIR로 db·intake·api·core·gaitfusion을 띄우고, 끝나면 볼륨까지 지움(UWB_E2E_KEEP=1이면 남김)."""
    for d in DATA_DIRS:
        (data / d).mkdir(parents=True, exist_ok=True)
        os.chmod(data / d, 0o777)  # 컨테이너는 UID 10001
    os.chmod(data, 0o777)
    s = Stack(project, ports, data, extra_files=extra_files, extra_env=extra_env)
    s.compose("up", "-d", "--build", "--wait", "db", "intake", "api", "core", "gaitfusion", capture=False)
    try:
        wait_ok(f"{s.intake}/healthz")
        wait_ok(f"{s.api}/healthz")
        yield s
    finally:
        if os.environ.get("UWB_E2E_KEEP") != "1":
            s.compose("down", "-v", "--remove-orphans", check=False)


def send_until_acked(client: httpx.Client, intake: str, token: str, body: bytes, timeout_s: float = 180) -> list[int]:
    """Head처럼: 확인을 못 받으면 다시 보냄(wire.md 다시 보내기 규칙). 돌려주는 값: 묶음마다 결과 코드."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            r = client.post(f"{intake}/d/v1/chunks", content=body,
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"})
            if r.status_code == 200:
                return fake_head.decode_ack(r.content)[1]
            assert r.status_code >= 500, r.status_code
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError("intake did not come back")
        time.sleep(0.5)
