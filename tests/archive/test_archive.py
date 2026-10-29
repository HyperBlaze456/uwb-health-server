"""M4 원본 보관과 삭제 (AR). SPEC 11절 M4 시험 1–5."""

import hashlib
from dataclasses import replace

import pytest

from tests.support import closed_segment, make_core, run_jobs
from uwbserver.archive import run as archive_run
from uwbserver.pipeline import clock, files, jobs, settings, wire

pytestmark = pytest.mark.db
PAD_US = 15 * 1_000_000


def _analyze_and_archive(env, device, mode="ok", **opts):
    seg = closed_segment(env, device, **opts)
    core = make_core(mode)
    results = run_jobs(env, core)
    return seg, core, results


def _archived_seqs(env) -> list[int]:
    seqs = []
    for r in env.q("SELECT path FROM archive.range ORDER BY first_seq"):
        recs, _ = files.scan_raw(r["path"])
        seqs += [wire.decode_chunk(x.data).header.chunk_seq for x in recs]
    return seqs


def test_ranges_cover_bouts_with_padding(env, device):
    """M4-1: 걷기 구간 ± 15초, 구간 안으로 자름, 겹침 합침, 이미 있는 범위 제외, sha256이 행과 같음."""
    seg, core, results = _analyze_and_archive(env, device)
    assert results == ["done", "done"]  # analysis, archive
    run = env.one("SELECT id, origin_at, origin_head_us FROM analysis.run")
    offset = clock.to_us(run["origin_at"]) - run["origin_head_us"]
    bouts = env.q("SELECT start_at, end_at FROM analysis.bout WHERE run_id = %s", (run["id"],))
    chunks = env.q("SELECT chunk_seq, head_us_start FROM intake.chunk ORDER BY chunk_seq")
    s = env.one("SELECT first_seq, last_seq FROM segment.segment WHERE id = %s", (seg,))
    expected = sorted(
        c["chunk_seq"]
        for c in chunks
        if s["first_seq"] <= c["chunk_seq"] <= s["last_seq"]
        and any(
            c["head_us_start"] + offset <= clock.to_us(b["end_at"]) + PAD_US
            and c["head_us_start"] + offset + 1_000_000 > clock.to_us(b["start_at"]) - PAD_US
            for b in bouts
        )
    )
    assert _archived_seqs(env) == expected
    for r in env.q("SELECT path, sha256, bytes, n_chunks, from_run_id FROM archive.range"):
        data = files.resolve(r["path"]).read_bytes()
        assert hashlib.sha256(data).digest() == r["sha256"] and len(data) == r["bytes"]
        assert r["from_run_id"] == run["id"]
    cut = env.one("SELECT n_ranges, n_new_chunks FROM archive.cut WHERE run_id = %s", (run["id"],))
    assert cut["n_ranges"] == env.scalar("SELECT count(*) FROM archive.range")
    assert cut["n_new_chunks"] == len(expected)
    # 재분석: 같은 걷기 → 새 범위 없음(이미 덮음)
    jobs.enqueue_analysis(env.conn("core"), seg, now=clock.now())
    assert run_jobs(env, core) == ["done", "done"]
    cuts = env.q("SELECT n_ranges FROM archive.cut ORDER BY run_id")
    assert [c["n_ranges"] for c in cuts] == [cut["n_ranges"], 0]


def test_skipped_run_still_cut(env, device):
    """M4-2: 걷기가 없는 run도 archive.cut 행(n_ranges = 0)."""
    _analyze_and_archive(env, device, mode="422")
    assert env.q("SELECT n_ranges, n_new_chunks FROM archive.cut") == [{"n_ranges": 0, "n_new_chunks": 0}]


def _purge(env, enabled: bool):
    settings.use(replace(env.settings, purge_enabled=enabled))
    try:
        return archive_run.purge(env.conn("core"))
    finally:
        settings.use(env.settings)


def _raw_files(env):
    return sorted(p.name for p in (env.data_root / "raw").rglob("*.bin"))


def test_purge_deletes_settled_file(env, device):
    """M4-3: 조건을 만족하는 raw만 지워지고, 순서: 블록 행 → 묶음 행 → 파일. survey는 남음."""
    _analyze_and_archive(env, device)
    n_chunks = env.scalar("SELECT count(*) FROM intake.chunk")
    assert _raw_files(env)
    env.clock.advance(29 * 86400)
    assert _purge(env, True).raw_files_deleted == 0  # 아직 30일 전
    env.clock.advance(2 * 86400)
    stats = _purge(env, True)
    assert stats.raw_files_deleted == 1 and stats.chunk_rows == n_chunks
    assert _raw_files(env) == []
    for t in ("intake.chunk", "intake.health", "intake.system"):
        assert env.scalar(f"SELECT count(*) FROM {t}") == 0
    assert env.scalar("SELECT count(*) FROM archive.range") > 0
    assert all(files.exists(r["path"]) for r in env.q("SELECT path FROM archive.range"))


