"""R&E 보고서 hwpx를 만듭니다: uv run python report/build/build.py <판 번호>

바탕 파일(_수정3.hwpx)의 머리를 쓰고 본문은 content.py로 다시 만듭니다. 결과는 저장소 뿌리의 `..._수정<판>.hwpx`.
"""
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import content  # noqa: E402
import hwpx  # noqa: E402

ROOT = HERE.parents[1]
NAME = "2026 R&E 프로그램 보고서_정보#B(팀코드)_수정{}.hwpx"
BASE = ROOT / NAME.format(3)


def main(version: str, results: dict) -> None:
    out = ROOT / NAME.format(version)
    if out.exists() and "--force" not in sys.argv:
        raise SystemExit(f"{out.name} already exists (add --force to overwrite)")
    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(BASE).extract("BinData/image2.png", tmp)
        d = hwpx.Doc()
        meta = content.write(d, results, Path(tmp) / "BinData")
        info = hwpx.build(BASE, out, d, abstract=meta["abstract"], keywords=meta["keywords"], title=meta.get("title"))
    pages, last = d.pages(hwpx.first_page_used(meta["abstract"]))
    print(out.name, info, f"본문 어림: {pages}쪽, 마지막 쪽 {last:.0%} 사용")


if __name__ == "__main__":
    import results as results_module
    main(sys.argv[1], results_module.load())
