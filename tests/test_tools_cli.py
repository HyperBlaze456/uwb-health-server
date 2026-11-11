"""도구와 운영 스크립트의 빠른 시험(SPEC 6.8 TL, 6.9 OP). DB가 필요 없습니다.

- 모든 도구의 --help가 0으로 끝남
- ops/backup.sh 문법(bash -n)과 --help
- bench.py의 로그 세기(9절 표)
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [
    "fake_head",
    "fake_gaitfusion",
    "reindex_raw",
    "verify_archive",
    "reingest_rejected",
    "admin",
    "import_usb",
    "bench",
    "papers",
]


def run(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60, **kw)


@pytest.mark.parametrize("name", TOOLS)
def test_tool_help(name: str) -> None:
    p = run(sys.executable, f"tools/{name}.py", "--help")
    assert p.returncode == 0, p.stderr
    assert "usage:" in p.stdout


def test_import_usb_dry_run() -> None:
    """TL-7: 실제 기록 조각을 묶음으로 바꿔 검사만 함. 더 자세한 것은 tests/test_import_usb.py."""
    p = run(sys.executable, "tools/import_usb.py", "tests/data/usb_head_slice.jsonl", "--device", "e0:72:a1:ac:a8:58=t",
            "--utc-start", "2026-10-20T10:24:00Z", "--dry-run")  # fmt: skip
    assert p.returncode == 0, p.stderr
    assert "검사(check_chunk): ok" in p.stdout


def test_import_usb_argument_error() -> None:
    p = run(sys.executable, "tools/import_usb.py", "tests/data/usb_head_slice.jsonl", "--device", "e0:72:a1:ac:a8:58=t",
            "--utc-start", "2026-10-20T00:00")  # fmt: skip
    assert p.returncode == 2
    assert "시간대" in p.stderr


def test_verify_archive_rejects_prefix_outside_archive() -> None:
    p = run(sys.executable, "tools/verify_archive.py", "--prefix", "/data/raw/")
    assert p.returncode == 2


def test_backup_sh_syntax() -> None:
    p = run("bash", "-n", "ops/backup.sh")
    assert p.returncode == 0, p.stderr


def test_backup_sh_help_and_usage_error() -> None:
    assert run("bash", "ops/backup.sh", "--help").returncode == 0
    assert run("bash", "ops/backup.sh", "no-such-mode").returncode == 2


def test_bench_counts_p95_from_intake_log() -> None:
    from tools import bench

    def line(device: str, n: int, total: float, fsync: float, db_read: float, db_write: float) -> str:
        rec = {
            "at": "2026-10-29T10:00:00+00:00",
            "event": "intake_request",
            "device": device,
            "n_chunks": n,
            "total_ms": total,
            "fdatasync_ms": fsync,
            "db_read_ms": db_read,
            "db_write_ms": db_write,
        }
        return "intake-1  | " + json.dumps(rec)

    lines = [line("head-a", 1, 1.0 + 5.0, 5.0, 0.1, 0.2) for _ in range(19)]
    lines.append(line("head-a", 1, 3.0 + 5.0, 5.0, 0.1, 0.2))  # 20개 중 1개만 느림 → p95는 1.0
    lines.append(line("head-a", 16, 12.0 + 30.0, 30.0, 1.0, 3.0))  # 16개: 12 ms > 10 ms, fdatasync 30 > 20
    lines.append(line("head-other", 1, 99.0, 0.0, 9.0, 9.0))  # 다른 장치는 빼고 셈
    lines.append("not json")
    samples = bench.read_samples(lines, devices={"head-a"}, since=None, until=None)
    assert len(samples) == 21
    rows = {r.name: r for r in bench.rows_from(samples)}
    one = rows["request, 1 chunk (no fdatasync)"]
    assert one.p95 == pytest.approx(1.0) and one.verdict() == "OK"
    assert rows["request, 16 chunks (no fdatasync)"].verdict() == "OVER"
    assert rows["DB time per chunk"].p95 == pytest.approx(0.3)
    fsync = rows["fdatasync"]  # 21개 중 30 ms 하나는 p95에 들지 않음
    assert fsync.p95 == pytest.approx(5.0) and fsync.verdict() == "record"
    assert "change the disk" not in fsync.note
