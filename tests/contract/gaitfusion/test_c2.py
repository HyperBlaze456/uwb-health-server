"""C2 계약 시험 (SPEC 11절 M3 시험 7, 7절).

- 요청: 파트 순서와 context 필드(7.1절).
- 응답 검사: 7.2·7.3절 규칙마다 하나씩 어긴 응답이 그 규칙으로 거절되고, 맞는 응답은 통과.
- 클라이언트의 400·422·5xx·시간 초과 처리, multipart 부호기·해독기, TRJ1 읽기.

환경 변수 GAITFUSION_URL이 있으면 `service` 시험(버전, 맞는 응답, 400, 422)을 그 주소의 진짜 gaitfusion으로도
돌립니다. 없으면 같은 프로세스의 가짜(tools/fake_gaitfusion.py)로 돌립니다. 가짜에만 있는 모드 시험은 언제나 가짜입니다.
"""

import json
import math
import os
import struct
import threading
import time
import zlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from tools import fake_gaitfusion as fake
from uwbserver.analysis import rules
from uwbserver.analysis.gaitfusion import AnalyzeReply, Client, Part, decode_multipart, encode_multipart
from uwbserver.pipeline import wire

ORIGIN = 81_234_567_890  # origin_head_us (7.1절 예)
BOOT = 0x1234
ANCHORS = [
    {"id": 0, "xyz_m": [0.0, 0.0, 1.2], "bias_m": 0.0},
    {"id": 1, "xyz_m": [4.1, 0.0, 1.2], "bias_m": 0.0},
    {"id": 2, "xyz_m": [3.9, 3.2, 1.2], "bias_m": 0.0},
    {"id": 3, "xyz_m": [0.1, 3.1, 0.4], "bias_m": 0.0},
]

# --- C1 묶음 만들기 (wire.md 배치를 손으로 묶음. 서버 해독기와 따로) -----------------

_HDR = struct.Struct("<BBHIIqqIHHHHHHHH")
_RANGE = struct.Struct("<IIBBBB4h4b")
_IMU = struct.Struct("<I6sIHIbBBB")
_SAMPLE = struct.Struct("<HIB7h")
IMU_FRAMES = 2  # 발마다 묶음 하나에 IMU 프레임 수(프레임마다 표본 10개)


def c1_chunk(
    seq: int, *, t_s: float | None = None, imu: tuple[int, ...] = (0,), ranges: tuple[int, ...] = (0,)
) -> bytes:
    """맞는 C1 묶음 하나. 시각 = ORIGIN + t_s초(기본 seq초). imu·ranges = 프레임이 있는 tag_id들."""
    head_us = ORIGIN + round((seq if t_s is None else t_s) * 1_000_000)
    rng = b"".join(
        _RANGE.pack(20_000 * k, seq, 0x0F, 0xFF, 0x0F, tag, 150, 200, 250, 300, -70, -71, -72, -73)
        for k, tag in enumerate(ranges)
    )
    frames = b""
    k = 0
    for j in range(IMU_FRAMES):
        for tag in imu:
            frames += _IMU.pack(
                100_000 * j + k, bytes([2, 0, 0, 0, 0x10, tag]), 7, seq * 4 + j, seq * 1000, -60, 0x11, tag, 10
            )
            frames += b"".join(
                _SAMPLE.pack((seq * 40 + j * 10 + s) & 0xFFFF, seq * 1000 + s * 10, 0, 10, 20, 4096, 25, 1, 2, 3)
                for s in range(10)
            )
            k += 1
    mask = 0
    for tag in (*imu, *ranges):
        mask |= 1 << tag
    head = _HDR.pack(0xC1, 1, 48, BOOT, seq, head_us, 0, 0, 0, len(ranges), len(imu) * IMU_FRAMES, 0, 0, mask, 0, 0)
    data = head + rng + frames
    data += struct.pack("<I", zlib.crc32(data))
    assert isinstance(wire.check_chunk(data, device_mac=bytes([2, 0, 0, 0, 0, 1]), server_utc_us=0), wire.Chunk)
    return data


