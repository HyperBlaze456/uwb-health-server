"""진짜 gaitfusion 전 구간 시험 (M3-9, M8-5): 진짜 분석 서비스 이미지로 받기 → 구간 → 분석 → 보관·특징 풀기 → 앱 API,
그리고 원본 행이 지워진 뒤 보관본으로 다시 분석해도 같은 걷기 구간과 궤적이 나오는지(AR-8, 7.1절의 seq 빈자리).

gaitfusion 이미지를 먼저 만들고 그 이름을 줍니다:

    (cd ../gaitfusion && docker build -t gaitfusion:local .)
    UWB_E2E=1 UWB_E2E_GAITFUSION_IMAGE=gaitfusion:local uv run pytest tests/e2e/test_real_gaitfusion.py -s

입력은 tests/e2e/data/walk_two_feet.*입니다. gaitfusion 합성 보행으로 만든 양발 기록(약 5분, 걷기 둘, 그 사이 3분 쉼)이고,
만드는 법과 판은 make_walk_two_feet.py와 walk_two_feet.json에 있습니다. 가짜 Head(tools/fake_head.py)의 보행 신호는
형식 시험용이라 진짜 서비스가 걸음으로 알아보지 못하므로 이 파일을 씁니다.
"""

import gzip
import hashlib
import json
import math
import os
import struct
from pathlib import Path

import httpx
import pytest

from tests.e2e.stack import running, send_until_acked, wait_until
from tools import fake_head

IMAGE = os.environ.get("UWB_E2E_GAITFUSION_IMAGE", "")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(os.environ.get("UWB_E2E") != "1" or not IMAGE,
                       reason="UWB_E2E=1과 UWB_E2E_GAITFUSION_IMAGE(진짜 gaitfusion 이미지)가 있을 때만"),
]

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
META = json.loads((DATA / "walk_two_feet.json").read_text())
PROJECT = "uwbe2egf"
PORTS = {"DEV_DB_PORT": "55442", "DEV_INTAKE_PORT": "58011", "DEV_API_PORT": "58010"}
EDGE_TOLERANCE_S = 1.0  # 걷기 구간의 시작·끝이 합성기가 걷게 한 시각에서 벗어나도 되는 폭
ADMIN = ("e2e@example.com", "e2e-password-1")


def load_chunks() -> list[bytes]:
    batch = gzip.decompress((DATA / "walk_two_feet.c1.gz").read_bytes())
    assert hashlib.sha256(batch).hexdigest() == META["sha256"], "walk_two_feet.c1.gz가 walk_two_feet.json과 다름"
    chunks = fake_head.unframe_request(batch)
    assert len(chunks) == META["n_chunks"]
    return chunks


def requests_of(chunks: list[bytes]) -> list[bytes]:
    """C1 한도(묶음 16개, 본문 128 kB) 안의 요청 본문들."""
    out, cur = [], []
    for c in chunks:
        if cur and (len(cur) == fake_head.MAX_REQUEST_CHUNKS
                    or sum(4 + len(x) for x in cur) + 4 + len(c) > fake_head.MAX_REQUEST_BYTES):
            out.append(fake_head.frame_request(cur))
            cur = []
        cur.append(c)
    out.append(fake_head.frame_request(cur))
    return out


def read_trj(data: bytes) -> tuple[tuple, list[tuple[float, ...]]]:
    """TRJ1(7.3절) → ((side, n_channels, rate_hz, n, t0_s), 채널별 값)."""
    magic, side, n_ch, rate, n, _reserved, t0 = struct.unpack_from("<4sBBHIId", data, 0)
    assert magic == b"TRJ1"
    values = struct.unpack_from(f"<{n_ch * n}f", data, 24)
    return (side, n_ch, rate, n, t0), [values[k * n:(k + 1) * n] for k in range(n_ch)]


