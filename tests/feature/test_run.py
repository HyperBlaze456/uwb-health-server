"""M8 특징 풀기 (FT-1–FT-6). SPEC 14.10 M8 시험 1, 3, 4, 6과 작업 실패."""

import gzip
import json
import time

import psycopg
import pytest

from tests.feature.conftest import patched_core
from tests.support import closed_segment, make_core, run_jobs
from tools import unpack_features
from uwbserver.pipeline import clock, files, jobs, metrics

pytestmark = pytest.mark.db

RECORD_COLS = "bout_id, kind, idx, side, start_at, end_at, v"


def _run(env) -> dict:
    return env.one("SELECT * FROM analysis.run ORDER BY id DESC LIMIT 1")


def _result(run: dict) -> dict:
    return json.loads(gzip.decompress(files.resolve(run["result_path"]).read_bytes()))


def _records(env) -> list[dict]:
    return env.q(f"SELECT {RECORD_COLS} FROM feature.record ORDER BY bout_id, kind, idx")


def _unpack(env, run_id: int) -> dict:
    return env.one("SELECT * FROM feature.unpack WHERE run_id = %s", (run_id,))


def _feature_jobs(env) -> list[dict]:
    return env.q("SELECT run_id, state, attempts, last_error FROM pipeline.job WHERE kind = 'feature' ORDER BY id")


def test_done_run_is_unpacked_as_emitted(env, device):
    """M8-1 (FT-1, FT-2): done run → feature 작업 → unpack 행 하나와 줄. 줄 수와 v가 결과 원문과 같음."""
    closed_segment(env, device)
    core = make_core()
    assert run_jobs(env, core, ("analysis",)) == ["done"]
    run = _run(env)
    assert _feature_jobs(env) == [{"run_id": run["id"], "state": "queued", "attempts": 0, "last_error": None}]
    assert run_jobs(env, core, ("feature",)) == ["done"]
    assert _feature_jobs(env)[0]["state"] == "done"

    doc = _result(run)
    bout_ids = {r["idx"]: r["id"] for r in env.q("SELECT id, idx FROM analysis.bout WHERE run_id = %s", (run["id"],))}
    want = [(bout_ids[k], "stride", i, s) for k, b in enumerate(doc["bouts"]) for i, s in enumerate(b["strides"])]
    assert len(want) > 20  # 가짜 gaitfusion이 걷기 구간·발마다 보폭 줄을 냄
    got = [(r["bout_id"], r["kind"], r["idx"], r["v"]) for r in _records(env)]
    assert got == want
    for r in _records(env):
        assert r["side"] == r["v"]["side"]
        assert r["start_at"] == run["origin_at"] + _s(r["v"]["start_s"])
        assert r["end_at"] == run["origin_at"] + _s(r["v"]["end_s"])

    u = _unpack(env, run["id"])
    assert (u["n_records"], u["n_dropped_records"], u["n_bad_values"]) == (len(want), 0, 0)
    assert (u["unknown_fields"], u["unknown_tables"]) == ([], [])
    assert u["catalog_version"] == metrics.load().version
    assert u["formats"]["strides"] == "gaitfusion.stride/1"
    assert u["core_version"] == env.settings.core_version and u["unpacked_at"] == clock.now()


def _s(seconds: float):
    from datetime import timedelta

    return timedelta(microseconds=round(seconds * 1_000_000))


def test_skipped_run_has_no_feature_job(env, device):
    """FT-1: skipped run은 넣지 않음."""
    closed_segment(env, device)
    assert run_jobs(env, make_core("422"), ("analysis",)) == ["skipped"]
    assert _feature_jobs(env) == []


def test_run_without_bouts_is_unpacked_empty(env, device):
    closed_segment(env, device)
    core = patched_core(lambda doc: doc.update(bouts=[]))
    assert run_jobs(env, core, ("analysis", "feature")) == ["done", "done"]
    u = _unpack(env, _run(env)["id"])
    assert (u["n_records"], u["n_dropped_records"]) == (0, 0)


def test_bad_start_or_end_drops_only_that_record(env, device):
    """M8-3 (FT-3): 시작·끝이 틀린 줄만 빠지고 n_dropped_records에 셈. idx는 배열 안 순번 그대로."""

    def patch(doc: dict) -> None:
        s = doc["bouts"][0]["strides"]
        s[1]["end_s"] = s[1]["start_s"] - 0.5
        s[2]["start_s"] = None
        s[3]["side"] = "middle"

    closed_segment(env, device)
    core = patched_core(patch)
    assert run_jobs(env, core, ("analysis", "feature")) == ["done", "done"]
    run = _run(env)
    n_all = sum(len(b["strides"]) for b in _result(run)["bouts"])
    u = _unpack(env, run["id"])
    assert (u["n_records"], u["n_dropped_records"]) == (n_all - 3, 3)
    first_bout = env.scalar("SELECT id FROM analysis.bout WHERE run_id = %s AND idx = 0", (run["id"],))
    idxs = [r["idx"] for r in _records(env) if r["bout_id"] == first_bout]
    assert idxs[:3] == [0, 4, 5]