def walking_left() -> list[bytes]:
    """왼발 하나. 걷기 [3, 15)와 [24, 35), 그 사이 5초 IMU(짧아서 걷기 아님)와 seq 빈자리 22–23."""
    out = []
    for seq in range(35):
        if seq in (22, 23):
            continue
        imu = (0,) if 3 <= seq < 15 or 17 <= seq < 22 or seq >= 24 else ()
        out.append(c1_chunk(seq, t_s=30.02 if seq == 30 else None, imu=imu))  # seq 30은 20 ms 늦음(±50 ms 안)
    return out


def walking_both(n: int = 12) -> list[bytes]:
    """두 발, 처음부터 n초 걷기."""
    return [c1_chunk(seq, imu=(0, 1), ranges=(0, 1)) for seq in range(n)]


def context(
    sides: str = "left", anchors: list | None = None, *, origin: int = ORIGIN, params: dict | None = None
) -> dict:
    return rules.build_context(
        segment_id=1234, run_id=5678, sides=sides, origin_head_us=origin, anchors=anchors,
        subject={"height_m": 1.72, "leg_length_m": 0.9, "paretic_side": None}, params=params or {},
    )  # fmt: skip


def t_last_of(chunks: list[bytes]) -> float:
    return rules.t_last_s(ORIGIN, wire.decode_chunk(chunks[-1]).header.head_us_start)


def fake_client(mode: str = "ok") -> Client:
    return Client("http://testserver", 30, http=TestClient(fake.create_app(mode)))


@pytest.fixture
def service():
    """(클라이언트, 가짜인지). GAITFUSION_URL이 있으면 진짜 서비스."""
    url = os.environ.get("GAITFUSION_URL")
    if url:
        with Client(url, float(os.environ.get("GAITFUSION_TIMEOUT_S", "120"))) as c:
            yield c, False
    else:
        yield fake_client(), True


# --- multipart --------------------------------------------------------------------


def test_multipart_round_trip_keeps_order_and_duplicates():
    parts = [
        Part("context", "application/json", b'{"a":1}'),
        Part("chunks", "application/octet-stream", b"\r\n--\r\n\r\n--x--\r\n" + bytes(range(256))),
        Part("chunks", "application/octet-stream", b""),
        Part("궤적", "text/plain; charset=utf-8", "한글".encode()),
    ]
    ctype, body = encode_multipart(parts)
    assert ctype.startswith("multipart/form-data; boundary=")
    assert decode_multipart(ctype, body) == parts


def test_multipart_given_boundary_must_not_occur_in_data():
    with pytest.raises(ValueError):
        encode_multipart([Part("a", "text/plain", b"xx--abc--")], boundary="abc")
    with pytest.raises(ValueError):
        encode_multipart([Part("a", "text/plain", b"x")], boundary="bad boundary ")


def test_multipart_decode_accepts_other_writers():
    body = (
        b"preamble ignored\r\n"
        b"--b 1  \r\n"  # 경계 뒤 공백(transport padding)
        b'content-disposition: form-data; name="result"; filename="r.json"\r\n'
        b"CONTENT-TYPE: application/json\r\n\r\n"
        b'{"x":1}\r\n'
        b"--b 1\r\n"
        b'Content-Disposition: form-data; name="a\\"b"\r\n\r\n'
        b"plain\r\n"
        b"--b 1--\r\nepilogue ignored"
    )
    parts = decode_multipart('Multipart/Form-Data; charset=utf-8; boundary="b 1"', body)
    assert parts == [Part("result", "application/json", b'{"x":1}'), Part('a"b', "text/plain", b"plain")]


@pytest.mark.parametrize(
    ("ctype", "body"),
    [
        ("application/json", b"{}"),
        ("multipart/form-data", b"--x\r\n\r\n--x--\r\n"),  # 경계 없음
        ("multipart/form-data; boundary=x", b"no boundary here"),
        (
            "multipart/form-data; boundary=x",
            b'--x\r\nContent-Disposition: form-data; name="a"\r\n\r\ndata',
        ),  # 닫힘 없음
        ("multipart/form-data; boundary=x", b'--x\r\nContent-Disposition: form-data; name="a"\r\n\r\ndata\r\n--x'),
        ("multipart/form-data; boundary=x", b"--x\r\nContent-Type: text/plain\r\n\r\ndata\r\n--x--\r\n"),  # 이름 없음
        ("multipart/form-data; boundary=x", b"--x\r\nno colon line\r\n\r\ndata\r\n--x--\r\n"),
        (
            "multipart/form-data; boundary=x",
            b'--xjunk\r\nContent-Disposition: form-data; name="a"\r\n\r\nd\r\n--x--\r\n',
        ),
        ("multipart/form-data; boundary=x", b'--x\r\nContent-Disposition: form-data; name="a"'),  # 머리 끝 없음
    ],
)
def test_multipart_decode_rejects_malformed(ctype, body):
    with pytest.raises(ValueError):
        decode_multipart(ctype, body)


