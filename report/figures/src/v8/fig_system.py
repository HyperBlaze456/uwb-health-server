"""그림: 시스템 구성도. 측정 장치 → 수집 서버 ⇄ 분석 서비스 → 리포트 생성부."""
from figlib import ACCENT, GROUP_FILL, arrow, group, label, node, render

W, H = 1740, 560
Y1, Y2 = 165, 450
HN = 75                      # 노드 높이의 절반
TAG, HEAD = 108, 392         # 측정 장치의 열
RX, AR, AG = 668, 854, 1040  # 수집 서버의 열: 수신, 원본 보관, 지표 집계
LM, CK = 1348, 1566          # 리포트 생성부의 열
b = []
b.append(group(20, 20, 460, 520, "측정 장치"))
b.append(group(580, 20, 548, 262, "수집 서버"))
b.append(group(626, 342, 316, 198, "분석 서비스"))
b.append(group(1260, 20, 460, 520, "리포트 생성부"))

# 측정 장치
b.append(node(TAG, Y1, "발목 태그", "walk"))
b.append(node(TAG, Y2, "앵커 A1–A3", "antenna"))
b.append(node(HEAD, Y1, "헤드(A0)", "router"))
b.append(arrow([(TAG, Y1 + HN), (TAG, Y2 - HN)], both=True))
b.append(label(TAG, 316, "UWB 거리", bg=GROUP_FILL))
b.append(arrow([(TAG + 73, Y1), (HEAD - 73, Y1)]))
b.append(label((TAG + HEAD) / 2, Y1 - 14, "거리 · IMU", bg=GROUP_FILL))

# 헤드 ⇄ 수신
b.append(arrow([(HEAD + 73, Y1 - 24), (RX - 73, Y1 - 24)]))
b.append(label(530, Y1 - 38, "1초 묶음", bg=None))
b.append(arrow([(RX - 73, Y1 + 24), (HEAD + 73, Y1 + 24)]))
b.append(label(530, Y1 + 64, "수신 확인", bg=None))

# 수집 서버
b.append(node(RX, Y1, "수신", "cloud-upload"))
b.append(node(AR, Y1, "원본 보관", "archive"))
b.append(node(AG, Y1, "지표 집계", "chart-line"))
b.append(arrow([(RX + 73, Y1), (AR - 73, Y1)]))

# 분석 서비스
b.append(node(AR, 462, "융합 분석", "route", accent=True, h=136))
b.append(arrow([(AR, Y1 + HN), (AR, 394)]))
b.append(label(AR - 16, 320, "측정 구간", anchor="end", bg=None))
b.append(arrow([(AR + 73, 462), (AG, 462), (AG, Y1 + HN)], color=ACCENT))
b.append(label(AG + 16, 400, "걷기 구간 · 활보", anchor="start", bg=None))

# 리포트 생성부
b.append(node(LM, Y1, "언어 모델", "sparkles"))
b.append(node(LM, Y2, "논문 저장소", "books"))
b.append(node(CK, Y1, "서버 검사", "shield-check", accent=True))
b.append(node(CK, Y2, "리포트", "file-analytics"))
b.append(arrow([(AG + 73, Y1), (LM - 73, Y1)]))
b.append(label(1194, Y1 - 14, "지표 값", bg=None))
b.append(arrow([(LM, Y1 + HN), (LM, Y2 - HN)], both=True))
b.append(label(LM, 316, "검색", bg=GROUP_FILL))
b.append(arrow([(LM + 73, Y1), (CK - 73, Y1)]))
b.append(label((LM + CK) / 2, Y1 - 14, "글", bg=GROUP_FILL))
b.append(arrow([(CK, Y1 + HN), (CK, Y2 - HN)], color=ACCENT))
b.append(label(CK, 316, "통과한 글", bg=GROUP_FILL))
print(render("fig_system", W, H, "".join(b)))
