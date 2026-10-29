"""M3 분석 호출 (AN, TL-2). SPEC 11절 M3 시험 1–6, 8."""

import gzip
import hashlib
import json
import time
from datetime import timedelta

import pytest

from tests.support import closed_segment, fake_c2, make_core, run_jobs
from uwbserver.analysis import rules
from uwbserver.pipeline import clock, files, jobs

pytestmark = pytest.mark.db


def _runs(env):
    return env.q("SELECT * FROM analysis.run ORDER BY id")


def test_closed_segment_makes_run(env, device):
    """M3-1: run 1개(done), 걷기 구간 행, 궤적 파일(TRJ1 검사 통과), 결과 원문(sha256이 run과 같음), archive 작업 1개."""
    seg = closed_segment(env, device)
    core = make_core()
    assert run_jobs(env, core, ("analysis",)) == ["done"]
    (run,) = _runs(env)
    assert (run["segment_id"], run["status"], run["input"]) == (seg, "done", "raw")
    assert run["n_chunks_sent"] == env.scalar("SELECT n_chunks FROM segment.segment WHERE id = %s", (seg,))
    raw = gzip.decompress(files.resolve(run["result_path"]).read_bytes())
    assert hashlib.sha256(raw).digest() == run["result_sha256"] and len(raw) == run["result_bytes"]
    bouts = env.q("SELECT * FROM analysis.bout WHERE run_id = %s ORDER BY idx", (run["id"],))
    assert len(bouts) >= 4  # 600초, 걷기 40초/쉼 80초
    trajs = env.q("SELECT * FROM analysis.trajectory")
    assert len(trajs) == len(bouts)
    for t in trajs:
        trj = rules.parse_trj1(files.resolve(t["path"]).read_bytes())
        assert (trj.n, trj.rate_hz, t["side"]) == (t["n"], 100, trj.side)
    assert env.q("SELECT kind, run_id, state FROM pipeline.job WHERE kind = 'archive'") == [
        {"kind": "archive", "run_id": run["id"], "state": "queued"}
    ]
    ctx = run["context"]
    assert ctx["c2_version"] == 1 and ctx["segment_id"] == seg and ctx["run_id"] == run["id"]
    assert ctx["origin_head_us"] == run["origin_head_us"] and ctx["anchors"] is None  # 측량 안 된 배치


def test_skipped_on_422(env, device):
    closed_segment(env, device)
    assert run_jobs(env, make_core("422"), ("analysis",)) == ["skipped"]
    (run,) = _runs(env)
    assert run["status"] == "skipped" and run["skip_reason"] and run["result_path"] is None
    assert env.scalar("SELECT count(*) FROM pipeline.job WHERE kind = 'archive'") == 1


def test_500_retries_then_fails(env, device):
    """M3-2: 2, 4, 8분 뒤 다시 시도해 모두 네 번 실패하면 failed, run 없음, 쓴 파일 없음."""
    closed_segment(env, device)
    core = make_core("500")
    for wait in (0, 2, 4, 8):
        env.clock.advance(wait * 60)
        assert run_jobs(env, core, ("analysis",)) == ["retry_queued" if wait < 8 else "retry_failed"]
    job = env.one("SELECT state, attempts FROM pipeline.job WHERE kind = 'analysis'")
    assert job == {"state": "failed", "attempts": 4}
    assert _runs(env) == []
    assert not (env.data_root / "result").exists() or not any((env.data_root / "result").iterdir())


def _malformed_rules():
    from tools import fake_gaitfusion

    return fake_gaitfusion.MALFORMED_RULES


@pytest.mark.parametrize("rule", _malformed_rules())
def test_malformed_response_fails_at_once(env, device, rule):
    closed_segment(env, device)
    assert run_jobs(env, make_core(f"malformed:{rule}"), ("analysis",)) == ["failed_invalid"]
    job = env.one("SELECT state, attempts, last_error FROM pipeline.job WHERE kind = 'analysis'")
    assert job["state"] == "failed" and job["attempts"] == 0 and job["last_error"].startswith("c2_invalid")
    assert _runs(env) == []
    assert not list(env.data_root.rglob("*.trj")) and not list(env.data_root.rglob("*.json.gz"))