# --- 요청: 문맥과 파트 (7.1절, AN-5) ------------------------------------------------


def test_build_context_has_exactly_the_c2_keys():
    subject_row = {"id": 9, "code": "S01", "height_m": 1.72, "leg_length_m": None}  # paretic_side 없음 → null
    params = {"bout_pad_s": 2.0}
    ctx = rules.build_context(
        segment_id=1234, run_id=5678, sides="both", origin_head_us=ORIGIN,
        anchors=[{**a, "extra": 1} for a in ANCHORS], subject=subject_row, params=params,
    )  # fmt: skip
    assert ctx == {
        "c2_version": 1,
        "segment_id": 1234,
        "run_id": 5678,
        "origin_head_us": ORIGIN,
        "sides": "both",
        "anchors": ANCHORS,
        "subject": {"height_m": 1.72, "leg_length_m": None, "paretic_side": None},
        "params": {"bout_pad_s": 2.0},
    }
    ctx["params"]["x"] = 1
    assert params == {"bout_pad_s": 2.0}  # 복사본
    imu_only = rules.build_context(
        segment_id=1, run_id=2, sides="left", origin_head_us=0, anchors=None, subject=None, params={}
    )
    assert imu_only["anchors"] is None and imu_only["subject"] is None and imu_only["params"] == {}
    with pytest.raises(ValueError):
        rules.build_context(segment_id=1, run_id=2, sides="up", origin_head_us=0, anchors=None, subject=None, params={})


def test_t_last_is_end_of_sent_data():
    assert rules.t_last_s(ORIGIN, ORIGIN) == 1.0
    assert rules.t_last_s(ORIGIN, ORIGIN + 34_000_000) == 35.0
    with pytest.raises(ValueError):
        rules.t_last_s(ORIGIN, ORIGIN - 1)


def test_request_parts_order_and_context_fields():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(422, json={"reason": "x"})

    chunks = walking_left()
    body = wire.join_records(chunks)
    ctx = context("left", ANCHORS, params={"bout_pad_s": 2.0})
    with Client("http://gf:8100/", 5, http=httpx.Client(transport=httpx.MockTransport(handler))) as c:
        c.analyze(ctx, body)
    (req,) = seen
    assert req.method == "POST" and str(req.url) == "http://gf:8100/v1/analyze"
    parts = decode_multipart(req.headers["content-type"], req.content)
    assert [(p.name, p.content_type) for p in parts] == [
        ("context", "application/json"),
        ("chunks", "application/octet-stream"),
    ]
    assert json.loads(parts[0].data) == ctx
    assert list(json.loads(parts[0].data)) == [
        "c2_version", "segment_id", "run_id", "origin_head_us", "sides", "anchors", "subject", "params",
    ]  # fmt: skip
    assert parts[1].data == body
    assert wire.split_request(parts[1].data, max_chunks=None) == chunks  # C1 레코드 그대로, 16개 한도 없음


