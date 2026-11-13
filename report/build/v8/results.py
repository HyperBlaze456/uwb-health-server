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
        d.p(f"리포트 생성부는 실제 모델 호출로 평가하였다[표 {t_s}]. 논문 검색은 정답 논문을 붙인 질의 {n_q}개로 쟀고, 상위 "
            f"5개 단락에 정답 논문이 든 비율은 임베딩 벡터가 가장 높았다. 언어 모델(Claude Opus)에게 합성 착용자의 기간 "
            f"9개에 대해 리포트 {n_run}편을 쓰게 하였다[그림 {f_rep}]. 검색에는 합친 방식을 썼고, 한 편은 평균 검색 "
            f"{per['searches']:.0f}회, 인용 {per['citations']:.0f}개였다. 통과한 리포트에는 오류를 종류별로 하나씩 넣어 "
            "서버가 잡는 비율을 쟀다.")
        rows = [["논문 검색: 상위 5개 적중률", f"BM25 {_pct(bm['hit@5'])}, 임베딩 {_pct(vec['hit@5'])}, 합친 방식 {_pct(hyb['hit@5'])}",
                 f"질의 {n_q}개"],
                ["첫 초안이 서버 검사에 걸림", f"{n_flag}편({_pct(n_flag / n_run)}, 1편은 잘못 잡음), 다시 쓴 뒤 모두 통과", f"리포트 {n_run}편"]]
        if js:
            ci = js["citations"]
            rows.append(["인용이 문장을 뒷받침함(다른 회사 모델 Gemini의 판정)",
                         f"뒷받침 {ci['supported']}개({_pct(ci['supported'] / ci['n'])}), 일부 {ci['partial']}개, 아님 {ci['not_supported']}개",
                         f"인용 {ci['n']}개"])
        else:
            rows.append(["인용이 문장을 뒷받침함(다른 회사 모델의 판정)", "(평가)", ""])
        if fs:
            k1, n1 = ratio(covered)
            k2, n2 = fs["direction_reversed"]["kind_count"], fs["direction_reversed"]["n"]
            k3, n3 = ratio(uncovered)
            rows.append(["오류 주입: 없는 인용, 없는 값 번호, 직접 쓴 수치, 요약의 상세 값", f"{k1}/{n1}개 검출({_pct(k1 / n1)})", ""])
            rows.append(["오류 주입: 반대로 쓴 증감 방향", f"{k2}/{n2}개 검출({_pct(k2 / n2)})", ""])
            rows.append(["오류 주입: 단위 없는 수치, 무관한 단락의 인용, 진단의 말", f"{k3}/{n3}개 검출(검사 대상이 아님)", ""])
            rows.append(["통과한 글을 다시 검사했을 때 걸림", f"{clean['flagged']}/{clean['n']}편", ""])
        else:
            rows.append(["오류 주입: 종류별 검출", "(평가)", ""])
        d.table("리포트 생성부의 평가(실제 모델 호출)", ["항목", "결과", "표본"], rows, [2.2, 2.3, 0.8])
        d.figure(FIG / "fig_report.png", "실제 모델이 쓴 리포트의 일부. 값은 서버가 넣었고 인용은 검색된 단락이다", 144)
        if fs and js:
            k1, n1 = ratio(covered)
            d.p(f"규칙이 다루는 네 종류의 오류는 {n1}개 가운데 {k1}개가 잡혔다. 증감 방향은 낱말 목록으로 판정하므로 목록 "
                "밖의 표현으로 쓴 반대 방향은 놓쳤고, 첫 초안에서 문헌과의 비교를 평소와의 비교로 잘못 잡은 일이 한 번 "
                "있었다. 인용이 내용을 뒷받침하는지는 규칙으로 볼 수 없어 판정 모델에 맡겨야 한다.")

    out["rag"] = rag
    out["rag_answer"] = (f"리포트 생성부에서는 실제 언어 모델이 쓴 리포트 {n_run}편 가운데 {n_flag}편의 첫 초안이 서버 검사에 "
                         "걸렸고 다시 쓴 뒤 모두 통과하였다.")
    out["rag_abstract"] = (f"서버는 언어 모델이 쓴 풀이의 수치·인용·증감 방향을 검사하며, 실제 모델이 쓴 리포트 {n_run}편 "
                           f"가운데 {n_flag}편의 첫 초안을 걸러 다시 쓰게 하였다.")
    return out
