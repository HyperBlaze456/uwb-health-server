"""지표 목록 계약 시험(SPEC MC-7 2번)의 응답 견본을 만듭니다.

- real_result.json: 진짜 gaitfusion 코드(옆 저장소 ../gaitfusion)의 C2 v1 응답에서 `result` 파트.
  입력은 tests/e2e/data/walk_two_feet.*(양발 합성 보행, 걷기 둘)이고, 서버의 C2 클라이언트로 요청을 만들어
  gaitfusion의 앱(gaitfusion.service.create_app)에 프로세스 안에서 보냅니다. HTTP 포트는 열지 않습니다.
- fake_result.json: 같은 입력을 가짜 gaitfusion(tools/fake_gaitfusion.py)에 보낸 응답의 `result` 파트.
- samples.json: 만든 때의 gaitfusion 커밋과 입력 sha256.

서버 코드와 시험은 이 스크립트를 import하지 않습니다. 결과 파일만 읽습니다. 다시 만들 때는 gaitfusion의 환경으로:

    cd ../gaitfusion && uv run python ../uwb-health-server/tests/contract/metrics/samples/make_samples.py

가짜 견본은 서버 환경에서만 만들 수 있으므로(fastapi) 따로:

    uv run python tests/contract/metrics/samples/make_samples.py --fake
"""

import gzip
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parents[3]
GAITFUSION = SERVER.parent / "gaitfusion"
DATA = SERVER / "tests" / "e2e" / "data"
sys.path[:0] = [str(SERVER / "src"), str(SERVER)]

from uwbserver.analysis import rules  # noqa: E402
from uwbserver.analysis.gaitfusion import Client  # noqa: E402
from uwbserver.pipeline import wire  # noqa: E402


def load_input() -> tuple[dict, bytes, str]:
    """(context, C1 묶음 배치(u32 길이 + 묶음의 반복, 7.1절 chunks 파트), 배치의 sha256). 서버가 구간 하나를 보낼 때와 같은 꼴."""
    meta = json.loads((DATA / "walk_two_feet.json").read_text())
    batch = gzip.decompress((DATA / "walk_two_feet.c1.gz").read_bytes())
    sha = hashlib.sha256(batch).hexdigest()
    assert sha == meta["sha256"], "walk_two_feet.c1.gz가 walk_two_feet.json과 다름"
    chunks, i = [], 0
    while i < len(batch):
        n = int.from_bytes(batch[i : i + 4], "little")
        chunks.append(batch[i + 4 : i + 4 + n])
        i += 4 + n
    origin = wire.decode_chunk(chunks[0]).header.head_us_start
    anchors = [{"id": k, "xyz_m": a[:3], "bias_m": a[3]} for k, a in enumerate(meta["anchors"])]
    ctx = rules.build_context(segment_id=1, run_id=1, sides=meta["sides"], origin_head_us=origin, anchors=anchors,
                              subject=None, params={})
    return ctx, batch, sha


def result_of(client: Client, ctx: dict, body: bytes) -> dict:
    reply = client.analyze(ctx, body)
    assert reply.status == 200 and reply.parts, (reply.status, reply.reason)
    (part,) = [p for p in reply.parts if p.name == "result"]
    return json.loads(part.data)


def write(name: str, doc: dict) -> None:
    (HERE / name).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


def real() -> None:
    from starlette.testclient import TestClient

    commit = subprocess.run(["git", "-C", str(GAITFUSION), "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(GAITFUSION), "status", "--porcelain", "gaitfusion"],
                                capture_output=True, text=True, check=True).stdout.strip())
    os.environ["GAITFUSION_BUILD"] = commit[:7]
    os.environ.setdefault("GAITFUSION_WORKERS", "2")
    from gaitfusion import service

    ctx, body, sha = load_input()
    with TestClient(service.create_app()) as http:
        doc = result_of(Client("http://testserver", 600, http=http), ctx, body)
    write("real_result.json", doc)
    meta_path = HERE / "samples.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta["real_result.json"] = {
        "made_by": "tests/contract/metrics/samples/make_samples.py",
        "gaitfusion_commit": commit + ("-dirty" if dirty else ""),
        "input": "tests/e2e/data/walk_two_feet.c1.gz", "input_sha256": sha,
        "context": {k: ctx[k] for k in ("sides", "origin_head_us", "params")}, "trimmed": False,
    }  # fmt: skip
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")


def fake() -> None:
    from starlette.testclient import TestClient

    from tools import fake_gaitfusion

    ctx, body, sha = load_input()
    with TestClient(fake_gaitfusion.create_app("ok")) as http:
        doc = result_of(Client("http://testserver", 60, http=http), ctx, body)
    write("fake_result.json", doc)
    meta_path = HERE / "samples.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta["fake_result.json"] = {
        "made_by": "tests/contract/metrics/samples/make_samples.py --fake",
        "service": "tools/fake_gaitfusion.py (mode ok)",
        "input": "tests/e2e/data/walk_two_feet.c1.gz", "input_sha256": sha,
    }  # fmt: skip
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    fake() if "--fake" in sys.argv[1:] else real()