def same_values(a: tuple[float, ...], b: tuple[float, ...], tol: float) -> bool:
    return all((math.isnan(x) and math.isnan(y)) or abs(x - y) <= tol for x, y in zip(a, b, strict=True))


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    with running(PROJECT, PORTS, tmp_path_factory.mktemp("uwb-data-gf"),
                 extra_files=[HERE / "compose.real-gaitfusion.yaml"], extra_env={"GAITFUSION_IMAGE": IMAGE}) as s:
        yield s


def test_real_gaitfusion(stack):
    s = stack
    token = s.register_device(META["device_mac"])
    with httpx.Client(timeout=30) as client:
        codes = [c for body in requests_of(load_chunks()) for c in send_until_acked(client, s.intake, token, body)]
    assert codes == [0] * META["n_chunks"]

    # 구간이 닫히고(침묵 60초) 분석 → 보관이 끝날 때까지
    wait_until(lambda: s.psql("SELECT count(*) FROM archive.cut") == "1", 300, "first archive.cut")
    [[seg_id, device_id, state, n_chunks, n_missing, sides]] = s.rows(
        "SELECT id, device_id, state, n_chunks, n_missing_chunks, sides FROM segment.segment")
    assert (state, int(n_chunks), int(n_missing), sides) == ("closed", META["n_chunks"], 0, META["sides"])

    [[run1, status, source, sent, version, result_status]] = s.rows(
        "SELECT id, status, input, n_chunks_sent, gaitfusion_version, result_status FROM analysis.run")
    assert (status, source, int(sent), result_status) == ("done", "raw", META["n_chunks"], "complete")
    assert version and not version.startswith("fake"), version

    # 걷기 구간: 합성기가 걷게 한 시각과 맞고, 앵커가 있으니 융합, 양발
    bouts = s.rows(f"""SELECT b.id, extract(epoch FROM b.start_at - r.origin_at), extract(epoch FROM b.end_at - r.origin_at),
                              b.mode, b.sides, b.start_at, b.end_at
                       FROM analysis.bout b JOIN analysis.run r ON r.id = b.run_id
                       WHERE b.run_id = {run1} ORDER BY b.idx""")
    assert len(bouts) == len(META["walks_s"]), bouts
    for (_, start, end, mode, b_sides, *_), (w0, w1) in zip(bouts, META["walks_s"], strict=True):
        assert abs(float(start) - w0) <= EDGE_TOLERANCE_S and abs(float(end) - w1) <= EDGE_TOLERANCE_S, (start, end)
        assert (mode, b_sides) == ("fused", "both")

    # 궤적: 걷기 구간 × 발마다 행과 파일
    traj_paths = [p for [p] in s.rows(
        f"SELECT t.path FROM analysis.trajectory t JOIN analysis.bout b ON b.id = t.bout_id WHERE b.run_id = {run1}")]
    assert len(traj_paths) == 2 * len(bouts)
    first = {Path(p).name: (s.data / p.removeprefix("/data/")).read_bytes() for p in traj_paths}

    # 보관: 걷기 구간마다 범위 하나(둘 사이가 3분이라 합쳐지지 않음), 파일이 있음
    ranges = [tuple(map(int, r)) for r in s.rows(
        f"SELECT first_seq, last_seq, n_chunks FROM archive.range WHERE segment_id = {seg_id} ORDER BY first_seq")]
    assert len(ranges) == len(bouts), ranges
    assert ranges[0][0] == 0, ranges  # 첫 걷기가 15초에 시작해 앞 여유 15초가 첫 묶음에 닿음: 보관본의 시각 0도 같음
    assert ranges[1][0] - ranges[0][1] > 60, ranges  # 보관본 재분석 때 seq 빈자리가 수 분
    assert len(list((s.data / "archive").rglob("*.bin"))) == len(ranges)

    # M8-5: 특징 풀기. 그 run의 보폭 줄이 걷기 구간마다 있고, 지표 목록으로 본 틀린 값과 모르는 필드·표가 없음
    wait_until(lambda: s.psql(f"SELECT count(*) FROM feature.unpack WHERE run_id = {run1}") == "1", 120,
               "feature.unpack")  # fmt: skip
    [[n_records, n_dropped, n_bad, unknown_fields, unknown_tables]] = s.rows(
        f"""SELECT n_records, n_dropped_records, n_bad_values, unknown_fields, unknown_tables
            FROM feature.unpack WHERE run_id = {run1}""")
    assert (int(n_dropped), int(n_bad), unknown_fields, unknown_tables) == (0, 0, "{}", "{}")
    strides_per_bout = s.rows(f"""SELECT b.idx, count(r.id) FROM analysis.bout b
                                  LEFT JOIN feature.record r ON r.bout_id = b.id AND r.kind = 'stride'
                                  WHERE b.run_id = {run1} GROUP BY b.idx ORDER BY b.idx""")
    assert all(int(n) > 0 for _, n in strides_per_bout), strides_per_bout
    assert sum(int(n) for _, n in strides_per_bout) == int(n_records)

    # 앱 API: 걷기 구간의 궤적(양발)과 응답 원문
    s.create_admin(*ADMIN)
    auth = s.login(*ADMIN)
    r = httpx.get(f"{s.api}/v1/bouts/{bouts[0][0]}/trajectory", headers=auth, timeout=10)
    assert r.status_code == 200, r.text
    assert sorted(t["side"] for t in r.json()["trajectories"]) == ["left", "right"]
    assert all(t["n"] > 0 for t in r.json()["trajectories"])
    r = httpx.get(f"{s.api}/v1/runs/{run1}/result", headers=auth, timeout=10)
    assert r.status_code == 200 and len(r.json()["bouts"]) == len(bouts)
    r = httpx.get(f"{s.api}/v1/bouts/{bouts[0][0]}/records", headers=auth, timeout=10)
    assert r.status_code == 200 and r.json()["records"], r.text
    assert all(len(rec["values"]) == 9 for rec in r.json()["records"])  # 융합이라 uwb_support까지 9개 모두

    # 30일 삭제(AR-6)가 지우는 원본 행을 지움. 30일을 기다릴 수 없어 같은 순서로 직접 지움. 보관 파일은 그대로
    s.psql(f"""BEGIN;
               DELETE FROM intake.health WHERE device_id = '{device_id}';
               DELETE FROM intake.system WHERE device_id = '{device_id}';
               DELETE FROM intake.chunk WHERE device_id = '{device_id}';
               COMMIT;""")
    out = s.compose("exec", "-T", "api", "python", "tools/admin.py", "reanalyze", seg_id).stdout
    assert "queued" in out, out
    wait_until(lambda: s.psql("SELECT count(*) FROM archive.cut") == "2", 300, "archive.cut after reanalysis")

    # AN-4: 보관본으로 분석. 같은 걷기 구간(시각까지 같음)
    [[run2, status, source, sent, result_status]] = s.rows(
        f"SELECT id, status, input, n_chunks_sent, result_status FROM analysis.run WHERE id <> {run1}")
    assert (status, source, int(sent), result_status) == ("done", "archive", sum(n for *_, n in ranges), "complete")
    again = s.rows(f"SELECT start_at, end_at, mode, sides FROM analysis.bout WHERE run_id = {run2} ORDER BY idx")
    assert again == [[b[5], b[6], b[3], b[4]] for b in bouts]

    # 궤적: 옛 run 것은 지워지고(캐시, AN-9) 새 run 것이 원본으로 만든 것과 같음
    assert not list((s.data / "trajectory" / run1).glob("*.trj"))
    second = {p.name: p.read_bytes() for p in (s.data / "trajectory" / run2).glob("*.trj")}
    assert sorted(second) == sorted(first)
    for name, data in first.items():
        (head1, ch1), (head2, ch2) = read_trj(data), read_trj(second[name])
        assert head1 == head2, name  # 시각 0이 같은 묶음(보관 범위가 seq 0부터)이라 t0_s까지 같음
        assert all(same_values(a, b, 1e-5) for a, b in zip(ch1, ch2, strict=True)), name
