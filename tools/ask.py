"""대화 코어의 명령줄 입구 (SPEC TL-12, RP-0). 착용자와 기간의 지표 값을 맥락으로 모델과 묻고 답합니다.

    uv run python tools/ask.py --subject ID --from ISO --to ISO --show-context
    uv run python tools/ask.py --subject ID --from ISO --to ISO --report
    uv run python tools/ask.py --subject ID --from ISO --to ISO --question "걸음이 고른 편인가요?"
    uv run python tools/ask.py --subject ID --from ISO --to ISO            # 터미널에서 이어 묻기, /report, /quit

--show-context  맥락(uwbserver.report_input/1) JSON을 찍고 끝냄. 모델과 논문 저장소, API 키가 필요 없음
--report        보고서(여덟 부분의 Markdown)를 표준 출력에
--question 글   질문 하나의 답(자리 표시를 풀고 근거 문헌을 붙임). 검사에 걸린 것은 표준 오류에
(둘 다 없음)    한 줄에 질문 하나. '/report'는 보고서, '/quit' 또는 EOF는 끝
--papers-root   논문 저장소 뿌리(기본 DATA_ROOT/papers). 색인이 있어야 함(tools/papers.py build)
--trace-out 파일 대화의 기록(맥락, 주고받은 말, 찾은 기록)을 JSON으로 씀(RP-7)
--fake          진짜 모델 대신 정해진 대역: 저장소에서 한 번 찾고 v1 하나와 대목 하나를 넣은 짧은 글을 씀. 키가 필요 없음

시각은 시간대를 붙여 씁니다(예 2026-11-03T00:00:00+09:00). 기간은 [from, to)입니다.
모델은 REPORT_MODEL(기본 claude-opus-5-5), 깊이는 REPORT_EFFORT(기본 high), 질문 하나의 찾기 한도는 REPORT_MAX_SEARCHES(기본 12).
API 키는 SDK가 ANTHROPIC_API_KEY에서 읽습니다. DB 역할은 api_rw(읽기만)입니다.
바깥으로 나가는 것은 맥락(착용자 코드, 지표 값)과 찾은 논문 대목뿐입니다(RP-8).

종료 코드: 0 = 정상, 1 = 모델·검사·착용자 문제(메시지를 봄), 2 = 인자 오류나 DB·논문 저장소를 쓸 수 없음.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import psycopg

from uwbserver.pipeline import db, log, metrics, settings
from uwbserver.report import chat, papers, rules, writer


def _aware(text: str) -> datetime:
    try:
        t = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 time: {text!r}") from None
    if t.tzinfo is None:
        raise argparse.ArgumentTypeError(f"add a time zone (e.g. {text}+09:00): {text!r}")
    return t


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--subject", type=int, required=True, help="subject id (registry.subject.id)")
    p.add_argument("--from", dest="from_at", type=_aware, required=True, help="period start, ISO with time zone")
    p.add_argument("--to", dest="to_at", type=_aware, required=True, help="period end (exclusive)")
    what = p.add_mutually_exclusive_group()
    what.add_argument("--show-context", action="store_true", help="print the context JSON and exit")
    what.add_argument("--report", action="store_true", help="write the report to stdout")
    what.add_argument("--question", help="ask one question")
    p.add_argument("--papers-root", help="paper store root (default: DATA_ROOT/papers)")
    p.add_argument("--trace-out", help="write the conversation trace (JSON) to this file")
    p.add_argument("--fake", action="store_true", help="use the built-in scripted fake model (no API key needed)")
    return p


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _interactive(c: chat.Chat) -> int:
    print("questions, one per line. /report writes the report, /quit ends.", file=sys.stderr)
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            return 0
        if not line:
            continue
        if line == "/quit":
            return 0
        try:
            answer = c.report() if line == "/report" else c.ask(line)
        except writer.MissingCredentials as e:
            _err(f"error: {e}")
            return 1
        except (chat.ChatError, writer.WriterError) as e:
            _err(f"error: {e}")
            continue
        print(answer.text)
        for problem in answer.problems:
            _err(f"check: {problem}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log.setup("ask")
    if args.to_at <= args.from_at:
        _err("--to must be after --from")
        return 2
    catalog = metrics.load()
    try:
        with db.connect("api") as conn:
            ctx = chat.load_context(conn, catalog, args.subject, args.from_at, args.to_at)
    except LookupError as e:
        _err(f"error: {e}")
        return 1
    except (psycopg.Error, OSError) as e:
        _err(f"error: cannot read the database: {type(e).__name__}: {e}")
        return 2
    if args.show_context:
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
        return 0
    if args.report and not ctx["coverage"]["bout_count"]:
        _err("no walking in this period: no report is written (RP-3)")
        return 1

    root = Path(args.papers_root) if args.papers_root else papers.default_root()
    try:
        index = papers.load_index(root, None)
        paper_list = papers.read_papers(root)
        version = papers.papers_version(root)
    except (papers.PaperStoreError, OSError) as e:
        _err(f"error: paper store {root}: {e}")
        return 2

    s = settings.get()
    if args.fake:
        _err("[--fake: scripted fake model, the real model is not called]")
        model: writer.Model = writer.DemoModel()
    else:
        model = writer.ClaudeModel(s.report_model, s.report_effort)
    c = chat.Chat(ctx, index, model, papers=paper_list, sources=rules.metric_sources(catalog),
                  papers_version=version, max_searches=s.report_max_searches)  # fmt: skip
    try:
        if args.report:
            print(c.report().text)
        elif args.question is not None:
            answer = c.ask(args.question)
            print(answer.text)
            for problem in answer.problems:
                _err(f"check: {problem}")
        else:
            return _interactive(c)
        return 0
    except (chat.ChatError, writer.WriterError) as e:
        _err(f"error: {e}")
        return 1
    finally:
        if args.trace_out:
            Path(args.trace_out).write_text(json.dumps(c.trace(), ensure_ascii=False, indent=1, default=str),
                                            encoding="utf-8")  # fmt: skip


if __name__ == "__main__":
    sys.exit(main())
