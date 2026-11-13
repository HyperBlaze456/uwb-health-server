"""R&E 보고서 본문(4판부터). 결과가 준비된 만큼 채우고, 준비되지 않은 곳은 빈 자리로 둡니다.

build.py가 `write(d, R)`를 부릅니다. R은 준비된 결과의 묶음(없으면 None)이고, 인용 번호는 글에 처음 나온 순서로 매깁니다.
빈 자리 표기: 실측은 "(측정)", 평가는 "(평가)". 요약·결론의 빈 수치는 "○○".
"""
from pathlib import Path

FIG = Path(__file__).resolve().parents[1] / "figures" / "out"

REFS = {
    "deldin2019": "S. Del Din et al., “Gait Analysis with Wearables Predicts Conversion to Parkinson Disease,” Annals of Neurology, vol. 86, no. 3, pp. 357–367, 2019. DOI: 10.1002/ana.25548",
    "verghese2007": "J. Verghese, C. Wang, R. B. Lipton, R. Holtzer, and X. Xue, “Quantitative Gait Dysfunction and Risk of Cognitive Decline and Dementia,” J. Neurol. Neurosurg. Psychiatry, vol. 78, no. 9, pp. 929–935, 2007. DOI: 10.1136/jnnp.2006.106914",
    "dodge2012": "H. H. Dodge, N. C. Mattek, D. Austin, T. L. Hayes, and J. A. Kaye, “In-Home Walking Speeds and Variability Trajectories Associated with Mild Cognitive Impairment,” Neurology, vol. 78, no. 24, pp. 1946–1952, 2012. DOI: 10.1212/WNL.0b013e318259e1de",
    "micoamigo2023": "M. E. Micó-Amigo et al., “Assessing Real-World Gait with Digital Technology? Validation, Insights and Recommendations from the Mobilise-D Consortium,” J. NeuroEng. Rehabil., vol. 20, no. 1, 78, 2023. DOI: 10.1186/s12984-023-01198-5",
    "otim2019": "T. Otim, A. Bahillo, L. E. Díez, P. López-Iturri, and F. Falcone, “FDTD and Empirical Exploration of Human Body and UWB Radiation Interaction on TOF Ranging,” IEEE Antennas and Wireless Propagation Letters, vol. 18, no. 6, pp. 1119–1123, 2019. DOI: 10.1109/LAWP.2019.2910378",
    "dw3000": "Decawave/Qorvo, “DW3000 Data Sheet,” Version 1.3, 2020.",
    "makerfabs": "Makerfabs, “MaUWB_ESP32S3 with STM32 AT Command,” Version 1.1, 2024. https://github.com/Makerfabs/MaUWB_ESP32S3-with-STM32-AT-Command",
    "foxlin2005": "E. Foxlin, “Pedestrian Tracking with Shoe-Mounted Inertial Sensors,” IEEE Computer Graphics and Applications, vol. 25, no. 6, pp. 38–46, 2005. DOI: 10.1109/MCG.2005.140",
    "skog2010": "I. Skog, P. Händel, J.-O. Nilsson, and J. Rantakokko, “Zero-Velocity Detection—An Algorithm Evaluation,” IEEE Transactions on Biomedical Engineering, vol. 57, no. 11, pp. 2657–2666, 2010. DOI: 10.1109/TBME.2010.2060723",
    "huang2024": "L. Huang et al., “A Survey on Hallucination in Large Language Models: Principles, Taxonomy, Challenges, and Open Questions,” ACM Transactions on Information Systems, 2024. DOI: 10.1145/3703155",
    "singh2025": "A. Singh, A. Ehtesham, S. Kumar, and T. T. Khoei, “Agentic Retrieval-Augmented Generation: A Survey on Agentic RAG,” arXiv:2501.09136, 2025.",
    "anderson2019": "B. Anderson, M. Shi, V. Y. F. Tan, and Y. Wang, “Mobile Gait Analysis Using Foot-Mounted UWB Sensors,” Proc. ACM Interact. Mob. Wearable Ubiquitous Technol., vol. 3, no. 3, Article 73, 2019. DOI: 10.1145/3351231",
    "naheem2022": "K. Naheem and M. S. Kim, “A Low-Cost Foot-Placed UWB and IMU Fusion-Based Indoor Pedestrian Tracking System for IoT Applications,” Sensors, vol. 22, no. 21, 8160, 2022. DOI: 10.3390/s22218160",
    "decock2023": "C. De Cock, E. Tanghe, W. Joseph, and D. Plets, “Robust IMU-Based Mitigation of Human Body Shadowing in UWB Indoor Positioning,” Sensors, vol. 23, no. 19, 8289, 2023. DOI: 10.3390/s23198289",
    "kluge2021": "F. Kluge et al., “Consensus Based Framework for Digital Mobility Monitoring,” PLoS ONE, vol. 16, no. 8, e0256541, 2021. DOI: 10.1371/journal.pone.0256541",
    "mpu6050": "InvenSense, “MPU-6000 and MPU-6050 Product Specification,” Revision 3.4, 2013.",
    "vancriekinge2023": "T. Van Criekinge, W. Saeys, S. Truijen, L. Vereeck, L. H. Sloot, and A. Hallemans, “A Full-Body Motion Capture Gait Dataset of 138 Able-Bodied Adults Across the Life Span and 50 Stroke Survivors,” Scientific Data, vol. 10, 852, 2023. DOI: 10.1038/s41597-023-02767-y",
    "bachlin2010": "M. Bächlin et al., “Wearable Assistant for Parkinson’s Disease Patients with the Freezing of Gait Symptom,” IEEE Trans. Inf. Technol. Biomed., vol. 14, no. 2, pp. 436–446, 2010. DOI: 10.1109/TITB.2009.2036165",
    "shida2023": "T. K. F. Shida et al., “A Public Data Set of Walking Full-Body Kinematics and Kinetics in Individuals with Parkinson’s Disease,” Frontiers in Neuroscience, vol. 17, 992585, 2023. DOI: 10.3389/fnins.2023.992585",
}


