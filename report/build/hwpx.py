"""R&E 보고서 hwpx 조립 도구.

바탕 파일(한글이 저장한 hwpx)의 머리(안내 쪽, 제목, 지은이, 요약 틀)는 그대로 두고, 본문을 블록 목록으로 다시 만듭니다.
문단·표·그림의 XML은 바탕 파일에 있던 꼴을 그대로 본뜹니다(문단 모양·글자 모양 번호는 바탕 파일의 header.xml 기준).
줄 배치 기록(linesegarray)은 넣지 않으므로 한글이 열 때 다시 배치합니다.
"""
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

TEXT_WIDTH = 49607          # 본문 너비(HWPUNIT). 쪽 59528 − 여백 4252×2 − 제본 1417
MM = 283.465                # 1 mm = 283.465 HWPUNIT
PX = 75                     # 96 dpi 그림 1 px = 75 HWPUNIT

# (문단 모양, 글자 모양)
BODY, H1, H2, PLAIN, REF, FIGP = (31, 30), (15, 19), (36, 26), (0, 30), (34, 30), (20, 30)
CAPTION_CHAR, CELL = 25, (37, 34)


PAGE_H = 67180              # 쪽에서 글이 놓이는 높이(한글이 저장한 줄 배치에서 구한 값)
LINE_H = 1600               # 본문 한 줄(10pt, 줄 간격 160%)
BODY_CHARS = 49.6           # 본문 한 줄에 들어가는 한글 글자 수


def char_width(ch: str) -> float:
    """한글 한 글자를 1로 본 너비(한글이 저장한 줄 배치에 맞춘 어림값)."""
    if ch == " ":
        return 0.35
    if ch.isdigit():
        return 0.55
    if ch.isascii() and ch.isalpha():
        return 0.7 if ch.isupper() else 0.45
    if ch.isascii() or ch in "·–—−‘’“”…°±×":
        return 0.5
    return 1.0


def wrap_lines(text: str, width: float, indent: float = 0.0) -> int:
    """어절 단위로 줄을 바꿀 때의 줄 수. indent는 첫 줄의 들여쓰기(글자 수)."""
    if not text:
        return 1
    lines, used = 1, indent
    for word in text.split(" "):
        w = sum(char_width(c) for c in word)
        if used > indent and used + 0.35 + w > width:
            lines, used = lines + 1, 0.0
            while w > width:                       # 한 줄보다 긴 어절
                lines, w = lines + 1, w - width
            used = w
        else:
            used += (0.35 if used > indent else 0.0) + w
            while used > width:
                lines, used = lines + 1, used - width
    return lines


def first_page_used(abstract: str) -> float:
    """본문이 시작하는 쪽에서 제목·지은이·요약·키워드가 쓰는 높이(한글이 저장한 줄 배치에서 구한 값).
    요약은 9pt(한 줄 1440)로 16200에서 시작하고, 그 뒤에 빈 줄과 키워드 줄, 단 정의 문단(2400)이 옴."""
    return 16200 + (wrap_lines(abstract, 55.1) + 2) * 1440 + 2400


