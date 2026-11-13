"""준비된 평가 결과(report/data/*.json)를 본문 조각으로 바꿉니다. 결과 파일이 없으면 그 조각은 None(본문에 빈 자리가 들어감)."""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
FIG = Path(__file__).resolve().parents[1] / "figures" / "out"

FAULT_NAMES = {
    "citation_not_retrieved": "검색하지 않은 단락의 인용",
    "value_id_unknown": "없는 값 번호",
    "number_with_unit": "단위와 함께 직접 쓴 수치",
    "direction_reversed": "반대로 쓴 증감 방향",
    "summary_detail_value": "요약에 쓴 상세 값",
    "number_without_unit": "단위 없이 쓴 수치",
    "unsupported_citation": "내용과 무관한 단락의 인용",
    "overclaim_wording": "진단·권고의 말",
}


def _read(name: str):
    path = DATA / name
    return json.loads(path.read_text()) if path.exists() else None


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def load() -> dict:
    retrieval, summary, judge, checks = (_read(n) for n in ("rag_retrieval.json", "rag_runs_summary.json",
                                                             "rag_judge.json", "rag_checks.json"))
    out = {"rag": None, "rag_answer": None, "rag_abstract": None, "mocap": True}
    if not (retrieval and summary):
        return out
    s = retrieval["summary"]
    bm, vec, hyb = (s[k]["all"] for k in ("bm25", "vector:openrouter:openai/text-embedding-3-large",
                                           "hybrid:openrouter:openai/text-embedding-3-large"))
    n_q = bm["n"]
    n_run, n_flag = summary["drafted"], summary["first_draft_flagged"]
    kinds = summary["first_draft_problem_kinds"]
    per = summary["mean_per_report"]
    js = (judge or {}).get("summary")
    fs = (checks or {}).get("faults")
    clean = (checks or {}).get("clean")
    covered = ("citation_not_retrieved", "value_id_unknown", "number_with_unit", "summary_detail_value")
    uncovered = ("number_without_unit", "unsupported_citation", "overclaim_wording")

    def ratio(keys) -> tuple[int, int]:
        return sum(fs[k]["kind_count"] if fs[k]["matching_kinds"] else fs[k]["any_count"] for k in keys), sum(fs[k]["n"] for k in keys)

    def rag(d, c):
        t_s = d.n_table + 1
        f_rep = d.n_figure + 1
        cat = {k: retrieval["summary"][k]["metric"]["hit@5"] for k in (
            "bm25", "vector:openrouter:openai/text-embedding-3-large", "hybrid:openrouter:openai/text-embedding-3-large")}
        n_metric = retrieval["summary"]["bm25"]["metric"]["n"]
        n_known = retrieval["summary"]["bm25"]["known_item"]["n"]
        hyb_ngram = retrieval["summary"]["hybrid:ngram"]["all"]["hit@5"]
        d.p(f"리포트 생성부는 실제 LLM을 호출하여 세 가지를 평가하였다[표 {t_s}]. 첫째는 논문 검색이다. 질의 {n_q}개마다 "
            "정답 논문을 미리 정해 두고, 검색 결과의 상위 5개 단락에 정답 논문이 들어 있는 비율(hit@5)을 측정하였다. 검색 "
            "방식은 단어가 일치하는 정도로 찾는 BM25, 문장의 의미를 벡터로 바꾸어 비교하는 embedding 검색, 두 방식의 "
            "순위를 합친 hybrid 검색이다. hit@5는 embedding 검색이 가장 높았다.")
        d.p(f"둘째는 리포트 작성이다. LLM(Claude Opus)이 가상 착용자의 측정 기간 9개(하루 8개, 일주일 1개)마다 3번씩 "
            f"리포트 {n_run}편을 썼다[그림 {f_rep}]. LLM은 리포트 한 편에서 평균 {per['searches']:.0f}회 검색하고 "
            f"{per['citations']:.0f}번 인용하였다. 이때의 검색은 embedding 검색을 평가하기 전의 설정이었다. BM25에, 글자 "
            f"3개씩의 묶음이 겹치는 정도를 비교하는 벡터를 합친 검색이며, 이 조합의 hit@5는 {_pct(hyb_ngram)}이다. 셋째는 "
            "오류 주입이다. "
            f"검사를 통과한 리포트 {n_run}편에 오류 8종류를 하나씩 넣고, 서버가 검출하는 비율을 측정하였다. 그중 3종류는 "
            "서버의 규칙이 다루지 않는 오류이며, 규칙이 미치는 범위를 확인하려고 넣었다.")
        rows = [["논문 검색: hit@5", f"BM25 {_pct(bm['hit@5'])}, embedding {_pct(vec['hit@5'])}, hybrid {_pct(hyb['hit@5'])}, "
                 f"리포트 작성에 쓴 검색 {_pct(retrieval['summary']['hybrid:ngram']['all']['hit@5'])}",
                 f"질의 {n_q}개"],
                ["첫 초안이 서버 검사에서 걸림", f"{n_flag}편({_pct(n_flag / n_run)}, 1편은 오검출), 다시 쓴 뒤 모두 통과", f"리포트 {n_run}편"]]
        if js:
            ci = js["citations"]
            rows.append(["인용한 단락이 문장을 뒷받침함(다른 회사의 LLM인 Gemini가 판정)",
                         f"뒷받침 {ci['supported']}개({_pct(ci['supported'] / ci['n'])}), 일부 {ci['partial']}개, 아님 {ci['not_supported']}개",
                         f"인용 {ci['n']}개"])
        else:
            rows.append(["인용한 단락이 문장을 뒷받침함(다른 회사의 LLM이 판정)", "(평가)", ""])
        if fs:
            k1, n1 = ratio(covered)
            k2, n2 = fs["direction_reversed"]["kind_count"], fs["direction_reversed"]["n"]
            k3, n3 = ratio(uncovered)
            rows.append(["오류 주입: 검색하지 않은 단락의 인용, 없는 값 번호, 직접 쓴 수치, 요약의 상세 값", f"{k1}/{n1}개 검출({_pct(k1 / n1)})", ""])
            rows.append(["오류 주입: 반대로 쓴 증감 방향", f"{k2}/{n2}개 검출({_pct(k2 / n2)})", ""])
            rows.append(["오류 주입: 단위 없는 수치, 무관한 단락의 인용, 진단하는 표현", f"{k3}/{n3}개 검출(규칙이 다루지 않음)", ""])
            rows.append(["오류를 넣지 않은 리포트가 잘못 걸림", f"{clean['flagged']}/{clean['n']}편", ""])
        else:
            rows.append(["오류 주입: 종류별 검출", "(평가)", ""])
        if fs and js:
            k1, n1 = ratio(covered)
            k2, n2 = fs["direction_reversed"]["kind_count"], fs["direction_reversed"]["n"]
            ci = js["citations"]
            ds = js["direction_sentences"]
            d.p(f"첫 초안에서 걸린 문제는 직접 쓴 수치 {kinds.get('measured_number', 0)}건과 증감 방향 "
                f"{kinds.get('direction', 0)}건이었다. 증감 방향 1건은 논문의 값과 비교한 문장을 평소의 값과 비교한 "
                f"문장으로 잘못 판정한 오검출이다. 오류 주입에서 수치, 인용, 요약 규칙에 해당하는 네 종류의 오류는 "
                f"{n1}개 가운데 {k1}개가 검출되었다. 반대로 쓴 증감 방향은 {n2}개 가운데 {k2}개만 검출되었다. 서버는 증감 "
                "방향을 단어 목록으로 판정하므로, 목록에 없는 표현으로 쓴 문장을 검출하지 못하였다. 인용한 단락이 문장의 "
                "내용을 뒷받침하는지는 규칙으로 검사할 수 없다. 이 평가에서는 다른 회사의 LLM(Gemini)이 최종 리포트의 인용 "
                f"{ci['n']}개를 모두 판정하였고, {_pct(ci['supported'] / ci['n'])}가 뒷받침하는 것으로 판정되었다. 같은 "
                f"LLM은 서버가 통과시킨 문장 {ds['n']}개 가운데 증감 방향이 값과 맞지 않는 문장 "
                f"{ds['judge_mismatch_server_passed']}개를 찾았다.")
        d.table("리포트 생성부의 평가(실제 LLM 호출)", ["항목", "결과", "표본"], rows, [2.2, 2.3, 0.8])
        d.figure(FIG / "fig_report.png", "실제 LLM이 쓴 리포트의 일부. 값은 서버가 넣었고 인용은 검색된 단락이다", 144)

    out["rag"] = rag
    out["rag_answer"] = (f"리포트 생성부에서는 실제 LLM이 쓴 리포트 {n_run}편 가운데 {n_flag}편의 첫 초안이 서버 검사에서 "
                         "걸렸고, 다시 쓴 뒤 모두 통과하였다.")
    out["rag_abstract"] = (f"서버는 LLM이 쓴 리포트의 수치·인용·증감 방향을 검사하며, 실제 LLM이 쓴 리포트 {n_run}편 "
                           f"가운데 {n_flag}편의 첫 초안을 걸러 다시 쓰게 하였다.")
    return out
