"""새 양식(과학영재 창의연구 R&E 연구보고서)에 본문을 넣습니다: python3 report/build/build_form.py <판 번호> [--force]

바탕은 base/form_base.hwpx(다른 팀이 채운 양식을 hwp에서 hwpx로 바꾼 것)이고, 본문은 content.py, 양식에만 있는 칸은 form_text.py에서 옵니다.
바탕 양식과 authors_local.py(학생 이름, 바탕 양식에 적혀 있던 글)는 저장소에 넣지 않으므로, 두 파일이 있는 컴퓨터에서만 만들 수 있습니다.
결과는 저장소 뿌리의 `R&E 연구보고서_새양식_<판>.hwpx`.
"""
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import content  # noqa: E402
import form_text as T  # noqa: E402
import hwpx  # noqa: E402

ROOT = HERE.parents[1]
BASE = HERE / "base" / "form_base.hwpx"
TABLE_W = 44804
PAGE_H = 71435
H1_NAMES = {"Ⅰ. 연구의 필요성 및 목적": "Ⅰ. 서론", "Ⅲ. 연구방법 및 절차": "Ⅲ. 연구 방법 및 절차",
            "Ⅳ. 연구결과 및 분석": "Ⅳ. 연구 결과"}


def para(text: str, shape: tuple[int, int]) -> str:
    p, c = shape
    run = f'<hp:run charPrIDRef="{c}"><hp:t>{escape(text)}</hp:t></hp:run>' if text else f'<hp:run charPrIDRef="{c}"/>'
    return f'<hp:p id="0" paraPrIDRef="{p}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">{run}</hp:p>'


