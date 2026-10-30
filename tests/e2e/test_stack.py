"""전 구간 시험 (M6-1, M1-7): docker compose로 전부 띄우고 HTTP로 받기 → 구간 → 분석까지. 분석은 가짜 gaitfusion.

이미지를 빌드하므로 기본으로는 건너뜁니다:  UWB_E2E=1 uv run pytest tests/e2e -s
진짜 gaitfusion으로 도는 시험은 test_real_gaitfusion.py입니다.
"""

import os
import subprocess
import threading
import time

import httpx
import pytest

from tests.e2e.stack import running, send_until_acked
from tools import fake_head

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(os.environ.get("UWB_E2E") != "1", reason="UWB_E2E=1일 때만(이미지 빌드)"),
]

PROJECT = "uwbe2e"
PORTS = {"DEV_DB_PORT": "55432", "DEV_INTAKE_PORT": "58001", "DEV_API_PORT": "58000"}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with running(PROJECT, PORTS, tmp_path_factory.mktemp("uwb-data")) as s:
        yield s


def test_whole_stack(stack):
    s = stack
    token = s.register_device(fake_head.TEST_DEVICE_MAC)
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
        s.compose("up", "-d", "db", "intake", check=False)  # 재시작(intake는 받기 전에 재색인)

    k = threading.Thread(target=killer, daemon=True)
    k.start()
    with httpx.Client(timeout=10) as client:
        for i, r in enumerate(reqs):
            if i == len(reqs) // 3:
                go.set()  # 보내는 도중에 죽임: 다음 요청들이 날아가는 사이에 intake와 db가 kill -9
            codes = send_until_acked(client, s.intake, token, r.body)
            acked |= {k_ for k_, c in zip(r.keys, codes, strict=True) if c in (0, 1)}
    k.join(timeout=120)
    assert killed.is_set()

    # M1-7: 0·1을 받은 모든 키가 intake.chunk에 있음(재시작 때 재색인, IN-9)
    stored = {(int(b), int(q)) for b, q in s.rows("SELECT head_boot_id, chunk_seq FROM intake.chunk")}
    assert acked <= stored, sorted(acked - stored)[:10]
    assert len(acked) == sum(len(r.keys) for r in reqs)

    # core: 구간 → 침묵 60초 → 분석 → 보관
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        if s.psql("SELECT count(*) FROM archive.cut") not in ("", "0"):
            break
        time.sleep(5)
    seg = s.psql("SELECT state || ',' || n_chunks FROM segment.segment ORDER BY id LIMIT 1")
    assert seg.startswith("closed,"), seg
    run = s.psql("SELECT status FROM analysis.run ORDER BY id LIMIT 1")
    assert run == "done"
    assert int(s.psql("SELECT count(*) FROM analysis.bout")) >= 1
    assert int(s.psql("SELECT count(*) FROM archive.range")) >= 1
    assert (s.data / "core.alive").exists()
    assert list((s.data / "archive").rglob("*.bin"))