def test_unpacking_again_gives_the_same_rows(env, device):
    """M8-4 (FT-5): 같은 run을 다시 풀면 행이 같음(id 제외). 옛 행은 지워짐."""
    closed_segment(env, device)
    core = make_core()
    run_jobs(env, core, ("analysis", "feature"))
    run = _run(env)
    before = _records(env)
    ids_before = {r["id"] for r in env.q("SELECT id FROM feature.record")}
    unpack_before = _unpack(env, run["id"])
    env.clock.advance(60)
    conn = env.conn("core")
    with conn.transaction():
        assert jobs.enqueue_feature(conn, run["id"], now=clock.now())
    assert run_jobs(env, core, ("feature",)) == ["done"]
    assert _records(env) == before
    assert not ids_before & {r["id"] for r in env.q("SELECT id FROM feature.record")}
    after = _unpack(env, run["id"])
    assert after["unpacked_at"] > unpack_before["unpacked_at"]
    assert {k: v for k, v in after.items() if k != "unpacked_at"} == {
        k: v for k, v in unpack_before.items() if k != "unpacked_at"
    }


def test_unpack_features_tool_fills_past_runs(env, device):
    """M8-4 (FT-6, TL-9): 풀린 행이 없는 지난 run을 도구로 채움. --since는 그 뒤에 끝난 run만."""
    seg = closed_segment(env, device)
    core = make_core()
    run_jobs(env, core, ("analysis",))
    first = _run(env)
    env.clock.advance(3600)
    conn = env.conn("core")
    with conn.transaction():
        jobs.enqueue_analysis(conn, seg, now=clock.now())  # 재분석: 같은 구간의 새 run
    run_jobs(env, core, ("analysis",))
    second = _run(env)
    assert second["id"] != first["id"]
    run_jobs(env, core, ("feature",))
    want = _records(env)
    env.q("DELETE FROM feature.record; DELETE FROM feature.unpack; DELETE FROM pipeline.job WHERE kind = 'feature'")

    assert unpack_features.main(["--since", (second["finished_at"]).isoformat()]) == 0
    assert [j["run_id"] for j in _feature_jobs(env)] == [second["id"]]
    assert unpack_features.main(["--all"]) == 0  # 이미 대기 중인 run은 다시 넣지 않음
    assert sorted(j["run_id"] for j in _feature_jobs(env)) == [first["id"], second["id"]]
    assert run_jobs(env, core, ("feature",)) == ["done", "done"]
    assert _records(env) == want


def test_unpack_features_needs_since_or_all(env):
    with pytest.raises(SystemExit):
        unpack_features.main([])
    with pytest.raises(SystemExit):
        unpack_features.main(["--all", "--since", "2026-10-01T00:00:00Z"])


def test_unpack_features_skips_skipped_runs(env, device):
    closed_segment(env, device)
    run_jobs(env, make_core("422"), ("analysis",))
    assert unpack_features.main(["--all"]) == 0
    assert _feature_jobs(env) == []


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_missing_or_changed_result_file_fails_without_retry(env, device, damage):
    """결과 원문이 없거나 sha256이 run과 다르면 작업 failed(재시도 없음). run·걷기 구간·궤적·원본 보관은 그대로."""
    closed_segment(env, device)
    core = make_core()
    run_jobs(env, core, ("analysis", "archive"))
    run = _run(env)
    path = files.resolve(run["result_path"])
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(gzip.compress(b'{"bouts": []}'))
    snapshot = _phase1(env)
    assert run_jobs(env, core, ("feature",)) == ["failed"]
    (job,) = _feature_jobs(env)
    assert job["state"] == "failed" and job["attempts"] == 0
    assert job["last_error"].startswith("result_missing" if damage == "missing" else "result_invalid: sha256")
    assert env.q("SELECT * FROM feature.unpack") == [] and env.q("SELECT * FROM feature.record") == []
    assert _phase1(env) == snapshot


def _phase1(env) -> dict:
    return {
        t: env.q(f"SELECT * FROM {t} ORDER BY 1")
        for t in ("analysis.run", "analysis.bout", "analysis.trajectory", "archive.range", "archive.cut")
    }


def test_run_gone_fails(env):
    conn = env.conn("core")
    with conn.transaction():
        jobs.enqueue_feature(conn, 999, now=clock.now())
    assert run_jobs(env, make_core(), ("feature",)) == ["no_run"]
    assert _feature_jobs(env)[0]["state"] == "failed"