class FormDoc(hwpx.Doc):
    """hwpx.Doc과 같은 쓰임새이고, 문단·글자 모양 번호만 새 양식의 것."""
    tail: list | None = None           # 'Ⅴ. 결론 및 고찰'의 문단을 모아 두었다가 고찰과 결론으로 나눔

    def _line(self, text, shape, chars=43.2, line=1523, after=140):
        self.blocks.append(para(text, shape))
        self.layout.append(("lines", hwpx.wrap_lines(text, chars), line + after / max(1, hwpx.wrap_lines(text, chars))))

    def h1(self, text):
        if text == "Ⅴ. 결론 및 고찰":
            self.tail = []
            return
        self._line(H1_NAMES.get(text, text), (81, 104), line=1813, after=375)

    def h2(self, text): self._line(text, (82, 95), after=260)

    def p(self, text):
        if self.tail is not None:
            self.tail.append(text)
            return
        self._line(" " + text, (80, 94))

    def item(self, text): self._line(text, (80, 94))

    def blank(self):
        if self.tail is not None:       # 결론 및 고찰의 끝: 고찰(해석할 때 고려할 점) 다음에 결론(답, 장치와 서버, 다음 단계)
            answer, device, limits, nxt = self.tail
            self.tail = None
            self.h1("Ⅴ. 고찰")
            for t in (getattr(T, "DISCUSSION", None) or [limits]):
                self.p(t)
            self.h1("Ⅵ. 결론")
            for t in (answer, device, nxt):
                self.p(t)
        self._line("", (80, 94))

    def ref(self, text): self._line(text, (84, 6), chars=50.4, line=1215, after=90)
    def refs_title(self, text="참고문헌"): self._line(text, (81, 99), line=1813, after=375)

    def table(self, caption, header, rows, widths):
        self.n_table += 1
        scale = TABLE_W / sum(widths)
        w = [round(x * scale) for x in widths]
        w[-1] += TABLE_W - sum(w)
        ncol, all_rows = len(header), [header, *rows]
        assert all(len(r) == ncol for r in all_rows), caption
        left = [any(len(str(r[c])) > 16 for r in rows) for c in range(ncol)]
        trs, height = [], 282
        for r, row in enumerate(all_rows):
            tcs = []
            for c, cell in enumerate(row):
                shape = (94, 100) if r == 0 else ((85 if left[c] else 94), 97)
                paras = "".join(para(line, shape) for line in str(cell).split("\n"))
                tcs.append(
                    f'<hp:tc name="" header="{1 if r == 0 else 0}" hasMargin="1" protect="0" editable="0" dirty="0" '
                    f'borderFillIDRef="{14 if r == 0 else 15}"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
                    f'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
                    f'hasTextRef="0" hasNumRef="0">{paras}</hp:subList><hp:cellAddr colAddr="{c}" rowAddr="{r}"/>'
                    f'<hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="{w[c]}" height="2100"/>'
                    f'<hp:cellMargin left="350" right="350" top="250" bottom="250"/></hp:tc>')
            trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
            n = max(sum(hwpx.wrap_lines(line, (w[c] - 700) / 950) for line in str(cell).split("\n"))
                    for c, cell in enumerate(row))
            height += max(2100, 500 + 1283 * n)
        tbl = (f'<hp:tbl id="{self._id()}" zOrder="{self.n_table + 20}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
               f'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" '
               f'rowCnt="{len(all_rows)}" colCnt="{ncol}" cellSpacing="0" borderFillIDRef="7" noAdjust="0">'
               f'<hp:sz width="{TABLE_W}" widthRelTo="ABSOLUTE" height="{2100 * len(all_rows)}" heightRelTo="ABSOLUTE" '
               f'protect="0"/><hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
               f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" '
               f'horzOffset="0"/><hp:outMargin left="141" right="141" top="141" bottom="141"/>'
               f'<hp:inMargin left="283" right="283" top="283" bottom="283"/>{"".join(trs)}</hp:tbl>')
        self._line(f"표 {self.n_table}. {caption}", (93, 94), line=1365, after=200)
        self.blocks.append('<hp:p id="0" paraPrIDRef="87" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                           f'<hp:run charPrIDRef="94">{tbl}<hp:t/></hp:run></hp:p>')
        self.layout.append(("box", height))
        self._line("", (86, 94), line=1365, after=200)
        return self.n_table

    def figure(self, png, caption, width_mm, image_id=None):
        image_id = f"image{len(self.images) + 1}"
        self.images.append((image_id, Path(png)))
        n = super().figure(png, caption, min(width_mm, 158), image_id=image_id)
        block = re.sub(r"<hp:caption .*?</hp:caption>", "", self.blocks.pop(), flags=re.S)
        block = (block.replace('paraPrIDRef="20"', 'paraPrIDRef="86"').replace('charPrIDRef="30"', 'charPrIDRef="94"')
                 .replace('numberingType="NONE"', 'numberingType="PICTURE"').replace('id="2147483648"', 'id="0"', 1))
        kind, h = self.layout.pop()
        self.blocks.append(block)
        self.layout.append(("box", h - 850 - 1440 - 300))
        self._line(f"그림 {n}. {caption}", (86, 94), line=1365, after=200)
        self._line("", (86, 94), line=1365, after=200)
        return n

    def pages(self, first_page_used):
        page, used = 1, first_page_used
        for kind, *v in self.layout:
            if kind == "box":
                if used + v[0] > PAGE_H:
                    page, used = page + 1, 0.0
                used += v[0]
            else:
                for _ in range(v[0]):
                    if used + v[1] > PAGE_H:
                        page, used = page + 1, 0.0
                    used += v[1]
        return page, used / PAGE_H


# ---------------------------------------------------------------- 양식의 칸 바꾸기

def top_paragraphs(s: str) -> list[tuple[int, int]]:
    depth, out, start = 0, [], 0
    for m in re.finditer(r"<hp:p [^>]*>|</hp:p>", s):
        if m.group().startswith("</"):
            depth -= 1
            if depth == 0:
                out.append((start, m.end()))
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return out


def cells(s: str) -> dict:
    """s 안의 칸을 (표가 겹친 깊이, 행, 열) → (시작, 끝)으로 돌려줌. 표 안의 표를 구별함."""
    out, stack, depth = {}, [], 0
    for m in re.finditer(r'<hp:tbl |</hp:tbl>|<hp:tc |</hp:tc>|<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"/>', s):
        g = m.group()
        if g == "<hp:tbl ":
            depth += 1
        elif g == "</hp:tbl>":
            depth -= 1
        elif g == "<hp:tc ":
            stack.append([m.start(), depth, None])
        elif g == "</hp:tc>":
            start, d, addr = stack.pop()
            out[(d, *addr)] = (start, m.end())
        else:
            stack[-1][2] = (int(m.group(2)), int(m.group(1)))
    return out


def set_cell(s: str, depth: int, row: int, col: int, lines: list[str], keep_nested: bool = False) -> str:
    a, b = cells(s)[(depth, row, col)]
    cell = s[a:b]
    i = cell.index(">", cell.index("<hp:subList ")) + 1
    j = cell.rindex("</hp:subList>")
    inner = cell[i:j]
    shape = (int(re.search(r'paraPrIDRef="(\d+)"', inner).group(1)), int(re.search(r'charPrIDRef="(\d+)"', inner).group(1)))
    rest = inner[inner.rfind("<hp:p ", 0, inner.index("<hp:tbl ")):] if keep_nested else ""
    new = "".join(para(t, shape) for t in (lines or [""])) + rest
    return s[:a] + cell[:i] + new + cell[j:] + s[b:]


def replace_once(s: str, old: str, new: str, count: int | None = None) -> str:
    old, new = escape(old), escape(new)
    n = s.count(old)
    if n == 0 or (count is not None and n != count):
        raise RuntimeError(f"바탕 파일에서 찾은 횟수가 다름({n}): {old}")
    return s.replace(old, new)


def sp(lines: list[str]) -> list[str]:
    return [" " + t for t in lines]


def fill_form(section: str, doc: FormDoc, abstract: str) -> str:
    section = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", section, flags=re.S)
    spans = top_paragraphs(section)
    tops = [section[a:b] for a, b in spans]
    head, tail = section[:spans[0][0]], section[spans[-1][1]:]
    assert "Ⅰ. 서론" in tops[10] and "[5] Edge Impulse" in tops[172], "바탕 파일의 문단 순서가 다름"

    # 표지: 학생 이름만 넣고 다른 팀의 개인 정보는 지움
    cover = tops[1]
    for k, name in enumerate(T.STUDENTS):
        cover = set_cell(cover, 1, 10 + k, 1, [name])
        for col in (4, 8, 11, 15, 20):
            cover = set_cell(cover, 1, 10 + k, col, [""])
    s1, s2, s3, s4 = T.STUDENTS
    base = T.BASE_FORM  # 바탕 양식에 적혀 있던 글
    cover = replace_once(cover, base["cover_first"], f" {s1}", 1)
    cover = replace_once(cover, base["cover_rest"], f" (인)  {s2}  (인)  {s3}  (인)  {s4}  (인)", 1)
    tops[1] = cover

    # 연구결과 요약문
    t = tops[3]
    t = set_cell(t, 1, 1, 1, sp(T.SUMMARY_NEED))
    t = set_cell(t, 1, 2, 1, sp(T.SUMMARY_RESULT))
    tops[3] = set_cell(t, 1, 3, 1, sp(T.SUMMARY_PLAN))

    # 과제 수행 과정
    t = tops[5]
    t = set_cell(t, 1, 0, 1, T.EXPERT_FEEDBACK)
    t = set_cell(t, 1, 1, 1, T.PROBLEM_SOLVING, keep_nested=True)
    used = T.AI_USED
    t = set_cell(t, 2, 0, 1, [("☑" if used is True else "□") + " 활용함"])
    t = set_cell(t, 2, 0, 3, [("☑" if used is False else "□") + " 활용하지 않음"])
    for row, value in ((1, T.AI_TOOL), (2, T.AI_PURPOSE), (3, T.AI_DETAIL)):
        t = set_cell(t, 2, row, 2, [value])
    tops[5] = t
    t = tops[6]
    for k, role in enumerate(T.ROLES):
        t = set_cell(t, 1, k, 2, sp([role]) if role else [""])
    tops[6] = set_cell(t, 1, 4, 1, sp(T.LEARNING))

    # 보고서 제목 칸의 초록
    t, n = re.subn(r"<hp:t> 본 연구는 손 제스처[^<]*</hp:t>", lambda m: f"<hp:t> {escape(abstract)}</hp:t>", tops[9])
    assert n == 1, "초록 문단을 찾지 못함"
    tops[9] = t

    # 연구윤리 체크리스트의 서명
    tops[185] = replace_once(tops[185], base["ethics_signs"],
                             f" {s1} (인)  {s2} (인)  {s3} (인)  {s4} (인)", 1)

    out = head + "".join(tops[:10]) + "".join(doc.blocks) + "".join(tops[173:]) + tail
    # 여러 곳에 같은 글로 들어 있는 것: 제목, 지은이, 주제어
    out = replace_once(out, base["title_ko_1"], T.TITLE_KO[0] + " ", 3)
    out = replace_once(out, base["title_ko_2"], T.TITLE_KO[1], 3)
    out = replace_once(out, base["title_en_1"], T.TITLE_EN[0], 2)
    out = replace_once(out, base["title_en_2"], T.TITLE_EN[1], 2)
    out = replace_once(out, base["authors_ko"], T.AUTHORS_KO, 1)
    out = replace_once(out, base["authors_en"], T.AUTHORS_EN, 1)
    out = replace_once(out, base["keywords"], T.KEYWORDS, 2)
    ElementTree.fromstring(out.encode("utf-8"))
    return out


def first_page_used(abstract: str) -> float:
    lines = hwpx.wrap_lines(abstract, 42.2) + 2
    return 2190 + 1305 + (2155 * 2 + 4412) + (1635 * 2 + 4412) + 1800 + 1468 * lines + 282


def main(version: str) -> None:
    import results
    if not BASE.exists() or T.BASE_FORM is None:
        raise SystemExit("base/form_base.hwpx and authors_local.py are kept out of the repository; both are needed to build")
    out = ROOT / f"R&E 연구보고서_새양식_{version}.hwpx"
    if out.exists() and "--force" not in sys.argv:
        raise SystemExit(f"{out.name} already exists (add --force to overwrite)")
    doc = FormDoc()
    with tempfile.TemporaryDirectory() as tmp:
        meta = content.write(doc, results.load(), Path(tmp))
    src = zipfile.ZipFile(BASE)
    section = fill_form(src.read("Contents/section0.xml").decode("utf-8"), doc, meta["abstract"])
    hpf = src.read("Contents/content.hpf").decode("utf-8")
    hpf = re.sub(r'<opf:item id="image\d+"[^>]*/>', "", hpf)
    items = "".join(f'<opf:item id="{i}" href="BinData/{i}.png" media-type="image/png" isEmbeded="1"/>' for i, _ in doc.images)
    assert '<opf:item id="settings"' in hpf
    hpf = hpf.replace('<opf:item id="settings"', items + '<opf:item id="settings"')
    with zipfile.ZipFile(out, "w") as z:
        done = False
        for info in src.infolist():
            name = info.filename
            if name.startswith("BinData/"):
                if not done:
                    for image_id, png in doc.images:
                        z.writestr(f"BinData/{image_id}.png", png.read_bytes(), compress_type=zipfile.ZIP_STORED)
                    done = True
                continue
            data = src.read(name)
            if name == "Contents/section0.xml":
                data = section.encode("utf-8")
            elif name == "Contents/content.hpf":
                data = hpf.encode("utf-8")
            z.writestr(name, data, compress_type=zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED)
    pages, last = doc.pages(first_page_used(meta["abstract"]))
    print(out.name, f"표 {doc.n_table}개, 그림 {doc.n_figure}개, 3번 연구보고서 어림 {pages}쪽(마지막 쪽 {last:.0%} 사용)")


if __name__ == "__main__":
    main(sys.argv[1])
