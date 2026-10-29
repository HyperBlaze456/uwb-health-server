"""받기 속도 재기 (SPEC TL-8, 9절, 11절 M1-9).

장치 N대가 각자 1초에 요청 하나씩 intake에 보내고, intake의 PL-5 로그(event = intake_request)에서
서버 쪽 소요 시간의 p95를 세어 9절 목표와 나란히 보입니다. 네트워크·TLS 시간은 빼고 셉니다.

    # 1. intake 로그를 파일로 받기 시작
    docker compose -f compose.yaml -f compose.dev.yaml logs -f --no-log-prefix intake > /tmp/intake.log &
    # 2. 장치 10대, 10분(M1-9). 장치는 개발 DB에 직접 등록(--register) 하거나 --device MAC=TOKEN으로 줌
    uv run python tools/bench.py --register 10 --duration 600 --log-file /tmp/intake.log
    # 밀린 묶음 16개짜리 요청(9절 둘째 줄)
    uv run python tools/bench.py --register 10 --duration 120 --chunks-per-request 16 --log-file /tmp/intake.log
    # 이미 받은 로그만 다시 셈
    docker compose logs --no-log-prefix intake | uv run python tools/bench.py --report-only --log-file -

장치 등록: SPEC TL-8은 이 도구가 HTTP만 쓴다고 정합니다. --device MAC=TOKEN이 그 방식입니다.
--register N은 개발 DB용 편의 기능으로, api_rw 역할로 registry.device에 직접 넣습니다(RG-1과 같은 store 함수).
MAC은 e0:72:a1:b0:00:01부터 씁니다(fake_head의 시험 장치 MAC은 건드리지 않음). 이미 있는 장치는 토큰을 재발급합니다.

종료 코드: 0 = 잰 줄이 모두 목표 안, 1 = 목표를 넘은 줄이 있거나 잴 로그가 없음, 2 = 인자 오류나 보내기·등록 실패.
"""

import argparse
import json
import math
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

try:
    from tools import fake_head
except ImportError:  # 스크립트로 돌 때는 tools/가 sys.path[0]
    import fake_head  # type: ignore[no-redef]

from uwbserver.pipeline import log

# 9절 목표 (p95, ms)
TARGET_ONE_CHUNK_MS = 2.0
TARGET_SIXTEEN_CHUNKS_MS = 10.0
TARGET_DB_PER_CHUNK_MS = 0.5
FDATASYNC_LIMIT_MS = 20.0  # 넘으면 디스크를 바꿈
# 로그의 at(서버 시계)과 이 도구의 시계 차이 여유. 시작 쪽은 좁게 둬서 바로 앞 실행의 줄을 세지 않음
# (보내기는 시작 1초 뒤부터, 앞 실행은 --settle 동안 기다린 뒤 끝남). 서버와 시계가 몇 초 넘게 다르면 같은 호스트에서 돌림.
SLACK_BEFORE = timedelta(seconds=2)
SLACK_AFTER = timedelta(seconds=30)


# ---------------------------------------------------------------- 장치


@dataclass(frozen=True, slots=True)
class BenchDevice:
    mac: str
    token: str

    @property
    def device_id(self) -> str:
        return fake_head.device_id(self.mac)


def bench_macs(n: int) -> list[str]:
    """시험 장치 MAC(fake_head.TEST_DEVICE_MAC)을 뺀 n개."""
    return fake_head.device_macs(n + 1)[1:]


def register_devices(n: int) -> list[BenchDevice]:
    """개발 DB에 장치 n대를 api_rw로 등록. 이미 있으면 토큰을 재발급(RG-2)."""
    from uwbserver.pipeline import clock, db
    from uwbserver.registry import rules, store

    out: list[BenchDevice] = []
    with db.connect("api") as conn:
        for mac in bench_macs(n):
            token = rules.new_token()
            now = clock.now()
            with conn.transaction():
                created = store.insert_device(
                    conn,
                    device_id=rules.device_id(mac),
                    mac=rules.normalize_mac(mac),
                    token_hash=rules.token_hash(token),
                    now=now,
                    note="bench.py",
                )
                if not created:
                    store.set_device_token(conn, rules.device_id(mac), rules.token_hash(token), now)
            out.append(BenchDevice(mac, token))
    log.info("bench_registered", devices=[d.device_id for d in out])
    return out


def parse_device(text: str) -> BenchDevice:
    mac, _, token = text.partition("=")
    try:
        fake_head.mac_bytes(mac)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"MAC 형식이 아님: {text!r}") from e
    if not token:
        raise argparse.ArgumentTypeError(f"MAC=TOKEN 꼴이어야 함: {text!r}")
    return BenchDevice(mac.lower(), token)