def test_request_refuses_nan_in_context():
    c = Client("http://gf", 5, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    ctx = context()
    ctx["subject"]["height_m"] = math.nan
    with pytest.raises(ValueError):
        c.analyze(ctx, b"")


# --- 클라이언트: 응답 코드 (7.4절, AN-7) -----------------------------------------------


def _mock(response: httpx.Response | Exception) -> Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    return Client("http://gf", 5, http=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize(
    ("response", "want"),
    [
        (httpx.Response(400, json={"reason": "crc", "chunk_index": 3}), AnalyzeReply(400, None, "crc", 3)),
        (
            httpx.Response(400, json={"reason": "framing", "chunk_index": None}),
            AnalyzeReply(400, None, "framing", None),
        ),
        (httpx.Response(400, text="bad request"), AnalyzeReply(400, None, "bad request", None)),
        (httpx.Response(422, json={"reason": "no_imu"}), AnalyzeReply(422, None, "no_imu", None)),
        (httpx.Response(422, content=b'{"reason": ""}'), AnalyzeReply(422, None, '{"reason": ""}', None)),
        (httpx.Response(422), AnalyzeReply(422, None, "http 422", None)),
        (httpx.Response(500, json={"reason": "boom"}), AnalyzeReply(500, None, None, None)),
        (httpx.Response(503), AnalyzeReply(503, None, None, None)),
    ],
)
def test_client_error_replies(response, want):
    assert _mock(response).analyze(context(), b"x") == want


def test_client_undecodable_200_is_rejected_by_check():
    reply = _mock(httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})).analyze(
        context(), b"x"
    )
    assert reply.status == 200 and reply.parts is None and reply.reason.startswith("multipart")
    with pytest.raises(rules.ResponseInvalid) as e:
        rules.check_response(reply.parts, segment_sides="left", anchors_sent=False, t_last=10.0)
    assert e.value.rule == "multipart"


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
def test_client_timeouts_and_connection_errors_propagate(exc):
    with pytest.raises(type(exc)):
        _mock(exc).analyze(context(), b"x")


def test_client_version():
    assert (
        _mock(httpx.Response(200, json={"gaitfusion_version": "1.3.0", "c2_version": 1})).version()["c2_version"] == 1
    )
    with pytest.raises(httpx.HTTPStatusError):
        _mock(httpx.Response(503)).version()
    with pytest.raises(ValueError):
        _mock(httpx.Response(200, json=[1])).version()


# --- 서비스 (진짜 또는 가짜) ------------------------------------------------------------


def test_service_version(service):
    client, _ = service
    v = client.version()
    assert v["c2_version"] == 1
    assert isinstance(v["gaitfusion_version"], str) and v["gaitfusion_version"]


def test_service_good_response_passes_checks(service):
    client, is_fake = service
    chunks = walking_left()
    ctx = context("left", None)
    reply = client.analyze(ctx, wire.join_records(chunks))
    assert reply.status == 200, reply
    t_last = t_last_of(chunks)
    assert t_last == 35.0
    checked = rules.check_response(reply.parts, segment_sides="left", anchors_sent=False, t_last=t_last)
    if not is_fake:
        return
    # 가짜의 걷기 구간 규칙(TL-2): IMU가 이어진 10초 이상만, 궤적은 ± 2초를 [0, t_last]로 자름
    assert [(b.idx, b.start_s, b.end_s, b.mode, b.sides) for b in checked.bouts] == [
        (0, 3.0, 15.0, "imu_only", "left"),
        (1, 24.0, 35.0, "imu_only", "left"),
    ]
    assert [b.trajectories for b in checked.bouts] == [(("left", "traj-0-left"),), (("left", "traj-1-left"),)]
    t0 = checked.trajectories["traj-0-left"]
    t1 = checked.trajectories["traj-1-left"]
    assert (t0.side, t0.rate_hz, t0.t0_s, t0.n, t0.end_s) == ("left", 100, 1.0, 1601, 17.0)
    assert (t1.t0_s, t1.n, t1.end_s, t1.nbytes) == (22.0, 1301, 35.0, 28 + 52 * 1301)
    assert checked.gaitfusion_version == "fake-1.0"
    n_imu_chunks = 12 + 5 + 11
    assert checked.summary == {
        "n_imu": n_imu_chunks * IMU_FRAMES * 10, "n_range": len(chunks), "seq_gaps": 1,
        "sides": ["left"], "sensor_site": "lateral_ankle",
    }  # fmt: skip
    raw = json.loads(checked.result_bytes)  # 표에 없는 필드는 원문에 그대로(AN-10)
    assert all(raw["bouts"][0][k] == [] for k in ("strides", "turns", "steps", "windows", "events"))
    assert raw["bouts"][0]["quality"] == {"uwb_accept_ratio": None}
    data = {p.name: p.data for p in reply.parts}["traj-0-left"]
    ch = rules.read_channels(data, ["stance", "sig_px"], step=4)
    assert set(ch["stance"]) == {0.0, 1.0} and len(ch["stance"]) == rules.stepped_n(1601, 4) == 401
    assert ch["sig_px"][0] is None and all(v is not None for v in ch["sig_px"][1:])  # 첫 표본 NaN → None