BREAKS = {
    "segment_open": "UPDATE segment.segment SET state = 'open', closed_reason = NULL",
    "no_run": "DELETE FROM archive.cut; DELETE FROM analysis.trajectory; DELETE FROM archive.range; "
    "DELETE FROM analysis.bout; DELETE FROM pipeline.job WHERE kind = 'archive'; DELETE FROM analysis.run",
    "run_not_archived": "DELETE FROM archive.cut",
    "analysis_pending": "INSERT INTO pipeline.job (kind, segment_id) SELECT 'analysis', id FROM segment.segment",
    "archive_pending": "INSERT INTO pipeline.job (kind, run_id) SELECT 'archive', id FROM analysis.run",
    "analysis_failed": "INSERT INTO pipeline.job (kind, segment_id, state) SELECT 'analysis', id, 'failed' FROM segment.segment",
    "not_segmented": "UPDATE pipeline.cursor SET last_id = 0",
    "chunk_without_segment": "DELETE FROM segment.segment_tag; UPDATE segment.segment SET first_seq = first_seq + 100000, last_seq = last_seq + 100000",
}


@pytest.mark.parametrize("reason", sorted(BREAKS))
def test_purge_keeps_when_condition_broken(env, device, reason):
    """M4-3: 조건 하나씩 깨뜨린 경우마다 남음(AR-6)."""
    _analyze_and_archive(env, device)
    for stmt in BREAKS[reason].split("; "):
        env.q(stmt)
    env.clock.advance(31 * 86400)
    stats = _purge(env, True)
    assert stats.raw_files_deleted == 0 and _raw_files(env)
    assert env.scalar("SELECT count(*) FROM intake.chunk") > 0


def test_purge_disabled_only_logs(env, device, capsys):
    """M4-4 (AR-7): PURGE_ENABLED = false면 아무것도 지우지 않고 지울 대상 수만 로그에."""
    _analyze_and_archive(env, device)
    n_chunks = env.scalar("SELECT count(*) FROM intake.chunk")
    env.clock.advance(31 * 86400)
    capsys.readouterr()
    stats = _purge(env, False)
    assert stats.raw_files_deleted == 1 and stats.chunk_rows == n_chunks  # "지울" 수
    assert _raw_files(env) and env.scalar("SELECT count(*) FROM intake.chunk") == n_chunks
    assert '"purge_dry_run"' in capsys.readouterr().out


def test_rejected_purged_without_conditions(env, device, intake_client):
    from tests.support import auth
    from tools import fake_head

    intake_client.post("/d/v1/chunks", content=wire.join_records([fake_head.bad_chunk("bad_crc")]), headers=auth(device.token))
    assert env.scalar("SELECT count(*) FROM intake.rejected") == 1
    env.clock.advance(32 * 86400)
    stats = _purge(env, True)
    assert stats.rejected_files == 1 and stats.rejected_rows == 1
    assert env.scalar("SELECT count(*) FROM intake.rejected") == 0
    assert not list((env.data_root / "rejected").rglob("*.bin"))


def test_reanalysis_after_purge_uses_archive(env, device):
    """M4-5 (AR-8, AN-4): raw가 지워진 구간의 재분석은 archive로 보내고, 범위 사이가 seq 빈자리로 보임."""
    seg, core, _ = _analyze_and_archive(env, device)
    archived = _archived_seqs(env)
    n_ranges = env.scalar("SELECT count(*) FROM archive.range")
    env.clock.advance(31 * 86400)
    _purge(env, True)
    assert env.scalar("SELECT count(*) FROM intake.chunk") == 0
    seen = {}
    real = core.client.analyze

    def spy(context, body):
        seen["seqs"] = [wire.decode_chunk(c).header.chunk_seq for c in wire.split_request(body, max_chunks=None)]
        return real(context, body)

    core.client.analyze = spy
    jobs.enqueue_analysis(env.conn("core"), seg, now=clock.now())
    assert run_jobs(env, core) == ["done", "done"]
    last = env.one("SELECT input, n_chunks_sent FROM analysis.run ORDER BY id DESC LIMIT 1")
    assert last == {"input": "archive", "n_chunks_sent": len(archived)}
    assert seen["seqs"] == archived
    if n_ranges > 1:
        gaps = [b - a for a, b in zip(archived, archived[1:]) if b - a > 1]
        assert gaps, "범위 사이가 seq 빈자리로 보여야 함"
    # 새 run의 archive 작업은 raw가 없으니 새 범위를 만들지 않음
    assert env.scalar("SELECT n_ranges FROM archive.cut ORDER BY run_id DESC LIMIT 1") == 0


def test_reanalyze_api_409_without_input(env, device):
    """8절: raw도 archive도 없으면 409. 여기서는 store 함수로 확인(HTTP는 M5 시험)."""
    from uwbserver.segment import store as segment_store

    seg = closed_segment(env, device)
    assert segment_store.has_input(env.conn("api"), seg)
    env.q("DELETE FROM intake.health; DELETE FROM intake.system")
    env.q("DELETE FROM intake.chunk")
    assert not segment_store.has_input(env.conn("api"), seg)

