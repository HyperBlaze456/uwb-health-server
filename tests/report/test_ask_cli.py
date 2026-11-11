"""tools/ask.py의 시험 (SPEC TL-12). DB는 시험 DB, 논문 저장소는 tmp 폴더, 모델은 --fake 대역. 네트워크 없음.

키가 없을 때의 시험은 진짜 SDK 길을 타되, SDK의 주소를 닫힌 로컬 포트로 두고 HOME을 빈 폴더로 바꿔 밖으로 나갈 수 없게 합니다.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.summary.conftest import bout, device, run, segment, subject, walk, wear
from tools import ask
from uwbserver.pipeline import clock
from uwbserver.report import papers, rules, writer

pytestmark = pytest.mark.db

DAY = date(2026, 10, 27)
FROM = clock.day_start(DAY).isoformat()
TO = clock.day_start(DAY + timedelta(days=1)).isoformat()


@pytest.fixture
def subject_id(env) -> int:
    s = subject(env, "S01")
    dev = device(env, "head-aabbccddeeff")
    wear(env, s, dev, clock.day_start(DAY) - timedelta(days=1))
    start = clock.day_start(DAY) + timedelta(hours=10)
    seg = segment(env, dev, start, start + timedelta(hours=1))
    bout(env, run(env, seg), start + timedelta(minutes=5), 120, walk(120))
    return s


@pytest.fixture
def store(tmp_path) -> Path:
    root = tmp_path / "papers"
    papers.add_paper(root, papers.Paper("hausdorff2001", "Gait variability", ["Hausdorff JM"], 2001, "10.1/h"),
                     "Stride time variability is linked with falls.\n\nGait speed is lower in older adults.")  # fmt: skip
    papers.build_index(root, papers.NgramEmbedder())
    return root


def no_network_no_key(monkeypatch, tmp_path):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")


def test_show_context_without_key_or_papers(env, subject_id, capsys, monkeypatch, tmp_path):
    no_network_no_key(monkeypatch, tmp_path)
    code = ask.main(["--subject", str(subject_id), "--from", FROM, "--to", TO, "--show-context",
                     "--papers-root", str(tmp_path / "nowhere")])  # fmt: skip
    assert code == 0
    ctx = json.loads(capsys.readouterr().out)
    assert ctx["schema"] == "uwbserver.report_input/1" and ctx["subject"]["code"] == "S01"
    assert ctx["coverage"]["bout_count"] == 1


def test_fake_question_with_trace(env, subject_id, store, capsys, tmp_path):
    trace = tmp_path / "trace.json"
    code = ask.main(["--subject", str(subject_id), "--from", FROM, "--to", TO, "--fake", "--question", "어땠나요?",
                     "--papers-root", str(store), "--trace-out", str(trace)])  # fmt: skip
    out = capsys.readouterr()
    assert code == 0, out.err
    assert writer.DEMO_LABEL in out.out and "--fake" in out.err
    assert "{v" not in out.out and "[1]" in out.out and "hausdorff2001#" in out.out
    t = json.loads(trace.read_text())
    assert t["model"] == "demo-fake" and t["context"]["subject"]["code"] == "S01"
    searches = [e for e in t["events"] if e["type"] == "search"]
    assert len(searches) == 1 and searches[0]["results"][0]["passage"].startswith("hausdorff2001#")


def test_fake_report(env, subject_id, store, capsys):
    code = ask.main(["--subject", str(subject_id), "--from", FROM, "--to", TO, "--fake", "--report",
                     "--papers-root", str(store)])  # fmt: skip
    out = capsys.readouterr()
    assert code == 0, out.err
    heads = [line for line in out.out.splitlines() if line.startswith("#")]
    assert heads[0] == "# 보행 보고서" and heads[-1] == "## 한계와 근거 문헌" and len(heads) == 8
    assert rules.DISCLAIMER in out.out


def test_missing_key_is_a_clear_message(env, subject_id, store, capsys, monkeypatch, tmp_path):
    no_network_no_key(monkeypatch, tmp_path)
    code = ask.main(["--subject", str(subject_id), "--from", FROM, "--to", TO, "--question", "q",
                     "--papers-root", str(store)])  # fmt: skip
    err = capsys.readouterr().err
    assert code == 1
    assert "ANTHROPIC_API_KEY" in err and "Traceback" not in err


def test_bad_period_and_unknown_subject(env, subject_id, capsys):
    assert ask.main(["--subject", str(subject_id), "--from", TO, "--to", FROM, "--show-context"]) == 2
    assert ask.main(["--subject", "999", "--from", FROM, "--to", TO, "--show-context"]) == 1
    assert "999" in capsys.readouterr().err