def test_service_bad_chunk_is_400_with_index(service):
    client, _ = service
    chunks = walking_both()
    bad = bytearray(chunks[3])
    bad[-1] ^= 0xFF  # CRC
    chunks[3] = bytes(bad)
    reply = client.analyze(context("both", ANCHORS), wire.join_records(chunks))
    assert (reply.status, reply.chunk_index) == (400, 3)
    assert reply.reason


def test_service_no_imu_is_422(service):
    client, is_fake = service
    chunks = [c1_chunk(seq, imu=()) for seq in range(15)]
    reply = client.analyze(context("left", ANCHORS), wire.join_records(chunks))
    assert reply.status == 422 and reply.parts is None and reply.reason
    if is_fake:
        assert reply.reason == "no_imu"


# --- 가짜에만 있는 것 ------------------------------------------------------------------


def test_fake_two_feet_fused_trajectory_clipped_at_zero():
    chunks = walking_both(12)
    reply = fake_client().analyze(context("both", ANCHORS), wire.join_records(chunks))
    checked = rules.check_response(reply.parts, segment_sides="both", anchors_sent=True, t_last=12.0)
    (b,) = checked.bouts
    assert (b.start_s, b.end_s, b.mode, b.sides, b.quality) == (0.0, 12.0, "fused", "both", {"uwb_accept_ratio": 1.0})
    assert b.trajectories == (("left", "traj-0-left"), ("right", "traj-0-right"))
    for name in ("traj-0-left", "traj-0-right"):
        trj = checked.trajectories[name]
        assert (trj.t0_s, trj.n, trj.end_s) == (0.0, 1201, 12.0)
    assert [p.name for p in reply.parts] == ["result", "traj-0-left", "traj-0-right"]


def test_fake_is_deterministic():
    tc = TestClient(fake.create_app("ok"))
    ctype, body = encode_multipart(
        [
            Part("context", "application/json", json.dumps(context("both", ANCHORS)).encode()),
            Part("chunks", "application/octet-stream", wire.join_records(walking_both())),
        ]
    )
    a = tc.post("/v1/analyze", content=body, headers={"content-type": ctype})
    b = tc.post("/v1/analyze", content=body, headers={"content-type": ctype})
    assert a.status_code == 200 and a.content == b.content and a.headers["content-type"] == b.headers["content-type"]


@pytest.mark.parametrize(
    ("ctx", "chunks", "index"),
    [
        (context("left"), walking_both(3), 0),  # 오른발 프레임인데 sides = left
        (context("left", origin=ORIGIN + 1), walking_left()[:3], 0),  # origin ≠ 첫 묶음
        (context("left"), [c1_chunk(1), c1_chunk(0)], 1),  # seq 순서
    ],
)
def test_fake_rejects_request_that_breaks_c2(ctx, chunks, index):
    reply = fake_client().analyze(ctx, wire.join_records(chunks))
    assert (reply.status, reply.chunk_index) == (400, index), reply


def test_fake_rejects_bad_context():
    ctx = context()
    ctx["extra"] = 1
    reply = fake_client().analyze(ctx, wire.join_records(walking_left()[:3]))
    assert reply.status == 400 and "context" in reply.reason


def test_fake_modes_422_and_500():
    body = wire.join_records(walking_left())
    assert fake_client("422").analyze(context(), body) == AnalyzeReply(422, None, "fake_422", None)
    assert fake_client("500").analyze(context(), body) == AnalyzeReply(500, None, None, None)
    with pytest.raises(ValueError):
        fake.create_app("malformed:nope")


def test_malformed_modes_cover_every_check_rule():
    assert set(fake.MALFORMED_RULES) == set(rules.RESPONSE_RULES)


_SHORT_LEFT = [c1_chunk(seq) for seq in range(6)]  # 걷기 없음 → 가짜가 [0, t_last]를 하나로
_TWO_FEET = walking_both(12)