def _p(text: str, shape: tuple[int, int], pid: str = "2147483648") -> str:
    para, char = shape
    body = f"<hp:t>{escape(text)}</hp:t>" if text else ""
    run = f'<hp:run charPrIDRef="{char}">{body}</hp:run>' if text else f'<hp:run charPrIDRef="{char}"/>'
    return (f'<hp:p id="{pid}" paraPrIDRef="{para}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f"{run}</hp:p>")


@dataclass
class Doc:
    blocks: list[str] = field(default_factory=list)
    images: list[tuple[str, Path]] = field(default_factory=list)   # (id, 파일)
    n_table: int = 0
    n_figure: int = 0
    _next_id: int = 1700000000
    layout: list[tuple] = field(default_factory=list)              # 쪽수 어림용: ("lines", 줄 수, 줄 높이) | ("box", 높이)

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    # ---- 글
    def _text(self, text, shape, pid="2147483648", indent=0.0, line=LINE_H):
        self.blocks.append(_p(text, shape, pid))
        self.layout.append(("lines", wrap_lines(text, BODY_CHARS, indent), line))

    def h1(self, text): self._text(text, H1, "0", line=2400)
    def h2(self, text): self._text(text, H2, line=2200)
    def p(self, text): self._text(text, BODY, indent=1.0)
    def item(self, text): self._text(text, PLAIN)
    def blank(self): self._text("", PLAIN, "0")
    def ref(self, text): self._text(text, REF)
    def refs_title(self, text="참고문헌"): self._text(text, (13, 19), "0", line=2400)

    # ---- 표
    def table(self, caption: str, header: list[str], rows: list[list[str]], widths: list[float]) -> int:
        """widths: 열 너비의 비(합을 48745에 맞춤). 표 번호를 돌려줌."""
        self.n_table += 1
        total = 48745
        scale = total / sum(widths)
        w = [round(x * scale) for x in widths]
        w[-1] += total - sum(w)
        ncol, all_rows = len(header), [header, *rows]
        assert all(len(r) == ncol for r in all_rows), caption

        def fill(r: int, c: int) -> int:
            col = 0 if c == 0 else (2 if c == ncol - 1 else 1)
            if r == 0:
                return (18, 19, 20)[col]
            if r == len(all_rows) - 1:
                return (24, 6, 25)[col]
            return (21, 22, 23)[col]

        trs = []
        for r, row in enumerate(all_rows):
            tcs = []
            for c, cell in enumerate(row):
                paras = "".join(_p(line, CELL).replace('styleIDRef="0"', 'styleIDRef="19"') for line in str(cell).split("\n"))
                tcs.append(
                    f'<hp:tc name="" header="{1 if r == 0 else 0}" hasMargin="0" protect="0" editable="0" dirty="0" '
                    f'borderFillIDRef="{fill(r, c)}"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
                    f'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
                    f'hasTextRef="0" hasNumRef="0">{paras}</hp:subList><hp:cellAddr colAddr="{c}" rowAddr="{r}"/>'
                    f'<hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="{w[c]}" height="280"/>'
                    f'<hp:cellMargin left="510" right="510" top="141" bottom="141"/></hp:tc>')
            trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
        cap = (f'<hp:caption side="TOP" fullSz="0" width="8504" gap="850" lastWidth="{total}"><hp:subList id="" '
               f'textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" '
               f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0"><hp:p id="0" paraPrIDRef="0" styleIDRef="0" '
               f'pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="{CAPTION_CHAR}"><hp:t>표 </hp:t><hp:ctrl>'
               f'<hp:autoNum num="{self.n_table}" numType="TABLE"><hp:autoNumFormat type="DIGIT" userChar="" '
               f'prefixChar="" suffixChar="" supscript="0"/></hp:autoNum></hp:ctrl><hp:t>. {escape(caption)}</hp:t>'
               f"</hp:run></hp:p></hp:subList></hp:caption>")
        tbl = (f'<hp:tbl id="{self._id()}" zOrder="{self.n_table + 20}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
               f'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="TABLE" repeatHeader="1" '
               f'rowCnt="{len(all_rows)}" colCnt="{ncol}" cellSpacing="0" borderFillIDRef="17" noAdjust="0">'
               f'<hp:sz width="{total}" widthRelTo="ABSOLUTE" height="{2044 * len(all_rows)}" heightRelTo="ABSOLUTE" '
               f'protect="0"/><hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
               f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" '
               f'horzOffset="0"/><hp:outMargin left="141" right="141" top="141" bottom="141"/>{cap}'
               f'<hp:inMargin left="510" right="510" top="140" bottom="140"/>{"".join(trs)}</hp:tbl>')
        self.blocks.append(f'<hp:p id="2147483648" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" '
                           f'merged="0"><hp:run charPrIDRef="30">{tbl}<hp:t/></hp:run></hp:p>')
        height = 2032
        for row in all_rows:
            n = max(sum(wrap_lines(line, (w[c] - 1020) / 900) for line in str(cell).split("\n"))
                    for c, cell in enumerate(row))
            height += 1180 + 1440 * (n - 1)
        self.layout.append(("box", height))
        return self.n_table

    # ---- 그림
    def figure(self, png: Path, caption: str, width_mm: float, image_id: str | None = None) -> int:
        """png를 글자처럼 취급하는 그림으로 넣고 아래에 '[그림 n] 제목'을 붙임. 그림 번호를 돌려줌.
        image_id를 주면 바탕 파일에 이미 있는 그림(BinData)을 다시 씀."""
        self.n_figure += 1
        png = Path(png)
        pw, ph = struct.unpack(">II", png.read_bytes()[16:24])
        if image_id is None:
            image_id = f"image{len(self.images) + 3}"
            self.images.append((image_id, png))
        w = round(width_mm * MM)
        h = round(w * ph / pw)
        dw, dh = pw * PX, ph * PX
        pid = self._id()
        pic = (f'<hp:pic id="{pid}" zOrder="{self.n_figure + 60}" numberingType="NONE" textWrap="TOP_AND_BOTTOM" '
               f'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="0" instid="{pid - 1000000000}" '
               f'reverse="0"><hp:offset x="0" y="0"/><hp:orgSz width="{w}" height="{h}"/><hp:curSz width="0" height="0"/>'
               f'<hp:flip horizontal="0" vertical="0"/><hp:rotationInfo angle="0" centerX="{w // 2}" centerY="{h // 2}" '
               f'rotateimage="1"/><hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
               f'<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/><hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" '
               f'e6="0"/></hp:renderingInfo><hc:img binaryItemIDRef="{image_id}" bright="0" contrast="0" effect="REAL_PIC" '
               f'alpha="0"/><hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{w}" y="0"/><hc:pt2 x="{w}" y="{h}"/>'
               f'<hc:pt3 x="0" y="{h}"/></hp:imgRect><hp:imgClip left="0" right="{dw}" top="0" bottom="{dh}"/>'
               f'<hp:inMargin left="0" right="0" top="0" bottom="0"/><hp:imgDim dimwidth="{dw}" dimheight="{dh}"/>'
               f'<hp:effects/><hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" protect="0"/>'
               f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="1" holdAnchorAndSO="0" '
               f'vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
               f'<hp:outMargin left="0" right="0" top="0" bottom="0"/><hp:shapeComment>그림입니다.\n'
               f'원본 그림의 이름: {escape(png.name)}\n원본 그림의 크기: 가로 {pw}pixel, 세로 {ph}pixel</hp:shapeComment>'
               f'<hp:caption side="BOTTOM" fullSz="0" width="8504" gap="850" lastWidth="{w}"><hp:subList id="" '
               f'textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" '
               f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0"><hp:p id="0" paraPrIDRef="20" styleIDRef="0" '
               f'pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="{CAPTION_CHAR}"><hp:t>'
               f'[그림 {self.n_figure}] {escape(caption)}</hp:t></hp:run></hp:p></hp:subList></hp:caption></hp:pic>')
        self.blocks.append(f'<hp:p id="2147483648" paraPrIDRef="20" styleIDRef="0" pageBreak="0" columnBreak="0" '
                           f'merged="0"><hp:run charPrIDRef="30">{pic}<hp:t/></hp:run></hp:p>')
        self.layout.append(("box", h + 850 + 1440 + 300))
        return self.n_figure

    def pages(self, first_page_used: float) -> tuple[int, float]:
        """본문의 쪽수 어림. first_page_used: 본문이 시작하는 쪽에서 이미 쓴 높이(제목·요약).
        돌려주는 값: (쪽 수, 마지막 쪽에서 쓴 높이의 비율). 표와 그림은 쪽을 넘지 않고 통째로 다음 쪽으로 감."""
        page, used = 1, first_page_used
        for kind, *v in self.layout:
            if kind == "box":
                if used + v[0] > PAGE_H:
                    page, used = page + 1, 0.0
                used += v[0]
            else:
                n, lh = v
                for _ in range(n):
                    if used + lh > PAGE_H:
                        page, used = page + 1, 0.0
                    used += lh
        return page, used / PAGE_H


# ---------------------------------------------------------------- 조립

_NS = ('xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
       'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"')


def build(base: Path, out: Path, doc: Doc, *, abstract: str, keywords: str, title: str | None = None) -> dict:
    """base(hwpx)의 머리에 doc의 본문을 붙여 out으로 씁니다. 요약 문단과 키워드 줄, (주면) 제목을 바꿉니다."""
    src = zipfile.ZipFile(base)
    section = src.read("Contents/section0.xml").decode("utf-8")
    section = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", section, flags=re.S)

    # 머리: 처음부터 요약 뒤의 '단 정의' 문단까지
    kw = section.index("키워드 :")
    col = section.index("<hp:colPr ", kw)
    head_end = section.index("</hp:p>", col) + len("</hp:p>")
    head = section[:head_end]

    # 요약 문단: 첫 글자 '본'은 양식의 메모(들여쓰기없음) 안에 있고, 그 뒤의 글이 메모 끝(fieldEnd) 다음에 옴
    if not abstract.startswith("본"):
        raise RuntimeError("요약은 '본'으로 시작해야 함(양식의 메모가 첫 글자를 감쌈)")
    memo = head.index("들여쓰기없음.")
    m = re.compile(r"(<hp:fieldEnd [^>]*/></hp:ctrl><hp:t>)(.*?)(</hp:t></hp:run></hp:p>)", re.S).search(head, memo)
    if m is None:
        raise RuntimeError("요약 문단을 찾지 못함")
    head = head[:m.start(2)] + escape(abstract[1:]) + head[m.end(2):]
    head, n = re.subn(r"<hp:t>키워드 :[^<]*</hp:t>", f"<hp:t>키워드 : {escape(keywords)}</hp:t>", head)
    if n != 1:
        raise RuntimeError("키워드 줄을 찾지 못함")
    if title is not None:
        old = re.search(r'(<hp:run charPrIDRef="27"><hp:t>)([^<]*)(</hp:t>)', head)
        if old is None:
            raise RuntimeError("제목 문단을 찾지 못함")
        head = head[:old.start(2)] + escape(title) + head[old.end(2):]

    new_section = head + "".join(doc.blocks) + "</hs:sec>"
    ElementTree.fromstring(new_section.encode("utf-8"))            # 형식이 맞는 XML인지 확인

    hpf = src.read("Contents/content.hpf").decode("utf-8")
    items = "".join(f'<opf:item id="{i}" href="BinData/{i}.png" media-type="image/png" isEmbeded="1"/>'
                    for i, _ in doc.images)
    anchor = '<opf:item id="section0"'
    if anchor not in hpf:
        raise RuntimeError("content.hpf의 꼴이 다름")
    hpf = hpf.replace(anchor, items + anchor)

    with zipfile.ZipFile(out, "w") as z:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "Contents/section0.xml":
                data = new_section.encode("utf-8")
            elif info.filename == "Contents/content.hpf":
                data = hpf.encode("utf-8")
            z.writestr(_like(info), data, compress_type=info.compress_type)
            if info.filename == "BinData/image2.png":
                for image_id, png in doc.images:
                    z.writestr(_like(info, f"BinData/{image_id}.png"), png.read_bytes(), compress_type=zipfile.ZIP_STORED)
    return {"tables": doc.n_table, "figures": doc.n_figure, "images_added": len(doc.images),
            "section_bytes": len(new_section.encode("utf-8"))}


def _like(info: zipfile.ZipInfo, name: str | None = None) -> zipfile.ZipInfo:
    z = zipfile.ZipInfo(name or info.filename, date_time=info.date_time)
    z.create_system, z.external_attr, z.compress_type = info.create_system, info.external_attr, info.compress_type
    return z