def test_reanalysis_keeps_old_bouts_drops_old_trajectories(env, device):
    """M3-3 (AN-9)."""
    seg = closed_segment(env, device)
    core = make_core()
    run_jobs(env, core, ("analysis",))
    first = _runs(env)[0]["id"]
    old_paths = [t["path"] for t in env.q("SELECT path FROM analysis.trajectory")]
    assert jobs.enqueue_analysis(env.conn("api"), seg, now=clock.now())
    assert run_jobs(env, core, ("analysis",)) == ["done"]
    runs = _runs(env)
    assert [r["id"] for r in runs][0] == first and len(runs) == 2
    assert env.scalar("SELECT count(*) FROM analysis.bout WHERE run_id = %s", (first,)) > 0
    assert env.scalar(
        "SELECT count(*) FROM analysis.trajectory t JOIN analysis.bout b ON b.id = t.bout_id WHERE b.run_id = %s", (first,)
    ) == 0
    assert all(not files.exists(p) for p in old_paths)
    assert env.scalar("SELECT run_id FROM analysis.current_run WHERE segment_id = %s", (seg,)) == runs[1]["id"]


def test_revive_after_crash_makes_one_run(env, device):
    """M3-4: 작업 중 core가 죽었다 뜨면 작업이 되살아나 run이 정확히 1개."""
    closed_segment(env, device)
    core = make_core()
    conn = env.conn("core")
    with conn.transaction():
        job = jobs.claim(conn, ("analysis",), worker="dead", now=clock.now())
    assert job is not None
    env.clock.advance(600 + 301)
    with conn.transaction():
        assert jobs.revive_stale(conn, now=clock.now(), stale_after_s=600 + 300) == 1
    env.clock.advance(2 * 60)
    assert run_jobs(env, core, ("analysis",)) == ["done"]
    assert len(_runs(env)) == 1


def test_segment_changed_during_analysis_requeues(env, device):
    """M3-5: 분석 중에 구간에 묶음이 더해지면 run은 저장되고 analysis 작업이 하나 더 들어감."""
    seg = closed_segment(env, device)
    core = make_core()
    real = core.client.analyze

    def analyze_and_bump(context, chunks):
        reply = real(context, chunks)
        env.q("UPDATE segment.segment SET revision = revision + 1 WHERE id = %s", (seg,))
        return reply

    core.client.analyze = analyze_and_bump
    assert run_jobs(env, core, ("analysis",), limit=1) == ["done"]
    assert len(_runs(env)) == 1
    states = env.q("SELECT state FROM pipeline.job WHERE kind = 'analysis' ORDER BY id")
    assert [s["state"] for s in states] == ["done", "queued"]


def test_waits_for_installation(env, device):
    """M3-6 (AN-3): 배치가 없으면 10분씩 미루고, 블록이 오면 그 좌표로, 1시간이 지나면 anchors = null."""
    seg = closed_segment(env, device, surveyed=True)
    inst = env.one("SELECT id FROM segment.installation")["id"]
    env.q("UPDATE segment.segment SET installation_id = NULL WHERE id = %s", (seg,))
    core = make_core()
    assert run_jobs(env, core, ("analysis",)) == ["waiting_installation"]
    job = env.one("SELECT state, attempts, last_error, run_after FROM pipeline.job WHERE kind = 'analysis'")
    assert (job["state"], job["attempts"], job["last_error"]) == ("queued", 0, "waiting_installation")
    assert job["run_after"] == clock.now() + timedelta(minutes=10)
    env.q("UPDATE segment.segment SET installation_id = %s WHERE id = %s", (inst, seg))  # SG-8이 채운 것처럼
    env.clock.advance(600)
    assert run_jobs(env, core, ("analysis",)) == ["done"]
    anchors = _runs(env)[0]["context"]["anchors"]
    assert anchors is not None and len(anchors) == 4

    # 1시간이 지나면 기다리지 않음
    env.q("UPDATE segment.segment SET installation_id = NULL WHERE id = %s", (seg,))
    jobs.enqueue_analysis(env.conn("core"), seg, now=clock.now())
    env.clock.advance(3601)
    assert run_jobs(env, core, ("analysis",)) == ["done"]
    assert _runs(env)[-1]["context"]["anchors"] is None