class Cite:
    def __init__(self):
        self.order: list[str] = []

    def __call__(self, *keys: str) -> str:
        out = ""
        for key in keys:
            if key not in REFS:
                raise KeyError(key)
            if key not in self.order:
                self.order.append(key)
            out += f"[{self.order.index(key) + 1}]"
        return out


BLANK_M = "(측정)"
BLANK_E = "(평가)"


def write(d, R, base_images: Path) -> dict:
    """본문을 d에 쓰고 요약·키워드를 돌려줍니다. R: results.load()의 결과(준비되지 않은 조각은 None)."""
    c = Cite()
    rag, mocap = R.get("rag"), R.get("mocap")

    # ------------------------------------------------------------------ Ⅰ
    d.h1("Ⅰ. 연구의 필요성 및 목적")
    d.p("보행의 변화는 뇌 퇴행성 질환의 진단보다 먼저 나타난다. 지역사회에 사는 696명을 추적한 연구에서 뒤에 파킨슨병으로 "
        f"진단된 16명은 진단 약 4년 전부터 보행 지표가 달랐다{c('deldin2019')}. 이런 변화는 여러 달에 걸쳐 조금씩 "
        f"나타나므로, 검사실에서 한 번 재는 검사보다 집에서 평소의 걸음을 계속 재는 방법이 필요하다{c('dodge2012')}.")
    d.p("일상에서 발의 움직임을 재는 수단은 저마다 약점이 있다. 광학 동작 분석 장비는 검사실에서만 쓴다. 몸에 차는 IMU는 "
        f"활보(stride) 하나의 모양을 잘 재지만{c('micoamigo2023')}, 공간 안에서의 위치와 진행 방향을 알 수 없고 오차가 "
        "시간에 따라 쌓인다. UWB는 고정된 앵커까지의 거리로 절대 위치를 주지만, 발목의 태그는 걷는 동안 몸에 가려져 "
        f"거리가 실제보다 길게 측정된다{c('otim2019')}. 두 센서의 약점은 서로 반대이다. IMU는 짧은 시간에는 정확하고 "
        "길게는 틀어지며, UWB는 길게는 정확하고 순간순간 틀어진다. 그래서 본 연구는 발목 한 곳에서 두 센서를 합치는 "
        "방법을 주제로 정하였다.")
    d.p("연구 문제는 다음 하나이다. 몸에 가려진 거리가 섞여도, 발목의 UWB 거리와 IMU를 합쳐 각 센서를 단독으로 쓸 때보다 "
        "정확한 발 궤적과 활보를 구할 수 있는가. 이를 세 단계로 확인하였다.")
    d.item("① 장치. UWB와 IMU 보드로 발목까지의 거리를 재는 장치를 만들고 주기와 정밀도를 잰다.")
    d.item("② 융합. 가려져 길어진 거리를 걸러 내는 융합 방법을 만들고 IMU 단독, UWB 단독, 일반적인 융합과 정확도를 견준다.")
    d.item("③ 활용. 측정 원본을 보존하는 서버와, 지표를 문헌 근거와 함께 풀이하고 그 글을 검사하는 리포트 생성부를 만든다.")

    # ------------------------------------------------------------------ Ⅱ
    d.h1("Ⅱ. 이론적 배경")
    d.h2("1. UWB 거리 측정과 몸 가림")
    d.p("UWB(Ultra-Wideband)는 매우 짧은 펄스의 도착 시간으로 거리를 구한다. 본 연구에서 쓴 DW3000 칩셋은 시야가 확보되면 "
        f"오차가 10 cm 안팎이다{c('dw3000')}. 신호가 몸에 가려지면(NLOS) 돌아서 오거나 늦게 도착하여 거리가 길어진다. "
        f"인체에 가려질 때의 오차는 최악의 경우 약 30~60 cm로 보고되었다{c('otim2019')}. 가려진 거리는 길어지는 쪽으로 "
        "틀어진다는 이 성질을 융합에 쓴다.")
    d.h2("2. 관성 측정과 영속도 보정")
    d.p("IMU의 가속도를 두 번 적분하면 위치가 나오지만 오차도 함께 적분된다. 신발에 단 센서에서는 발이 땅에 닿아 멈춘 "
        "동안(입각기)의 속도가 0이라는 사실로 걸음마다 속도 오차를 바로잡는다. 이를 영속도 보정(ZUPT)이라 "
        f"한다{c('foxlin2005')}. 입각기는 가속도와 각속도의 통계량으로 검출한다{c('skog2010')}. 발목은 신발과 조건이 "
        "다르다. 발목의 센서는 정강이에 붙어 있어 입각기에도 정강이가 발목 관절을 축으로 돌면서 조금 움직인다. 이 "
        "차이의 영향은 Ⅳ.3에서 쟀다.")
    d.h2("3. 선행 연구")
    t1 = d.n_table + 1
    d.p(f"기존 접근과 본 연구를 표 {t1}에 견주었다. 신발 사이의 UWB 거리만 쓴 연구는 가림의 보정이 없었고"
        f"{c('anderson2019')}, UWB와 IMU를 합친 연구는 위치 추적이 목표여서 보행 지표를 내지 않았다"
        f"{c('naheem2022', 'decock2023')}. 본 연구는 발목 한 곳의 태그로 절대 위치가 있는 발 궤적과 보행 지표를 함께 구한다.")
    d.table("보행 측정 접근의 비교",
            ["접근", "센서 위치", "절대 위치", "몸 가림 대응", "보행 지표"],
            [["광학 동작 분석", "검사실(마커)", "있음", "해당 없음", "산출"],
             [f"IMU 단독{c('micoamigo2023')}", "허리", "없음", "해당 없음", "산출"],
             [f"UWB 단독{c('anderson2019')}", "양쪽 신발", "없음(발 사이 거리)", "스무딩", "산출"],
             [f"UWB·IMU 융합{c('naheem2022')}", "신발", "있음", "가려진 관측 제외", "없음"],
             [f"UWB·IMU 융합{c('decock2023')}", "몸통", "있음", "IMU 방향으로 오차 모형 선택", "없음"],
             ["본 연구", "발목", "있음", "길어진 거리만 엄격히 제거", "산출"]],
            [1.5, 1.1, 1.3, 2.2, 0.9])

    # ------------------------------------------------------------------ Ⅲ
    d.h1("Ⅲ. 연구방법 및 절차")
    d.h2("1. 시스템 구성과 장치")
    f_sys, f_photo = d.n_figure + 1, d.n_figure + 2
    d.p(f"시스템은 측정 장치, 수집 서버, 분석 서비스, 리포트 생성부로 이루어진다[그림 {f_sys}]. 장치에는 UWB 칩셋 DW3000과 "
        f"ESP32-S3가 든 MaUWB 보드{c('makerfabs')}를 썼고, 태그에는 가속도와 각속도를 재는 MPU6050을 연결하였다. 측정 "
        f"세트는 헤드(앵커 A0를 겸함), 앵커 A1~A3, 발목 바깥쪽에 차는 태그이다[그림 {f_photo}]. 태그는 10 ms마다 네 "
        "앵커까지의 거리를 한 번씩 재고 IMU를 100 Hz로 잰다.")
    d.figure(FIG / "fig_system.png", "시스템 구성과 자료의 흐름", 144)
    d.figure(FIG / "fig_photos.png", "제작한 장치. 태그와 헤드의 화면에 네 앵커까지의 거리가 표시된다", 144)
    d.h2("2. 융합 분석")
    f_nlos = d.n_figure + 1
    d.p("활보 하나 안에서 발이 움직인 모양은 IMU가 정하고, 공간 안에서의 위치와 진행 방향은 UWB가 정한다. 분석은 규칙과 "
        "필터로 하며 순서는 다음과 같다.")
    d.item("① 시각 맞춤. 태그가 보낸 시각과 헤드가 받은 시각의 쌍에 직선을 맞추어 두 시계의 빠르기 차이와 오프셋을 구한다.")
    d.item("② 입각기 검출(stance detection). 가속도와 각속도의 0.15초 통계량이 기준보다 작으면 입각기로 본다.")
    d.item("③ 융합. 위치, 속도, 자세, 센서 바이어스의 오차를 상태로 두는 오차 상태 칼만 필터에 영속도 보정과 앵커별 UWB "
           "거리를 넣는다. 발목 조건에 맞추어 센서에서 발목 관절 중심까지의 벡터를 줄 수 있게 하였다.")
    d.item(f"④ NLOS 거리 제거(게이팅). 측정 거리에서 예측 거리를 뺀 값을 그 차이의 표준편차로 나눈다. 이 값이 +1.5보다 "
           f"크면 가려진 거리로 보고 버리고, 짧은 쪽은 −3.0까지 받아들인다[그림 {f_nlos}]. 일반적인 융합은 양쪽에 같은 "
           "기준(±3)을 쓴다.")
    d.item("⑤ 스무딩. 측정이 끝난 뒤에 계산하므로 필터를 앞으로 돌린 뒤 뒤에서부터 다시 계산한다(RTS 스무딩).")
    d.figure(FIG / "fig_nlos.png", "앵커 배치와 몸 가림(가), 가려진 거리를 버리는 기준(나)", 144)
    d.p(f"걷기 구간(walking bout)은 Mobilise-D의 정의를 따랐다. 이 정의는 양발의 활보가 2개 이상 이어진 걷기를 걷기 "
        f"구간으로 보고{c('kluge2021')}, 활보는 0.15 m 이상, 쉼은 3초까지로 둔다{c('micoamigo2023')}. 태그가 한 발에만 "
        "있으면 반대쪽 발을 볼 수 없으므로 최소 길이를 한 발의 활보 4개로 높였다.")
    d.h2("3. 수집 서버와 리포트 생성")
    d.p("서버는 1초 묶음을 HTTPS로 받아 원본 파일에 쓰고, 디스크 기록을 확인한 뒤에만 수신 확인(ACK)을 보낸다. HTTP "
        "응답이 곧 수신 확인이므로 브로커가 필요 없고, 다시 온 묶음은 순번으로 알아보아 한 번만 저장한다. 걷기 구간의 "
        "원본은 영구히 보관하므로 분석 방법이 바뀌면 지난 자료를 다시 분석할 수 있다.")
    d.p(f"리포트는 언어 모델이 쓴다. 언어 모델은 사실과 다른 내용을 만들어 낼 수 있으므로{c('huang2024')} 모델이 논문 "
        "저장소(보행 논문 52편)를 검색해 근거로 쓰게 하고, 쓴 글을 서버가 세 가지로 검사한다. 모델은 "
        "측정값을 숫자로 적지 않고 값의 번호만 적으며 서버가 값을 넣는다. 인용은 실제로 검색된 단락만 허용한다. 서버는 "
        "값마다 지난 30일의 중앙값보다 큰지 작은지를 모델에 알려 주고, 글이 반대 방향으로 쓰였는지 검사한다. 요약에는 "
        "기본 값만 쓰게 하는 규칙도 함께 본다.")
    d.h2("4. 검증 방법")
    d.p("검증 자료의 종류는 결과마다 밝혔다. 실측은 두 가지이다. 정지 측정은 태그와 앵커를 줄자로 잰 거리에 두고 평균과 "
        "표준편차를 구한다. 통로 걷기는 줄자로 잰 직선 통로를 왕복하여 걸은 횟수와 총 이동 거리를 줄자 값과 견준다.")
    d.p("합성 보행은 참값을 아는 발의 운동에서 IMU 값과 거리를 계산하고 잡음을 더한 자료이다. IMU 잡음은 MPU6050 "
        f"데이터시트의 잡음 밀도{c('mpu6050')}에 해당하는 크기로 넣었다. 거리에는 3 cm의 잡음을 넣고, 전체의 약 45%에는 "
        "가림을 흉내 내어 0.15~0.5 m를 더하였다. 시계에는 20 ppm의 빠르기 차이와 12초의 오프셋을 넣었다. 가림의 크기와 "
        "빈도는 가정한 값이다. 게이팅 기준은 직선 보행 하나에서 골랐으므로 평가는 시드가 다른 20회와 다른 조건에서 "
        "하였다. 앵커 4대가 둘러싼 4 m × 5 m 구역을 도는 걸음에 기본 가림, 넓은 가림(0.05~1.0 m), 긴 가림(1초)을 넣은 "
        "세 조건과 8 m × 2 m 통로를 6.5 m 곧게 걷는 조건이다. 같은 자료에 IMU 단독, UWB 단독(삼변측량), 일반 융합, 본 "
        "연구의 방법을 적용하였다.")
    d.p("사람의 동작 자료로 만든 신호로도 확인하였다. 건강한 성인 138명과 뇌졸중 환자 50명의 전신 동작 자료(마커 방식, "
        f"100 Hz){c('vancriekinge2023')} 가운데 걷기 시험이 2초보다 짧은 5명을 뺀 183명을 썼다. 바깥 복사뼈 마커의 궤적과 "
        "정강이의 자세로 발목 IMU 값을 계산하고, 가상 앵커 4대까지의 거리를 만들었다. 가림은 합성 보행과 같은 무작위 구간과, 몸의 모양으로 판정한 가림의 두 가지로 넣었다. 뒤의 것은 "
        "태그와 앵커를 잇는 선이 다리나 몸통을 지나는 동안 거리를 0.05~0.25 m 늘리며, 이 크기는 가정한 값이다.")

    # ------------------------------------------------------------------ Ⅳ
    d.h1("Ⅳ. 연구결과 및 분석")
    d.h2("1. 장치의 거리 측정(실측)")
    t_static = d.n_table + 1
    d.p("태그와 앵커 1대를 고정하고 12초 동안 거리 1,194개를 모았을 때 표준편차는 1.93 cm, 측정 주기는 초당 99.5회였다. "
        "헤드의 출력을 10분 동안 기록한 시험에서는 거리 프레임 59,985개 가운데 누락이 21회(0.04%)였다. 줄자 거리에 대한 "
        f"정확도는 표 {t_static}에 적는다.")
    d.table("줄자로 잰 거리에서의 정지 측정(실측)",
            ["줄자 거리", "측정 평균", "오차(평균 − 줄자)", "표준편차", "표본 수"],
            [[f"{x} m", BLANK_M, BLANK_M, BLANK_M, BLANK_M] for x in (1, 2, 4, 6)],
            [1, 1, 1.3, 1, 1])
    d.h2("2. 융합의 정확도(합성 보행)")
    t_cmp, f_traj, f_cmp = d.n_table + 1, d.n_figure + 1, d.n_figure + 2
    d.p(f"네 방법의 위치 오차를 표 {t_cmp}에 정리하였다. 30초 길이의 네 조건에서 본 연구의 방법은 1.4~1.7 cm였고, IMU "
        f"단독은 21~23 cm, UWB 단독은 53~92 cm, 일반 융합은 9~28 cm였다. 120초를 걸으면 IMU 단독의 오차는 계속 커져 끝에서 1.0 m가 되었고, "
        f"본 연구의 방법은 2.5 cm 아래에 머물렀다[그림 {f_traj}]. UWB 거리가 IMU의 쌓이는 오차를 계속 바로잡고, IMU가 "
        "가려진 거리를 가려낼 예측을 주기 때문이다.")
    d.table("방법별 위치 오차(RMSE의 중앙값, 조건마다 20회)",
            ["조건", "UWB 단독", "IMU 단독", "일반 융합", "본 연구"],
            [["구역을 도는 걸음, 기본 가림", "58.4 cm", "23.4 cm", "22.2 cm", "1.7 cm"],
             ["구역을 도는 걸음, 넓은 가림", "92.0 cm", "23.4 cm", "9.0 cm", "1.7 cm"],
             ["구역을 도는 걸음, 긴 가림", "60.9 cm", "23.4 cm", "27.8 cm", "1.7 cm"],
             ["통로 직선 6.5 m", "53.0 cm", "20.9 cm", "27.7 cm", "1.4 cm"],
             ["구역을 도는 걸음 120초(10회)", "73.6 cm", "60.9 cm", "—", "1.7 cm"]],
            [2.4, 1, 1, 1, 1])
    d.figure(FIG / "fig_traj.png", "추정 궤적과 참값(가), 120초 걷기의 위치 오차(나, 띠는 10회의 10~90%)", 144)
    d.p(f"가림의 크기를 바꾸면 차이가 나는 구간이 드러난다[그림 {f_cmp}]. 거리가 15~30 cm 길어질 때 일반 융합의 오차는 "
        "14~27 cm였고 본 연구의 방법은 2 cm였다. 50 cm 이상 길어지면 일반 융합도 그 거리를 버려 두 방법이 같아졌다. "
        "5~10 cm의 작은 가림은 잡음과 구별되지 않아 본 연구의 방법도 오차가 4~5 cm였다. 활보 수는 모든 방법이 참값과 "
        "같았고, 활보장(stride length)의 평균 절대 오차는 본 연구의 방법이 0.2~0.4 cm, IMU 단독이 0.6~0.7 cm였다. "
        "융합의 이득은 활보장보다 공간 안에서의 위치와 방향에 있다.")
    d.figure(FIG / "fig_compare.png", "가림 크기에 따른 위치 오차(합성 보행, 크기마다 10회의 중앙값)", 144)
    d.p("오차가 20 cm를 넘은 것은 조건마다 20회 가운데 0~1회였고, 첫 활보에서 추정이 틀어진 뒤 가려지지 않은 거리까지 "
        "버려진 경우였다. 시계가 10 ms 어긋나면 위치 오차는 2.4 cm였고, 시각 맞춤의 오차는 합성 자료에서 0.1 ms였다.")
    d.h2("3. 융합의 정확도(실제 사람의 동작 자료)")
    t_mc = d.n_table + 1
    d.p(f"사람의 동작 자료에서 얻은 결과를 표 {t_mc}에 정리하였다. 무작위 구간 가림에서는 사람의 움직임으로 만든 신호에서도 본 연구의 방법이 "
        "1.0~1.2 cm였고 활보장 오차는 0.3 cm였다. 시험 하나가 약 3초로 짧아 IMU 단독의 오차도 4 cm에 그쳤다. 입각기 "
        "동안 정강이 각속도의 중앙값은 1.5 rad/s였고, 참 입각기 가운데 검출된 비율은 건강군 32%, 뇌졸중군 64%였다.")
    d.table("사람의 동작 자료에서 방법별 위치 오차(RMSE의 중앙값. 건강 133명 786회, 뇌졸중 50명 290회)",
            ["가림 모형, 집단", "UWB 단독", "IMU 단독", "일반 융합", "본 연구"],
            [["무작위 구간, 건강", "50.3 cm", "3.8 cm", "29.0 cm", "1.2 cm"],
             ["무작위 구간, 뇌졸중", "51.3 cm", "4.1 cm", "29.7 cm", "1.0 cm"],
             ["몸의 모양, 건강", "26.8 cm", "3.8 cm", "25.2 cm", "18.5 cm"],
             ["몸의 모양, 뇌졸중", "26.8 cm", "4.1 cm", "25.2 cm", "18.8 cm"]],
            [2.0, 1, 1, 1, 1])
    d.p("몸의 모양으로 판정한 가림에서는 결과가 달랐다. 태그가 발목 바깥쪽에 있어 반대편 앵커 2대가 자기 정강이에 걷는 내내 "
        "가려졌다. 계속 이어지는 가림은 위치가 통째로 밀린 것과 구별되지 않아 가려진 거리의 28%만 걸러졌고, 오차는 18.5 cm로 IMU "
        "단독보다 컸다. 본 연구의 기준은 가림이 생겼다 사라지는 경우에 듣는다. 걷는 내내 가려지는 앵커는 정강이의 "
        "방향으로 미리 가려내야 한다.")
    d.h2("4. 통로 걷기(실측)")
    t_walk = d.n_table + 1
    d.p("줄자로 4.04 m를 잰 직선 통로를 발목에 태그를 차고 왕복하였다. 걸은 횟수는 편도 22번이고, 줄자 거리로 모두 "
        "88.9 m이다. 측정한 150초 동안 거리 측정 14,361회(초당 약 96회)가 빠진 묶음 없이 서버에 들어왔다.")
    d.p("위치는 태그의 높이를 발목 높이 0.10 m로 두고 수평 방향만 계산하였다. 앵커 4대가 거의 한 평면에 놓여 있어 "
        "거리 4개로는 높이를 정하기 어려웠기 때문이다. 계산한 위치는 1초 길이로 스무딩하였다. 편도는 발이 통로 끝에서 "
        "돌아선 지점 사이로 세었다.")
    d.p(f"결과는 표 {t_walk}에 적었다. 편도 22번이 모두 세어졌다. 편도 길이는 평균 4.35 m로 줄자보다 0.31 m 길었다. "
        "총 이동 거리는 95.6 m로 줄자 거리보다 7.6% 길었다.")
    d.table("직선 통로 걷기(실측)",
            ["항목", "줄자로 잰 값", "측정한 값", "차이"],
            [["편도 횟수", "22번", "22번", "0번"],
             ["편도 길이(평균)", "4.04 m", "4.35 m", "+0.31 m"],
             ["총 이동 거리", "88.9 m", "95.6 m", "+7.6%"]],
            [1.6, 1.3, 1.3, 1.1])
    d.p("통로는 앵커 4대가 둘러싼 영역의 바깥에 있었다. 이 위치에서는 거리의 작은 오차가 위치에서 더 커진다. 앵커 영역 "
        "안에서의 걷기는 다음 측정에서 잰다. 이 절의 거리는 UWB 위치로 구한 값이다. IMU를 합친 궤적의 활보장은 실측 "
        "자료에서 아직 평가하지 않았다. 실측 통로는 앵커 영역 밖에 있어 합성 보행의 통로 조건과 직접 견줄 수 없다.")
    d.h2("5. 수집 서버와 리포트 생성부")
    d.p("묶음을 받는 도중에 서버를 강제로 끄고 다시 켠 시험에서 수신 확인을 보낸 묶음은 모두 남아 있었다. 전체 원본을 지우고 "
        "보관해 둔 걷기 원본만으로 다시 분석하였을 때 걷기 구간의 시각과 궤적 값이 처음과 같았다.")
    if rag:
        rag(d, c)
    else:
        t_r = d.n_table + 1
        d.p(f"리포트 생성부는 실제 모델 호출로 평가하고 있으며 결과는 표 {t_r}에 적는다.")
        d.table("리포트 생성부의 평가",
                ["항목", "지표", "결과"],
                [["논문 검색", "상위 5개 적중률(BM25 / 임베딩 / 합친 방식)", BLANK_E],
                 ["첫 초안", "서버 검사에 걸린 리포트의 비율", BLANK_E],
                 ["오류 주입", "종류별 검출률", BLANK_E],
                 ["인용", "인용한 단락이 문장을 뒷받침하는 비율", BLANK_E]],
                [1, 3, 1.2])

    # ------------------------------------------------------------------ Ⅴ
    d.h1("Ⅴ. 결론 및 고찰")
    d.p("합성 보행과 사람의 동작 자료로 만든 신호에서, 가림이 생겼다 사라지면 본 연구의 융합은 각 센서를 단독으로 쓸 "
        "때보다 위치 오차가 작았다. 30초 길이의 합성 보행에서 위치 오차는 본 연구의 방법이 1.4~1.7 cm, IMU 단독이 "
        "21~23 cm, UWB 단독이 53~92 cm, 일반 융합이 9~28 cm였다. 사람 183명의 동작 자료에서는 무작위 가림일 때 "
        "1.0~1.2 cm였다. 융합 궤적을 실측 자료로 평가하는 일은 남아 있다. 제작한 장치는 정지 상태에서 거리를 표준편차 "
        "1.93 cm로 초당 약 100회 재었다. 실측 통로 걷기에서는 편도 22번을 모두 세었고, UWB 위치로 구한 총 이동 거리는 "
        "줄자 거리보다 7.6% 길었다. "
        + (R.get("rag_answer") or "서버는 측정 원본을 보존하고 리포트의 수치, 인용, 증감 방향을 검사한다."))
    d.p("이 방법이 듣지 않는 조건도 확인하였다. 자기 정강이가 반대편 앵커를 걷는 내내 가리면 그 거리는 걸러지지 않아 오차가 "
        "18.5 cm로 커졌다. 발목 센서의 입각기는 사람의 동작 자료에서 참 입각기의 32~64%만 검출되었다. 가림의 크기는 "
        "가정한 값이다. 앵커 4대는 방 하나만 덮고, 태그의 배터리(110 mAh) 지속 시간은 아직 재지 않았으며, 집 안의 짧은 "
        "걸음은 한 발 활보 4개라는 기준에 못 미쳐 빠진다.")
    d.p("다음 단계는 세 가지이다. 첫째, 자기 다리와 몸에 가려질 때 거리가 길어지는 크기를 실측하고, 정강이의 방향으로 "
        "가려질 앵커를 미리 가려내는 처리를 더한다. 둘째, 이번에 만든 모사 방법으로 공개 동작 자료에서 발목 신호를 만들어 "
        f"증상 분석 모델을 학습한다. 파킨슨병 환자의 동작 자료{c('shida2023')}와 정강이 가속도계로 기록한 보행 동결 "
        f"자료{c('bachlin2010')}를 쓴다. 셋째, 헤드의 버퍼와 전송을 구현해 집에서 여러 날 잰다. 사람이 착용하는 실험은 "
        "참여자의 동의를 받아 진행하며, 환자를 대상으로 하는 단계에서는 기관생명윤리위원회(IRB)의 심의를 받는다.")
    d.blank()
    d.refs_title()
    for i, key in enumerate(c.order, 1):
        d.ref(f"[{i}] " + REFS[key].split(" DOI: ")[0])

    abstract = (
        "본 연구는 진단보다 먼저 나타나는 보행의 변화를 일상 공간에서 계속 재기 위해, 발목의 UWB 거리와 IMU를 합쳐 발 "
        "궤적과 활보(stride)를 구하는 방법과 시스템을 만들었다. IMU는 오차가 시간에 따라 쌓이고, 발목의 UWB는 몸에 가려져 "
        "거리가 길어진다. 본 연구는 영속도 보정과 UWB 거리를 오차 상태 칼만 필터로 합치고, 길어진 거리만 엄격히 버려 두 "
        "약점을 서로 메우게 하였다. 가림이 섞인 30초 길이의 합성 보행에서 위치 오차는 1.4~1.7 cm였고, 같은 자료에서 IMU "
        "단독은 21~23 cm, UWB 단독은 53~92 cm, 일반 융합은 9~28 cm였다. 사람 183명의 동작 자료로 만든 발목 신호에서는 "
        "무작위 가림일 때 1.0~1.2 cm였다. 자기 정강이가 앵커를 걷는 내내 가린다고 가정하면 18.5 cm로 IMU 단독(3.8 cm)보다 "
        "커서, 방향으로 가림을 미리 가려내는 처리가 더 필요하다. 제작한 장치는 정지 상태에서 거리를 표준편차 1.93 cm로 "
        "초당 약 100회 재었고, 실측 통로 걷기에서 UWB 위치로 구한 총 이동 거리는 줄자보다 7.6% 길었다. "
        + (R.get("rag_abstract") or "측정 원본을 보존하는 서버와, 언어 모델이 쓴 풀이의 수치·인용·증감 방향을 검사하는 "
           "리포트 생성부를 함께 만들었다."))
    return {"abstract": abstract, "keywords": "UWB, IMU, 보행 분석, 센서 융합, NLOS, 칼만 필터"}
