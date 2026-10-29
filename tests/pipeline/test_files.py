"""5.2 파일 형식과 쓰기 순서."""

from datetime import UTC, datetime

import pytest

from uwbserver.pipeline import clock, files, settings
from uwbserver.pipeline.settings import Settings


@pytest.fixture
def root(tmp_path):
    settings.use(Settings(data_root=tmp_path))
    yield tmp_path
    settings.use(None)


US = clock.to_us(datetime(2026, 10, 26, 3, 12, 5, 250_000, tzinfo=UTC))


def test_paths():
    assert files.raw_path("head-e072a1aca858", 0xAB, US) == "/data/raw/head-e072a1aca858/000000ab/2026-10-26T03.bin"
    assert files.rejected_path("head-x", US) == "/data/rejected/head-x/2026-10-26.bin"
    a = files.archive_path("head-x", 0xDEADBEEF, 10, 99, US)
    assert a == "/data/archive/head-x/2026-10/2026-10-26T03-12-05Z_deadbeef_10-99.bin"
    n = files.parse_archive_path(a)
    assert (n.device_id, n.head_boot_id, n.first_seq, n.last_seq) == ("head-x", 0xDEADBEEF, 10, 99)
    r = files.parse_raw_path("/data/raw/head-x/000000ab/2026-10-26T03.bin")
    assert r.hour == datetime(2026, 10, 26, 3, tzinfo=UTC) and r.head_boot_id == 0xAB


def test_append_scan_and_torn_tail(root):
    p = files.raw_path("head-x", 1, US)
    with files.Appender() as a:
        o1 = a.append(p, files.raw_record_head(3, US), b"abc")
        o2 = a.append(p, files.raw_record_head(2, US + 1), b"de")
        a.sync()
    assert (o1, o2) == (12, 12 + 3 + 12)
    assert files.read_at(p, o2, 2) == b"de"
    # 쓰는 중에 멈춘 꼬리
    with open(files.resolve(p), "ab") as f:
        f.write(files.raw_record_head(100, US) + b"xy")
    recs, end = files.scan_raw(p)
    assert [r.data for r in recs] == [b"abc", b"de"] and end == 12 + 3 + 12 + 2
    assert files.truncate_torn_tail(p, end) == 14
    assert files.size(p) == end


def test_rollback_truncates(root):
    p = files.rejected_path("head-x", US)
    with files.Appender() as a:
        a.append(p, files.rejected_record_head(1, US, 2, 6), b"z")
        a.sync()
    with pytest.raises(RuntimeError):
        with files.Appender() as a:
            a.append(p, files.rejected_record_head(1, US, 2, 6), b"q")
            raise RuntimeError
    recs, _ = files.scan_rejected(p)
    assert [(r.code, r.check, r.data) for r in recs] == [(2, 6, b"z")]


def test_write_atomic(root):
    p = files.result_path(5)
    files.write_atomic(p, b"x" * 10)
    assert files.size(p) == 10
    assert not list(files.resolve(p).parent.glob(".*tmp*"))