def test_open_segment_no_run(env, device):
    """AN-2: 작업을 시작할 때 구간이 열려 있으면 run 없이 done."""
    seg = closed_segment(env, device)
    env.q("UPDATE segment.segment SET state = 'open', closed_reason = NULL WHERE id = %s", (seg,))
    assert run_jobs(env, make_core(), ("analysis",)) == ["segment_open"]
    assert _runs(env) == []


def test_version_gate(env, device):
    """7.4: /v1/version을 확인하기 전에는 analysis 작업을 가져가지 않음."""
    from uwbserver.process.core import Core

    closed_segment(env, device)
    core = Core(client=fake_c2())
    assert core.work_once(env.conn("core"), ("analysis",), "w") is None
    core._check_c2()
    assert core.work_once(env.conn("core"), ("analysis",), "w") == "done"


@pytest.mark.slow
def test_speed_one_hour_run(env, device, capsys):
    """M3-8: 1시간 구간 run의 DB 트랜잭션 50 ms 이하. analysis_done 로그(PL-5)의 db_ms로 잼."""
    closed_segment(env, device, duration_s=3590)
    core = make_core()
    capsys.readouterr()
    t0 = time.perf_counter()
    assert run_jobs(env, core, ("analysis",)) == ["done"]
    total_ms = (time.perf_counter() - t0) * 1000
    (line,) = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"analysis_done"' in x]
    print(f"analysis total {total_ms:.0f} ms, db {line['db_ms']} ms, files {line['file_ms']} ms, bouts {line['bouts']}")
    assert line["db_ms"] <= 50


def test_frameless_chunk_inside_segment_still_raw(env, device):
    """AN-4: 구간 seq 범위 안의 프레임 없는 묶음(Tag가 잠깐 꺼진 동안의 health 묶음)은 구간에 들지 않으므로
    raw 판정과 보내는 묶음에서 뺌."""
    from tests.support import Feeder
    from tools import fake_head

    head = fake_head.FakeHead()
    up = fake_head.Uploader(head)
    reqs = up.run(100)
    boot = head.tags[0].boot_id
    head.set_tag(0, on=False)  # 전파가 닿지 않는 50초(재부팅 아님)
    reqs += up.run(50)
    head.set_tag(0, on=True, boot_id=boot)
    reqs += up.run(100)
    f = Feeder(env, {fake_head.TEST_DEVICE_MAC: device})
    f.send(reqs)
    f.idle(120)
    seg = env.scalar("SELECT id FROM segment.segment")
    s = env.one("SELECT first_seq, last_seq, n_chunks FROM segment.segment WHERE id = %s", (seg,))
    inside = env.scalar(
        "SELECT count(*) FROM intake.chunk WHERE chunk_seq BETWEEN %s AND %s AND n_range = 0 AND n_imu_frames = 0",
        (s["first_seq"], s["last_seq"]),
    )
    assert inside >= 1, "시나리오가 구간 안에 프레임 없는 묶음을 만들어야 함"
    assert env.scalar("SELECT count(*) FROM segment.segment") == 1
    assert run_jobs(env, make_core(), ("analysis",)) == ["done"]
    run = _runs(env)[0]
    assert (run["input"], run["n_chunks_sent"]) == ("raw", s["n_chunks"])