# ---------------------------------------------------------------- 보내기


def build_bodies(mac: str, seed: int, n_requests: int, per_request: int) -> list[bytes]:
    """요청 본문 n_requests개. 요청마다 묶음 per_request개(1초 묶음들). 묶음 시각은 지금까지로 끝남."""
    span_s = n_requests * per_request
    start_us = time.time_ns() // 1000 - span_s * 1_000_000
    requests = fake_head.scenario("normal", seed=seed, start_utc_us=start_us, mac=mac, duration_s=span_s)
    chunks = [c for r in requests for c in fake_head.unframe_request(r.body)]
    return [fake_head.frame_request(chunks[i : i + per_request]) for i in range(0, len(chunks), per_request)][
        :n_requests
    ]


@dataclass
class SendStats:
    statuses: Counter = field(default_factory=Counter)
    rtt_ms: list[float] = field(default_factory=list)


def send_device(
    url: str, dev: BenchDevice, bodies: list[bytes], t0: float, phase_s: float, timeout: float, out: SendStats
) -> None:
    import httpx

    headers = {"Authorization": f"Bearer {dev.token}", "Content-Type": "application/octet-stream"}
    with httpx.Client(timeout=timeout) as client:
        for i, body in enumerate(bodies):
            delay = t0 + phase_s + i - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            t = time.perf_counter()
            try:
                status = client.post(url, content=body, headers=headers).status_code
            except httpx.HTTPError as e:
                status = type(e).__name__
            out.rtt_ms.append((time.perf_counter() - t) * 1000)
            out.statuses[status] += 1