def test_db_constraint_feature_job_needs_run(env):
    """009: feature 작업은 run_id가 있어야 하고, analysis 작업은 run_id가 없어야 함."""
    with pytest.raises(psycopg.errors.CheckViolation):
        env.q("INSERT INTO pipeline.job (kind) VALUES ('feature')")
    with pytest.raises(psycopg.errors.CheckViolation):
        env.q("INSERT INTO pipeline.job (kind, segment_id, run_id) VALUES ('analysis', 1, 1)")
    env.q("INSERT INTO pipeline.job (kind, run_id) VALUES ('feature', 1)")
    with pytest.raises(psycopg.errors.UniqueViolation):
        env.q("INSERT INTO pipeline.job (kind, run_id) VALUES ('feature', 1)")
    env.q("INSERT INTO pipeline.job (kind, run_id) VALUES ('archive', 1)")


@pytest.mark.slow
def test_one_hour_run_unpacks_within_200ms(env, device, capsys):
    """M8-6: 1시간 동안 양발로 걸은 run(보폭 줄 약 6500개)을 푸는 트랜잭션이 200 ms 이하."""
    period = 1.1

    def patch(doc: dict) -> None:
        template = doc["bouts"][0]["strides"][0]
        rows = []
        for k in range(int(3600 / period)):
            for side, shift in (("left", 0.0), ("right", period / 2)):
                t = k * period + shift
                rows.append(template | {"side": side, "start_s": round(t, 4), "end_s": round(t + period, 4)})
        doc["bouts"][0]["strides"] = rows

    closed_segment(env, device)
    core = patched_core(patch)
    assert run_jobs(env, core, ("analysis",)) == ["done"]
    capsys.readouterr()
    t0 = time.perf_counter()
    assert run_jobs(env, core, ("feature",)) == ["done"]
    total_ms = (time.perf_counter() - t0) * 1000
    (line,) = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"feature_done"' in x]
    n = _unpack(env, _run(env)["id"])["n_records"]
    assert n >= 6500
    print(f"\nM8-6: records={n} tx_ms={line['tx_ms']} handle_ms={total_ms:.1f} parse_ms={line['parse_ms']}")
    assert line["tx_ms"] <= 200, line



# --- FT-8: 푼 트랜잭션에서 summary 작업 -------------------------------------------------------


def _summary_jobs(env) -> list[dict]:
    return env.q("SELECT device_id, date_from, date_to, state FROM pipeline.job WHERE kind = 'summary' ORDER BY id")


def _bout_days(env, run_id: int) -> list:
    return sorted({clock.local_date(r["start_at"])
                   for r in env.q("SELECT start_at FROM analysis.bout WHERE run_id = %s", (run_id,))})  # fmt: skip


def test_unpack_enqueues_summary_for_the_days_bouts_start(env, device):
    """FT-8: 그 run의 걷기 구간이 시작한 한국 날짜 범위로 summary {장치, 날짜} 하나."""
    closed_segment(env, device)
    core = make_core()
    run_jobs(env, core, ("analysis",))
    assert _summary_jobs(env) == []  # 풀기 전에는 없음
    run_jobs(env, core, ("feature",))
    days = _bout_days(env, _run(env)["id"])
    assert days
    assert _summary_jobs(env) == [
        {"device_id": device.device.id, "date_from": days[0], "date_to": days[-1], "state": "queued"}
    ]


def test_run_without_bouts_enqueues_no_summary(env, device):
    closed_segment(env, device)
    run_jobs(env, patched_core(lambda doc: doc.update(bouts=[])), ("analysis", "feature"))
    assert _summary_jobs(env) == []


def test_reanalysis_also_covers_days_of_earlier_runs(env, device):
    """FT-8: 재분석이면 옛 run의 걷기 구간이 걸친 날짜도 합침. 새 run에 걷기 구간이 없어도 옛 날짜를 다시 계산하게 함."""
    seg = closed_segment(env, device)
    run_jobs(env, make_core(), ("analysis", "feature"))
    old_days = _bout_days(env, _run(env)["id"])
    env.q("UPDATE pipeline.job SET state = 'done' WHERE kind = 'summary'")
    env.clock.advance(60)
    conn = env.conn("core")
    with conn.transaction():
        assert jobs.enqueue_analysis(conn, seg, now=clock.now())
    run_jobs(env, patched_core(lambda doc: doc.update(bouts=[])), ("analysis", "feature"))
    assert _bout_days(env, _run(env)["id"]) == []
    assert _summary_jobs(env)[-1] == {
        "device_id": device.device.id, "date_from": old_days[0], "date_to": old_days[-1], "state": "queued"
    }
