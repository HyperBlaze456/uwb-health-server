"""시험 도우미: 장치 등록, 묶음 보내기, core 단계 돌리기."""

from collections.abc import Iterable
from dataclasses import dataclass

import psycopg

from tools import fake_head
from uwbserver.intake import run as intake_run
from uwbserver.pipeline import clock
from uwbserver.registry import rules as registry_rules
from uwbserver.registry import store as registry_store
from uwbserver.registry.store import Device


@dataclass
class Registered:
    device: Device
    token: str


def register(conn: psycopg.Connection, mac: str = fake_head.TEST_DEVICE_MAC) -> Registered:
    """RG-1과 같은 길: id = head-<mac>, 토큰은 해시만 저장."""
    token = registry_rules.new_token()
    did = registry_rules.device_id(mac)
    with conn.transaction():
        registry_store.insert_device(
            conn, device_id=did, mac=registry_rules.normalize_mac(mac),
            token_hash=registry_rules.token_hash(token), now=clock.now(), note=None,
        )  # fmt: skip
    return Registered(Device(did, registry_rules.normalize_mac(mac)), token)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}


class Feeder:
    """fake_head 요청을 시각대로 받기에 넣고, 5초마다 segment 단계를 돌립니다(core 흉내).

    HTTP를 거치지 않고 intake.run.receive를 바로 부릅니다(HTTP는 M1 시험이 따로 봄).
    """

    def __init__(self, env, devices: dict[str, Registered], *, step_every_s: float | None = 5.0) -> None:
        self.env = env
        self.devices = devices
        self.intake = env.conn("intake")
        self.core = env.conn("core")
        self.step_every_us = int(step_every_s * 1_000_000) if step_every_s else None
        self.next_step_us: int | None = None
        self.replies: list[intake_run.Reply] = []

    def _steps_until(self, t_us: int) -> None:
        if self.step_every_us is None:
            return
        if self.next_step_us is None:
            self.next_step_us = t_us
        from uwbserver.segment import run as segment_run

        while self.next_step_us <= t_us:
            self.env.clock.set(self.next_step_us)
            segment_run.step(self.core)
            self.next_step_us += self.step_every_us

    def send(self, requests: Iterable[fake_head.Request], mac: str = fake_head.TEST_DEVICE_MAC) -> list[intake_run.Reply]:
        reg = self.devices[mac]
        out = []
        for r in requests:
            self._steps_until(r.at_us)
            self.env.clock.set(r.at_us)
            reply = intake_run.receive(self.intake, reg.device, r.body)
            out.append(reply)
        self.replies.extend(out)
        return out

    def idle(self, seconds: float) -> None:
        """수신 없이 시간을 흘리며 5초마다 segment를 돌림."""
        end = self.env.clock.now_us() + int(seconds * 1_000_000)
        self._steps_until(end)
        self.env.clock.set(end)

    def step(self):
        from uwbserver.segment import run as segment_run

        return segment_run.step(self.core)


def fake_c2(mode: str = "ok"):
    """가짜 gaitfusion(TL-2)을 프로세스 안에서 띄운 C2 클라이언트."""
    from fastapi.testclient import TestClient

    from tools import fake_gaitfusion
    from uwbserver.analysis import gaitfusion

    http = TestClient(fake_gaitfusion.create_app(mode=mode), base_url="http://gaitfusion")
    return gaitfusion.Client("http://gaitfusion", 600.0, http=http)


def make_core(mode: str = "ok"):
    from uwbserver.process.core import Core

    core = Core(client=fake_c2(mode))
    core._check_c2()
    assert core.c2_ready.is_set()
    return core


def closed_segment(env, device, name: str = "normal", **opts) -> int:
    """시나리오를 넣고 침묵으로 닫힌 구간 하나의 id."""
    f = Feeder(env, {fake_head.TEST_DEVICE_MAC: device})
    f.send(fake_head.scenario(name, **opts))
    f.idle(120)
    rows = env.q("SELECT id FROM segment.segment WHERE state = 'closed' ORDER BY id")
    assert rows, "닫힌 구간이 없음"
    return rows[0]["id"]


def run_jobs(env, core, kinds=("analysis", "archive"), limit: int = 50) -> list[str]:
    """대기 작업을 다 처리합니다(run_after가 지난 것만)."""
    conn = env.conn("core")
    out = []
    for _ in range(limit):
        r = core.work_once(conn, kinds, "test")
        if r is None:
            break
        out.append(r)
    return out