@pytest.mark.parametrize("rule", fake.MALFORMED_RULES)
@pytest.mark.parametrize(
    ("sides", "anchors", "chunks"),
    [("left", None, _SHORT_LEFT), ("both", ANCHORS, _TWO_FEET)],
    ids=["left-imu_only", "both-fused"],
)
def test_fake_malformed_response_fails_that_rule(rule, sides, anchors, chunks):
    reply = fake_client(f"malformed:{rule}").analyze(context(sides, anchors), wire.join_records(chunks))
    assert reply.status == 200
    with pytest.raises(rules.ResponseInvalid) as e:
        rules.check_response(
            reply.parts, segment_sides=sides, anchors_sent=anchors is not None, t_last=t_last_of(chunks)
        )
    assert e.value.rule == rule, str(e.value)


def test_fake_slow_over_real_http():
    server = uvicorn.Server(uvicorn.Config(fake.create_app("slow:0.3"), host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            assert time.monotonic() < deadline, "fake gaitfusion did not start"
            time.sleep(0.01)
        url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
        chunks = walking_both(12)
        body = wire.join_records(chunks)
        with Client(url, 0.1) as c:
            assert c.version()["c2_version"] == 1
            with pytest.raises(httpx.TimeoutException):
                c.analyze(context("both", ANCHORS), body)
        with Client(url, 5) as c:
            started = time.monotonic()
            reply = c.analyze(context("both", ANCHORS), body)
            assert time.monotonic() - started >= 0.3
        rules.check_response(reply.parts, segment_sides="both", anchors_sent=True, t_last=12.0)
    finally:
        server.should_exit = True
        thread.join(5)


# --- check_response 직접 (7.2절) ------------------------------------------------------


def _trj(side: str = "left", t0: float = 1.0, n: int = 3, **override) -> bytes:
    if not override and n >= 1:
        return fake.encode_trj1(side, t0, [[0.5] * n for _ in range(13)])
    head = dict(magic=b"TRJ1", side=0 if side == "left" else 1, n_channels=13, rate_hz=100, n=n, reserved=0, t0_s=t0)
    head |= {("side" if k == "side_byte" else k): v for k, v in override.items()}
    data = struct.pack("<4sBBHIId", *head.values()) + b"\0" * (52 * n)
    return data + struct.pack("<I", zlib.crc32(data))


def _bout(
    start: float, end: float, *, idx: int = 0, sides: str = "left", mode: str = "imu_only", feet=("left",), **extra
) -> dict:
    trajectories = [{"side": f, "part": f"traj-{idx}-{f}"} for f in feet]
    return {
        "start_s": start,
        "end_s": end,
        "mode": mode,
        "sides": sides,
        "quality": {},
        "trajectories": trajectories,
    } | extra


def _parts(result: dict | bytes, *trajs: tuple[str, bytes]) -> list[Part]:
    raw = result if isinstance(result, bytes) else json.dumps(result).encode()
    return [Part("result", "application/json", raw)] + [Part(n, "application/octet-stream", d) for n, d in trajs]


def _result(*bouts: dict, **override) -> dict:
    return {"c2_version": 1, "gaitfusion_version": "1.3.0+gabc1234", "summary": {}, "bouts": list(bouts)} | override


def _check(parts, *, sides: str = "left", anchors: bool = False, t_last: float = 10.0) -> rules.Checked:
    return rules.check_response(parts, segment_sides=sides, anchors_sent=anchors, t_last=t_last)


def _rule(parts, **kw) -> str:
    with pytest.raises(rules.ResponseInvalid) as e:
        _check(parts, **kw)
    return e.value.rule


def test_check_accepts_edges_and_keeps_unknown_fields():
    # 걷기 끝 = t_last, 궤적 끝 = t_last(9.98 + 2/100 = 10.0), 궤적 시작 = 0, 걷기 없음
    result = _result(
        _bout(0.0, 4.0, feet=()),
        _bout(4.5, 10.0, idx=1, strides=[{"len_m": 1.2}], future_field={"x": 1}),
        windows=[],
    )
    parts = _parts(result, ("traj-1-left", _trj(t0=9.98, n=3)))
    checked = _check(parts)
    assert [b.trajectories for b in checked.bouts] == [(), (("left", "traj-1-left"),)]
    assert json.loads(checked.result_bytes)["bouts"][1]["future_field"] == {"x": 1}
    assert _check(_parts(_result())).bouts == ()
    # 궤적 시작 0, 끝은 부동소수 오차만큼 t_last를 넘어도 됨
    assert _check(_parts(_result(_bout(0.0, 1.0)), ("traj-0-left", _trj(t0=0.0, n=1001))), t_last=10.0 - 1e-9)


@pytest.mark.parametrize(
    ("sides", "anchors", "bout", "ok"),
    [
        ("left", False, _bout(1, 2, mode="imu_only"), True),
        ("left", True, _bout(1, 2, mode="fused"), True),
        ("left", True, _bout(1, 2, mode="imu_only"), True),
        ("left", False, _bout(1, 2, mode="fused"), False),
        ("both", False, _bout(1, 2, sides="left"), True),
        ("both", False, _bout(1, 2, sides="right", feet=("right",)), True),
        ("both", False, _bout(1, 2, sides="both", feet=("left", "right")), True),
        ("both", False, _bout(1, 2, sides="both", feet=("right",)), True),
        ("left", False, _bout(1, 2, sides="both"), False),
        ("right", False, _bout(1, 2, sides="left"), False),
    ],
)
def test_check_mode_and_sides(sides, anchors, bout, ok):
    parts = _parts(_result(bout), *[(t["part"], _trj(t["side"])) for t in bout["trajectories"]])
    if ok:
        _check(parts, sides=sides, anchors=anchors)
    else:
        assert _rule(parts, sides=sides, anchors=anchors) in ("bouts.mode", "bouts.sides")


@pytest.mark.parametrize(
    ("result", "rule"),
    [
        (b"NaN", "result.json"),
        (b"\xff\xfe", "result.json"),
        (_result(c2_version=True), "c2_version"),
        (_result(c2_version=1.0), "c2_version"),
        (_result(gaitfusion_version=None), "gaitfusion_version"),
        (_result(summary=None), "summary"),
        (_result(bouts=None), "bouts"),
        (_result(["not an object"]), "bouts"),
        (_result(_bout(float("nan"), 2, feet=())), "bouts.time"),
        (_result(_bout(1, float("inf"), feet=())), "bouts.time"),
        (_result(_bout(True, 2, feet=())), "bouts.time"),
        (_result(_bout("1", 2, feet=())), "bouts.time"),
        (_result(_bout(10**400, 2, feet=())), "bouts.time"),
        (_result(_bout(-0.5, 2, feet=())), "bouts.range"),
        (_result(_bout(2, 2, feet=())), "bouts.range"),
        (_result(_bout(2, 2 + 1e-8, feet=())), "bouts.range"),  # µs로 같아짐(DB CHECK)
        (_result(_bout(2, 10.000001, feet=())), "bouts.range"),
        (_result(_bout(1, 3, feet=()), _bout(3, 4, idx=1, feet=())), "bouts.order"),  # 맞닿음
        (_result(_bout(5, 6, feet=()), _bout(1, 2, idx=1, feet=())), "bouts.order"),
        (_result(_bout(1, 2, quality=None, feet=())), "bouts.quality"),
        (_result(_bout(1, 2, trajectories=None)), "bouts.trajectories"),
        (_result(_bout(1, 2, trajectories=[{"side": "left"}])), "bouts.trajectories"),
        (_result(_bout(1, 2, feet=("right",))), "trajectories.side"),
        (_result(_bout(1, 2, trajectories=[{"side": "left", "part": "traj-00-left"}])), "trajectories.part"),
        (_result(_bout(1, 2, trajectories=[{"side": "left", "part": "traj-1-left"}])), "trajectories.part"),
    ],
)
def test_check_rejects_result_fields(result, rule):
    assert _rule(_parts(result, ("traj-0-left", _trj()))) == rule


def test_check_rejects_part_layout():
    good = _result(_bout(1, 2))
    traj = ("traj-0-left", _trj())
    assert _rule([]) == "parts.first"
    assert _rule(None) == "multipart"
    assert _rule(list(reversed(_parts(good, traj)))) == "parts.first"
    assert _rule(_parts(good, traj, ("traj-0-LEFT", _trj()))) == "parts.name"
    assert _rule(_parts(good, traj, ("traj-01-left", _trj()))) == "parts.name"
    assert _rule(_parts(good, traj) + _parts(good)) == "parts.duplicate"
    assert _rule(_parts(good)) == "trajectories.missing"
    assert _rule(_parts(good, traj, ("traj-3-left", _trj()))) == "trajectories.orphan"


@pytest.mark.parametrize(
    ("data", "rule"),
    [
        (b"TRJ1" + bytes(23), "trj.length"),
        (_trj(magic=b"TRJ2"), "trj.magic"),
        (_trj(n_channels=14), "trj.n_channels"),
        (_trj(rate_hz=200), "trj.rate_hz"),
        (_trj(reserved=7), "trj.reserved"),
        (_trj(n=0), "trj.n"),
        (_trj()[:-5] + _trj()[-4:], "trj.length"),
        (_trj() + b"\0", "trj.length"),
        (_trj()[:-4] + b"\0\0\0\0", "trj.crc"),
        (_trj(side_byte=2), "trj.side"),
        (_trj("right"), "trj.side"),  # 이름은 left
        (_trj(t0_s=float("nan")), "trj.time"),
        (_trj(t0_s=float("-inf")), "trj.time"),
        (_trj(t0=-0.01), "trj.time"),
        (_trj(t0=9.99, n=3), "trj.time"),  # 끝 10.01 > t_last 10
    ],
)
def test_check_rejects_trj1(data, rule):
    assert _rule(_parts(_result(_bout(1, 2)), ("traj-0-left", data))) == rule


def test_parse_trj1_minimal():
    trj = rules.parse_trj1(_trj("right", 2.5, n=1))
    assert trj == rules.Trj("right", 100, 1, 2.5, 80) and trj.end_s == 2.5


# --- 행 값과 TRJ1 읽기 (AN-7, AP-4) ------------------------------------------------------


def test_bout_rows_and_trajectory_t0():
    checked = _check(
        _parts(_result(_bout(1.25, 2.5, quality={"uwb_accept_ratio": 0.9})), ("traj-0-left", _trj(t0=0.5)))
    )
    origin_at = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)
    assert rules.bout_rows(checked, run_id=7, origin_at=origin_at) == [
        {
            "run_id": 7, "idx": 0, "start_at": origin_at + timedelta(seconds=1.25),
            "end_at": origin_at + timedelta(seconds=2.5), "mode": "imu_only", "sides": "left",
            "quality": {"uwb_accept_ratio": 0.9},
        }
    ]  # fmt: skip
    assert rules.trajectory_t0(origin_at, checked.trajectories["traj-0-left"]) == origin_at + timedelta(seconds=0.5)


def test_read_channels_steps_and_nulls():
    n = 10
    channels = [[k * 100 + i + 0.5 for i in range(n)] for k in range(13)]
    channels[9][4] = float("nan")  # stance
    channels[9][8] = float("inf")
    data = fake.encode_trj1("left", 3.0, channels)
    slices = rules.channel_slice(data[:24], ["px", "stance"], 4)
    assert slices == [("px", 24, 36), ("stance", 24 + 4 * n * 9, 36)]  # 표본 0, 4, 8까지
    got = rules.read_channels(data, ["px", "stance"], step=4)
    assert got == {"px": [0.5, 4.5, 8.5], "stance": [900.5, None, None]}
    assert list(rules.read_channels(data)) == list(rules.TRJ_CHANNELS)
    assert rules.read_channels(data, ["py"], step=1)["py"] == channels[1]
    assert rules.decode_channel(struct.pack("<f", 0.1), 1, digits=7) == [0.1]
    assert rules.rate_step(100, 25) == 4 and rules.stepped_n(n, 4) == 3
    with pytest.raises(ValueError):
        rules.rate_step(100, 30)
    with pytest.raises(ValueError):
        rules.channel_slice(data, ["speed"], 1)
    hdr = rules.trj_header(data[:24])
    assert (hdr.side, hdr.n, hdr.t0_s, hdr.nbytes) == ("left", n, 3.0, len(data))
