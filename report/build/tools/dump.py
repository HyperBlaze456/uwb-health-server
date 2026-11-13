"""hwpx 한 개의 글을 읽기용 텍스트로 뽑습니다: dump.py <hwpx> <out.txt>"""
import html, re, sys, zipfile
x = zipfile.ZipFile(sys.argv[1]).read("Contents/section0.xml").decode()
i = x.find("UWB·IMU 융합 기반")
x = x[i:]
# 문단 끝을 줄바꿈으로 바꾸고 글만 남김
x = re.sub(r"</hp:p>", "\n", x)
parts = []
for line in x.split("\n"):
    t = "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", line))
    if t.strip():
        parts.append(html.unescape(t))
s = "\n".join(parts).replace("들여쓰기없음.\n", "").replace("본\n 연구는", "본 연구는")
open(sys.argv[2], "w").write(s)
print(sys.argv[2], len(s))
