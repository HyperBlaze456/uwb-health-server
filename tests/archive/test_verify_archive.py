"""M4-6 (TL-4): verify_archive.py가 바뀐 파일을 찾고, --reindex가 지운 행을 파일에서 되살림."""

import pytest

from tests.support import closed_segment, make_core, run_jobs
from tools import verify_archive
from uwbserver.pipeline import files

pytestmark = pytest.mark.db
COLS = "device_id, head_boot_id, segment_id, first_seq, last_seq, n_chunks, start_at, end_at, path, bytes, sha256"


def test_detects_change_and_reindexes(env, device):
    closed_segment(env, device)
    assert run_jobs(env, make_core()) == ["done", "done"]
    before = env.q(f"SELECT {COLS} FROM archive.range ORDER BY first_seq")
    assert before
    assert verify_archive.main([]) == 0

    # 파일 한 바이트를 바꾸면 찾음
    p = files.resolve(before[0]["path"])
    data = bytearray(p.read_bytes())
    data[100] ^= 0xFF
    p.write_bytes(bytes(data))
    report = verify_archive.verify(env.conn("core"), prefix="/data/archive/", reindex=False)
    assert sum(report.problems.values()) == 1
    assert verify_archive.main([]) == 1
    data[100] ^= 0xFF
    p.write_bytes(bytes(data))

    # 행을 지우면(디스크를 잃고 덤프로 복구한 것처럼) --reindex가 파일에서 다시 만듦. from_run_id는 알 수 없어 NULL.
    env.q("DELETE FROM archive.range")
    assert verify_archive.main([]) == 1
    assert verify_archive.main(["--reindex"]) == 0
    assert env.q(f"SELECT {COLS} FROM archive.range ORDER BY first_seq") == before
    assert env.scalar("SELECT count(*) FROM archive.range WHERE from_run_id IS NOT NULL") == 0
