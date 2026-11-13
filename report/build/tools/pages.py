"""쪽마다 남는 높이와, 쪽을 넘긴 표·그림을 보여 줍니다."""
import sys, tempfile
from pathlib import Path
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
import hwpx, results, content
d = hwpx.Doc()
with tempfile.TemporaryDirectory() as tmp:
    meta = content.write(d, results.load(), Path(tmp))
used = hwpx.first_page_used(meta["abstract"]); page = 1
for i, (kind, *v) in enumerate(d.layout):
    if kind == "box":
        if used + v[0] > hwpx.PAGE_H:
            print(f"쪽 {page}: 표·그림(높이 {v[0]/1600:.1f}줄)이 넘어가며 {(hwpx.PAGE_H-used)/1600:.1f}줄 비움 (블록 {i})")
            page, used = page + 1, 0.0
        used += v[0]
    else:
        n, lh = v
        for _ in range(n):
            if used + lh > hwpx.PAGE_H:
                print(f"쪽 {page}: 글에서 넘어감, {(hwpx.PAGE_H-used)/1600:.1f}줄 비움")
                page, used = page + 1, 0.0
            used += lh
print(f"마지막 쪽 {page}: {used/1600:.1f}줄 사용, {(hwpx.PAGE_H-used)/1600:.1f}줄 남음")
