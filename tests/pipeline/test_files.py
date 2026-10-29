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


def _chunks(n):
    from tools import fake_head

    h = fake_head.FakeHead()
    return [h.next_chunk() for _ in range(n)]


def test_append_scan_and_torn_tail(root):
    c = _chunks(3)
    p = files.raw_path("head-x", 1, US)
    with files.Appender() as a:
        o1 = a.append(p, files.raw_record_head(len(c[0]), US), c[0])
        o2 = a.append(p, files.raw_record_head(len(c[1]), US + 1), c[1])
        a.sync()
    assert (o1, o2) == (12, 12 + len(c[0]) + 12)
    assert files.read_at(p, o2, len(c[1])) == c[1]
    end_ok = 24 + len(c[0]) + len(c[1])
    # 쓰는 중에 멈춘 꼬리
    with open(files.resolve(p), "ab") as f:
        f.write(files.raw_record_head(len(c[2]), US) + c[2][:100])
    garbage = []
    recs, end = files.scan_raw(p, garbage)
    assert [r.data for r in recs] == c[:2] and end == end_ok
    assert garbage == [(end_ok, 112)]
    assert files.truncate_torn_tail(p, end) == 112
    assert files.size(p) == end


def test_torn_record_in_middle_keeps_later_records(root):
    """작업자가 쓰는 중에 죽고(uvicorn이 다시 띄움) 다음 요청이 그 뒤에 이어 쓴 경우.

    뒤의 확인 받은 레코드를 읽을 수 있어야 하고, 꼬리 자르기가 그것을 자르면 안 됨.
    """
    c = _chunks(4)
    p = files.raw_path("head-x", 1, US)
    rec = lambda x: files.raw_record_head(len(x), US) + x  # noqa: E731
    torn = rec(c[1])[: 12 + len(c[1]) // 2]
    files.resolve(p).parent.mkdir(parents=True)
    files.resolve(p).write_bytes(rec(c[0]) + torn + rec(c[2]) + rec(c[3]))
    garbage = []
    recs, end = files.scan_raw(p, garbage)
    assert [r.data for r in recs] == [c[0], c[2], c[3]]
    assert garbage == [(len(rec(c[0])), len(torn))]
    assert end == files.size(p)
    assert files.truncate_torn_tail(p, end) == 0
    off = recs[1].offset
    assert files.read_at(p, off, len(c[2])) == c[2]


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


def test_rollback_does_not_cut_someone_elses_bytes(root):
    """검토 9: 내가 쓴 뒤 다른 작성자가 이어 썼으면(장치 잠금이 풀린 사이) 되돌리기가 남의 레코드를 자르지 않음."""
    c = _chunks(2)
    p = files.raw_path("head-x", 1, US)
    a = files.Appender()
    a.append(p, files.raw_record_head(len(c[0]), US), c[0])
    with open(files.resolve(p), "ab") as other:
        other.write(files.raw_record_head(len(c[1]), US) + c[1])
    a.rollback()
    a.close()
    recs, _ = files.scan_raw(p)
    assert [r.data for r in recs] == c


def test_rejected_scan_skips_broken_record(root):
    """검토 12: rejected 파일 중간의 깨진 레코드 뒤의 레코드도 읽음."""
    p = files.rejected_path("head-x", US)
    good = lambda d, code: files.rejected_record_head(len(d), US, code, 6) + d  # noqa: E731
    torn = good(b"x" * 100, 2)[:40]
    files.resolve(p).parent.mkdir(parents=True)
    files.resolve(p).write_bytes(good(b"a" * 60, 2) + torn + good(b"b" * 70, 3))
    recs, _ = files.scan_rejected(p)
    assert [(r.code, r.data) for r in recs] == [(2, b"a" * 60), (3, b"b" * 70)]
