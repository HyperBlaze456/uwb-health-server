"""M1-1 요청 벡터: request_<이름>.bin을 받기에 보내 HTTP 상태와 codes가 .json과 같은지 (IN-1, IN-3).

펌웨어가 tests/wire_vectors/를 만들면 tests/contract/wire_vectors/에 VERSION과 함께 복사합니다(12절 1번).
그때까지 건너뜁니다. 시험 장치 MAC은 wire.md대로 e0:72:a1:ac:a8:58.
"""

import json
from pathlib import Path

import pytest

from tests.support import auth

VECTORS = Path(__file__).parent / "wire_vectors"
REQUESTS = sorted(VECTORS.glob("request_*.bin")) if VECTORS.exists() else []


@pytest.mark.db
@pytest.mark.skipif(not REQUESTS, reason="펌웨어 요청 벡터가 아직 없음")
@pytest.mark.parametrize("path", REQUESTS, ids=[p.stem for p in REQUESTS])
def test_request_vector(env, device, intake_client, path):
    import time

    from uwbserver.pipeline import wire

    expected = json.loads(path.with_suffix(".json").read_text())
    body = path.read_bytes()
    # 벡터 묶음의 utc_us_start가 서버 시각 + 1일 검사에 걸리지 않게, 벡터에 적힌 시각이 있으면 그때로
    if "server_utc_us" in expected:
        env.clock.set(int(expected["server_utc_us"]))
    else:
        env.clock.set(time.time_ns() // 1000)  # 실제 지금(벡터를 만든 시각 근처)
    resp = intake_client.post("/d/v1/chunks", content=body, headers=auth(device.token))
    assert resp.status_code == expected["http_status"]
    if resp.status_code == 200:
        assert wire.decode_ack(resp.content)[1] == expected["codes"]
