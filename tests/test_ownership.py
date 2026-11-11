"""2.3절 경계 규칙: 각 단계의 SQL은 자기 스키마에만 씁니다(11절 공통).

store.py(와 pipeline/jobs.py)의 INSERT·UPDATE·DELETE 대상 스키마를 글자로 찾아 봅니다.
예외: archive가 30일 지난 intake 행을 지움(AR-6). 작업 넣기는 pipeline.jobs를 거침.
"""

import re
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "uwbserver"
WRITE = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|COPY)\s+([a-z_]+)\.([a-z_]+)", re.I)
ALLOWED_EXTRA = {"archive": {"intake"}}
STAGES = ["registry", "intake", "segment", "analysis", "archive", "feature", "summary"]


def _writes(path: Path) -> set[str]:
    return {m.group(1) for m in WRITE.finditer(path.read_text())}


@pytest.mark.parametrize("stage", STAGES)
def test_store_writes_only_own_schema(stage):
    store = SRC / stage / "store.py"
    if not store.exists():
        pytest.skip(f"{stage}/store.py 아직 없음")
    bad = _writes(store) - {stage} - ALLOWED_EXTRA.get(stage, set())
    assert not bad, f"{stage}/store.py가 남의 스키마에 씀: {bad}"


@pytest.mark.parametrize("stage", STAGES)
def test_no_sql_writes_outside_store(stage):
    """SQL 쓰기는 store.py에만(3.2절 파일 역할)."""
    for path in (SRC / stage).glob("*.py"):
        if path.name == "store.py":
            continue
        assert not _writes(path), f"{path.relative_to(SRC)}에 SQL 쓰기가 있음"


def test_pipeline_jobs_writes_pipeline_only():
    assert _writes(SRC / "pipeline" / "jobs.py") <= {"pipeline"}


def test_import_rules():
    """import-linter (3.2절)."""
    r = subprocess.run(["lint-imports"], capture_output=True, text=True, cwd=SRC.parents[1])
    assert r.returncode == 0, r.stdout + r.stderr
