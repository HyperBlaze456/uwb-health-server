"""앱 API (14.8절): GET /v1/bouts/{id}/records, GET /v1/runs/{id}의 unpack."""

import pytest

from tests.support import closed_segment, make_core, run_jobs
from uwbserver.pipeline import metrics

pytestmark = pytest.mark.db


def _bouts(api, people) -> list[dict]:
    r = api.get(f"/v1/subjects/{people['A']}/bouts?from=2026-10-25T00:00:00Z", headers=people["a"])
    assert r.status_code == 200, r.text
    return r.json()


def test_records_of_bout(env, api, admin, people, device):
    closed_segment(env, device)
    core = make_core()
    run_jobs(env, core, ("analysis",))
    bout = min(_bouts(api, people), key=lambda b: b["start_at"])
    url = f"/v1/bouts/{bout['id']}/records"

    # 아직 풀지 않음
    body = api.get(url, headers=people["a"]).json()
    assert body == {"bout_id": bout["id"], "run_id": bout["run_id"], "kind": "stride", "unpacked": False,
                    "format": None, "records": []}  # fmt: skip
    run_jobs(env, core, ("feature",))

    body = api.get(url, headers=people["a"]).json()
    assert (body["unpacked"], body["kind"], body["format"]) == (True, "stride", "gaitfusion.stride/1")
    recs = body["records"]
    stored = env.q("SELECT idx, side, v FROM feature.record WHERE bout_id = %s ORDER BY idx", (bout["id"],))
    assert [r["idx"] for r in recs] == [s["idx"] for s in stored] and recs
    catalog = metrics.load()
    for r, s in zip(recs, stored, strict=True):
        assert set(r) == {"idx", "side", "start_at", "end_at", "values"} and r["side"] == s["side"]
        assert r["values"] == metrics.normalize(catalog, "stride", "gaitfusion.stride/1", s["v"])
        assert set(r["values"]) <= {m.id for m in catalog.metrics_of("stride")}
    assert recs[0]["start_at"] < recs[-1]["start_at"]

    assert api.get(url + "?kind=stride", headers=admin).json() == body
    assert api.get(url + "?kind=turn", headers=admin).json()["records"] == []  # 목록에 있는 표, 줄은 없음
    assert api.get(url + "?kind=gaze", headers=admin).status_code == 422
    assert api.get(url, headers=people["b"]).status_code == 404  # 남의 걷기 구간
    assert api.get("/v1/bouts/999999/records", headers=admin).status_code == 404
    assert api.get(url).status_code == 401


def test_run_shows_unpack(env, api, admin, people, device):
    closed_segment(env, device)
    core = make_core()
    run_jobs(env, core, ("analysis",))
    run_id = env.scalar("SELECT id FROM analysis.run")
    assert api.get(f"/v1/runs/{run_id}", headers=admin).json()["unpack"] is None
    run_jobs(env, core, ("feature",))
    u = api.get(f"/v1/runs/{run_id}", headers=admin).json()["unpack"]
    n = env.scalar("SELECT count(*) FROM feature.record")
    assert {k: u[k] for k in ("n_records", "n_dropped_records", "n_bad_values", "unknown_fields", "unknown_tables")} == {
        "n_records": n, "n_dropped_records": 0, "n_bad_values": 0, "unknown_fields": [], "unknown_tables": []}  # fmt: skip
    assert u["catalog_version"] == metrics.load().version and u["formats"]["strides"] == "gaitfusion.stride/1"
    assert u["unpacked_at"]
    assert api.get(f"/v1/runs/{run_id}", headers=people["a"]).status_code == 403