def send_all(url: str, devices: list[BenchDevice], duration_s: int, per_request: int, timeout: float) -> SendStats:
    bodies = {d.mac: build_bodies(d.mac, i, duration_s, per_request) for i, d in enumerate(devices)}
    total = SendStats()
    per_device = {d.mac: SendStats() for d in devices}
    t0 = time.monotonic() + 1.0
    threads = [
        threading.Thread(
            target=send_device,
            args=(url, d, bodies[d.mac], t0, i / len(devices), timeout, per_device[d.mac]),
            daemon=True,
        )
        for i, d in enumerate(devices)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for s in per_device.values():
        total.statuses.update(s.statuses)
        total.rtt_ms.extend(s.rtt_ms)
    return total


# ---------------------------------------------------------------- 로그 세기


def read_samples(
    lines: Iterable[str], *, devices: set[str] | None, since: datetime | None, until: datetime | None
) -> list[dict]:
    """intake_request 줄 중 소요 시간이 있는 것. 'intake-1 | {...}'처럼 앞에 붙은 것은 떼고 읽습니다."""
    out = []
    for line in lines:
        start = line.find("{")
        if start < 0:
            continue
        try:
            rec = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if rec.get("event") != "intake_request" or "total_ms" not in rec or not rec.get("n_chunks"):
            continue
        if devices is not None and rec.get("device") not in devices:
            continue
        if since or until:
            try:
                at = datetime.fromisoformat(rec["at"])
            except (KeyError, ValueError):
                continue
            if (since and at < since) or (until and at > until):
                continue
        out.append(rec)
    return out


def p(values: list[float], q: float) -> float | None:
    """가장 가까운 순위 방식의 백분위수."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(q * len(s)) - 1)]


@dataclass
class Row:
    name: str
    values: list[float]
    target: float | None  # None = 기록만
    note: str = ""

    @property
    def p95(self) -> float | None:
        return p(self.values, 0.95)

    def verdict(self) -> str:
        if not self.values:
            return "n/a"
        if self.target is None:
            return "record"
        return "OK" if self.p95 <= self.target else "OVER"


def rows_from(samples: list[dict]) -> list[Row]:
    def server_ms(r: dict) -> float:
        return r["total_ms"] - r.get("fdatasync_ms", 0.0)

    one = [server_ms(r) for r in samples if r["n_chunks"] == 1]
    sixteen = [server_ms(r) for r in samples if r["n_chunks"] == 16]
    db_per_chunk = [(r.get("db_read_ms", 0.0) + r.get("db_write_ms", 0.0)) / r["n_chunks"] for r in samples]
    fsync = [r["fdatasync_ms"] for r in samples if "fdatasync_ms" in r]
    fsync_row = Row("fdatasync", fsync, None, f"over {FDATASYNC_LIMIT_MS:g} ms = change disk")
    rows = [
        Row("request, 1 chunk (no fdatasync)", one, TARGET_ONE_CHUNK_MS),
        Row("request, 16 chunks (no fdatasync)", sixteen, TARGET_SIXTEEN_CHUNKS_MS),
        Row("DB time per chunk", db_per_chunk, TARGET_DB_PER_CHUNK_MS),
        fsync_row,
    ]
    if fsync_row.values and fsync_row.p95 > FDATASYNC_LIMIT_MS:
        fsync_row.note = f"p95 over {FDATASYNC_LIMIT_MS:g} ms: change the disk (SPEC 9)"
    return rows


def fmt(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"


def print_table(rows: list[Row]) -> None:
    print(f"{'path (SPEC 9)':<36} {'n':>7} {'p50 ms':>9} {'p95 ms':>9} {'target':>8}  result")
    for r in rows:
        target = "-" if r.target is None else f"<= {r.target:g}"
        line = f"{r.name:<36} {len(r.values):>7} {fmt(p(r.values, 0.5)):>9} {fmt(r.p95):>9} {target:>8}  {r.verdict()}"
        print(line + (f"  ({r.note})" if r.note else ""))


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="bench.py",
        description="장치 N대로 초당 1요청씩 보내고 intake 로그에서 9절 p95를 셉니다(TL-8).",
        epilog="종료 코드: 0 목표 안, 1 목표 넘음·로그 없음, 2 인자 오류·보내기 실패.",
    )
    ap.add_argument("--url", default="http://localhost:8001", help="intake 주소(기본 %(default)s)")
    who = ap.add_mutually_exclusive_group()
    who.add_argument("--register", type=int, metavar="N", help="개발 DB에 장치 N대를 api_rw로 등록해 씀")
    who.add_argument("--device", action="append", type=parse_device, metavar="MAC=TOKEN", help="이미 등록한 장치(여러 번)")
    ap.add_argument("--duration", type=int, default=600, metavar="S", help="보내는 시간(초, 기본 %(default)s)")
    ap.add_argument(
        "--chunks-per-request", type=int, default=1, choices=range(1, 17), metavar="K", help="요청 하나의 묶음 수 1–16"
    )
    ap.add_argument("--timeout", type=float, default=10.0, help="HTTP 시간 한도(초)")
    ap.add_argument("--log-file", metavar="PATH", help="intake 로그 파일. '-'는 표준 입력. 없으면 보내기만 함")
    ap.add_argument("--report-only", action="store_true", help="보내지 않고 --log-file만 셈")
    ap.add_argument("--settle", type=float, default=3.0, metavar="S", help="보낸 뒤 로그가 파일에 닿기를 기다리는 초")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    log.setup("bench")
    if args.report_only and not args.log_file:
        ap.error("--report-only에는 --log-file이 필요함")
    if not args.report_only and not (args.register or args.device):
        ap.error("--register N 또는 --device MAC=TOKEN이 필요함")

    devices: list[BenchDevice] = []
    since = until = None
    if not args.report_only:
        try:
            devices = register_devices(args.register) if args.register else list(args.device)
        except Exception as e:  # noqa: BLE001 - 등록 실패는 어떤 것이든 실행 불가
            log.error("bench_register_failed", error=f"{type(e).__name__}: {e}")
            return 2
        url = args.url.rstrip("/") + "/d/v1/chunks"
        log.info(
            "bench_start", url=url, devices=len(devices), duration_s=args.duration, chunks_per_request=args.chunks_per_request
        )
        since = datetime.now(UTC) - SLACK_BEFORE
        sent = send_all(url, devices, args.duration, args.chunks_per_request, args.timeout)
        time.sleep(args.settle)
        until = datetime.now(UTC) + SLACK_AFTER
        log.info(
            "bench_sent",
            requests=sum(sent.statuses.values()),
            statuses={str(k): v for k, v in sent.statuses.items()},
            client_rtt_p95_ms=p(sent.rtt_ms, 0.95),
        )
        if sent.statuses.get(200, 0) == 0:
            log.error("bench_no_success", hint="intake 주소와 토큰을 확인하세요")
            return 2

    if not args.log_file:
        return 0
    try:
        if args.log_file == "-":
            lines = sys.stdin.readlines()
        else:
            lines = Path(args.log_file).read_text(errors="replace").splitlines()
    except OSError as e:
        log.error("bench_log_unreadable", error=str(e))
        return 2
    wanted = {d.device_id for d in devices} or None
    samples = read_samples(lines, devices=wanted, since=since, until=until)
    rows = rows_from(samples)
    print_table(rows)
    if not samples:
        log.error("bench_no_samples", hint="intake 로그(event=intake_request)가 --log-file에 있는지 확인하세요")
        return 1
    return 1 if any(r.verdict() == "OVER" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
