# uwb-health-server

UWB 보행 측정 세트(Head와 앵커 셋, 발목 Tag)가 1초마다 올리는 원본을 받아, 잃지 않고 쌓고, 분석 단위(구간)로 정돈하고, 분석 서비스(gaitfusion)를 불러 걷기 구간과 발 궤적을 남기는 서버입니다. 무엇을 해야 하는지는 [SPEC.md](SPEC.md) 하나에 있습니다. 1단계(원본 정돈)와 2단계의 지표 규약, 특징 풀기, 일별 값(M7–M9)을 만들었습니다. 2단계의 보고서는 논문 저장소와 명령줄 대화 도구(`tools/ask.py`)까지 있습니다. 단계별 상태는 SPEC 12절과 14.10절에 있습니다.

## 관련 저장소

이 서버는 세 저장소 가운데 하나입니다. 문서와 명령에 나오는 `../gaitfusion`, `../uwb-firmware`는 세 저장소를 한 폴더 아래에 나란히 받았을 때의 경로입니다.

| 저장소 | 내용 |
|---|---|
| [uwb-firmware](https://github.com/HyperBlaze456/uwb-firmware) | Head·앵커·Tag 펌웨어. Head가 서버에 올리는 바이트 형식(C1, `docs/wire.md`)과 시험 벡터를 소유 |
| uwb-health-server(이 저장소) | 원본 받기와 보관, 구간, 분석 호출, 지표와 보고서 |
| [gaitfusion](https://github.com/HyperBlaze456/gaitfusion) | UWB + IMU 융합과 분석 서비스. 서버가 구간마다 부름(C2) |

```sh
git clone https://github.com/HyperBlaze456/uwb-firmware.git
git clone https://github.com/HyperBlaze456/uwb-health-server.git
git clone https://github.com/HyperBlaze456/gaitfusion.git
```

## 빠른 시작 (개발)

필요한 것은 [uv](https://docs.astral.sh/uv/)와 Docker(Compose 포함)입니다. Python, 의존성, 실행은 모두 uv로 합니다. Python 3.14는 uv가 `.python-version`대로 받습니다. 버전은 `.python-version`, `pyproject.toml`, `uv.lock`에 고정되어 있습니다.

```sh
cp .env.example .env                                         # 처음 한 번. 개발에서는 값을 그대로 써도 됨
uv sync                                                      # .venv에 의존성 설치
docker compose -f compose.yaml -f compose.dev.yaml up -d db  # 시험용 PostgreSQL (localhost:5432)
uv run pytest                                                # 시험. DB가 없으면 DB 시험은 건너뜀
uv run lint-imports                                          # import 규칙(SPEC 3.2)
```

시험은 `.env`의 비밀번호로 같은 PostgreSQL에 `uwb_test` DB를 새로 만들어 씁니다. 같은 DB 서버에서 시험을 둘 이상 동시에 돌리면 `TEST_DB_NAME=uwb_test_다른이름`으로 DB 이름을 나눕니다.

이미지를 빌드해 스택 전체를 띄우는 전 구간 시험(`tests/e2e/`)은 켤 때만 돕니다. 각자 다른 compose 프로젝트와 포트를 써서 개발 스택 옆에서 돌고, 끝나면 내립니다.

```sh
UWB_E2E=1 uv run pytest tests/e2e -s       # 가짜 gaitfusion: 받는 도중 intake·db를 강제로 끄고 → 구간 → 분석 → 보관

# 진짜 gaitfusion(옆 저장소)으로: 받기 → 구간 → 분석 → 보관 → 앱 궤적, 원본을 지운 뒤 보관본 재분석(SPEC M3-9)
(cd ../gaitfusion && docker build -t gaitfusion:local .)
UWB_E2E=1 UWB_E2E_GAITFUSION_IMAGE=gaitfusion:local uv run pytest tests/e2e -s

# C2 계약 시험을 떠 있는 진짜 서비스에 대고(SPEC M3-7)
GAITFUSION_URL=http://127.0.0.1:8100 uv run pytest tests/contract/gaitfusion
```

진짜 gaitfusion 시험의 입력은 gaitfusion 합성 보행으로 만든 `tests/e2e/data/walk_two_feet.*`입니다. 가짜 Head의 보행 신호는 형식 시험용이라 진짜 서비스가 걸음으로 알아보지 못합니다. 만드는 법은 `tests/e2e/data/make_walk_two_feet.py`에 있습니다.

## 스택 띄우기

```sh
cp .env.example .env            # 비밀번호와 DATA_DIR을 고침
```

파일은 호스트의 `DATA_DIR`(기본 `/srv/uwb`)에 쌓이고 컨테이너 안에서는 `/data`입니다. 이미지는 root가 아닌 UID 10001로 돌므로, Linux 호스트에서는 처음에 폴더를 만들어 주인을 바꿉니다.

```sh
sudo mkdir -p /srv/uwb/{raw,rejected,archive,result,trajectory}
sudo chown -R 10001:10001 /srv/uwb
```

개발 PC에서는 `.env`의 `DATA_DIR`을 쓰기 가능한 폴더(예: `./data`)로 바꿔도 됩니다.

```sh
# 개발: proxy 없이 intake 8001, api 8000, db 5432를 열고, gaitfusion 자리에 가짜를 띄움
docker compose -f compose.yaml -f compose.dev.yaml up -d --build

# 개발인데 gaitfusion만 진짜 이미지로
GAITFUSION_IMAGE=gaitfusion:local docker compose -f compose.yaml -f compose.dev.yaml \
  -f tests/e2e/compose.real-gaitfusion.yaml up -d --build

# 운영: proxy(Caddy)가 443 하나만 엶. .env에 SITE_ADDRESS(도메인 또는 IP)를 둠. 도메인이 없으면 Caddyfile 참고
CORE_VERSION=$(git rev-parse --short HEAD) docker compose build
docker compose up -d
```

`migrate`가 먼저 한 번 돌고 끝나면 intake·core·api가 뜹니다. 상태 확인: `curl localhost:8001/healthz`, `curl localhost:8000/healthz`(개발). 첫 관리자는 `docker compose run --rm --no-deps api python tools/admin.py create-admin <이메일>`로 만듭니다.

이미지는 `Dockerfile` 하나이고 intake·core·api·migrate가 명령만 바꿔 씁니다. 의존성은 `uv sync --frozen`으로 `uv.lock` 그대로 설치합니다.

## 도구

모두 `uv run python tools/<도구>.py --help`로 사용법을 봅니다. 로그는 한 줄 JSON입니다. DB를 쓰는 도구는 프로세스와 같은 `DB_*` 환경 변수와 파일 뿌리 `DATA_ROOT`(기본 `/data`)를 씁니다. 그래서 보통은 컨테이너 안에서 돌립니다.

```sh
docker compose run --rm --no-deps intake python tools/reindex_raw.py
docker compose exec core python tools/verify_archive.py
```

호스트에서 돌릴 때는 `.env`를 읽고 주소와 파일 뿌리만 바꿉니다: `set -a; . ./.env; set +a; export DB_HOST=localhost DATA_ROOT=$DATA_DIR`.

| 도구 | 하는 일 | DB 역할 | SPEC |
|---|---|---|---|
| `fake_head.py` | 가짜 Head. 합성 보행 묶음을 intake에 보냄(시나리오 여럿) | 없음(HTTP) | TL-1 |
| `fake_gaitfusion.py` | 가짜 gaitfusion(C2) | 없음 | TL-2 |
| `reindex_raw.py [--since] [--device]` | raw 파일 → `intake.*` 행 | `intake_rw` | TL-3 |
| `verify_archive.py [--reindex] [--prefix]` | archive 파일의 sha256을 `archive.range`와 대조. `--reindex`는 빠진 행을 파일에서 다시 만듦 | `core_rw` | TL-4 |
| `reingest_rejected.py <파일> [--since]` | 거절한 묶음을 받기와 같은 경로로 다시 넣음 | `intake_rw` | TL-5 |
| `admin.py` | `create-admin`, `register-device`, `rotate-token`, `reanalyze` | `api_rw` | TL-6 |
| `import_usb.py <기록> --device MAC=TOKEN --utc-start ISO [--dry-run]` | Head USB 기록(`record_usb.py` 출력)을 1초 묶음으로 바꿔 intake에 보냄. `--dry-run`은 검사만 | 없음(HTTP) | TL-7 |
| `bench.py` | 장치 N대로 초당 1요청씩 보내고 intake 로그에서 p95를 셈 | 없음(`--register`는 `api_rw`) | TL-8 |
| `unpack_features.py --all \| --since ISO` | 결과 원문이 있는 지난 run마다 `feature` 작업을 넣음(지난 run 채우기, 새 표를 더한 뒤 다시 풀기) | `core_rw` | TL-9 |
| `resummarize.py --since 날짜 [--device]` | 그 한국 날짜부터 오늘까지 착용 기록이 있는 장치마다 `summary` 작업을 넣음(지표 목록의 `period` 지표나 `names`를 고친 뒤 일별 값 다시 계산) | `core_rw` | TL-11 |
| `ask.py --subject ID --from ISO --to ISO [--show-context \| --report \| --question 글] [--fake] [--trace-out]` | 착용자·기간의 지표 값을 맥락으로 모델과 묻고 답함(대화 코어). 모델이 논문 저장소를 스스로 찾아 인용. `--show-context`는 키 없이 맥락만, `--fake`는 키 없이 가짜 모델로 전 과정 | `api_rw`(읽기만) | TL-12 |
| `papers.py add \| build \| check \| search` | 논문 저장소(파일)에 논문을 넣고, 색인을 만들고, 지표 목록의 출처와 맞는지 보고, 대목을 찾음. 저장소 뿌리는 `--root` 또는 `DATA_ROOT/papers` | 없음 | TL-10 |
| `eval_retrieval.py` | 논문 찾기의 정량 평가. 질의 묶음 `eval/retrieval/queries.toml`로 방식마다 Hit@k와 MRR을 냄 | 없음 | RP-6 |
| `eval_checks.py` | 보고서 검사의 검출률. 오류를 하나씩 넣은 글(`eval/checks/`)을 검사에 돌림. 모델은 부르지 않음 | 없음 | RP-8 |
| `eval_report_runs.py run \| summarize \| judge \| make-corpus` | 실제 모델이 쓴 보고서로 검사와 인용을 평가. `--fake`는 키 없이 돎 | `api_rw`(읽기만) | RP-8 |

모델을 부르는 도구는 키를 환경 변수에서 읽습니다. `ask.py`와 `eval_report_runs.py`의 글 쓰는 모델은 `ANTHROPIC_API_KEY`, OpenRouter 임베딩(`papers.py build --embedder openrouter:<모델>`, `eval_retrieval.py`)과 판정 모델(`eval_report_runs.py judge`)은 `OPENROUTER_API_KEY`입니다. 키는 `.env`에만 두고 git에 넣지 않습니다.

## 평가 자료와 보고서 조립 코드

| 위치 | 내용 |
|---|---|
| `eval/retrieval/queries.toml` | 논문 찾기 평가의 질의 묶음 |
| `eval/checks/` | 보고서 검사 평가의 입력. 시연용 착용자(`S-DEMO`)의 맥락 파일과, 오류를 넣은 글 묶음 |
| `report/build/` | R&E 연구보고서(hwpx)를 조립하는 코드와 본문 글. `build.py`, `build_form.py` |
| `report/figures/src/` | 보고서 그림을 그리는 코드. `report/figures/icons/`는 Tabler Icons(MIT) |

학생 이름, 바탕 양식 파일, 사진, 실험 결과(`report/data/`), 만들어진 그림(`report/figures/out/`), 한글 파일(`*.hwp`, `*.hwpx`)은 저장소에 넣지 않습니다(`.gitignore`). 그래서 보고서 조립은 그 파일들이 있는 컴퓨터에서만 됩니다. 평가 도구의 결과는 `report/data/`에 쓰입니다.

## 데이터는 어디에

| 무엇 | 호스트 | 등급(SPEC 5.1) |
|---|---|---|
| raw: 받은 묶음 전체 | `DATA_DIR/raw/<장치>/<부팅>/<hour>.bin` | 30일 |
| archive: 걷기 구간 ± 15초 원본 | `DATA_DIR/archive/<장치>/<YYYY-MM>/...bin` | 영구 |
| rejected: 거절한 묶음 | `DATA_DIR/rejected/<장치>/<날짜>.bin` | 30일 |
| 결과 원문, 궤적 | `DATA_DIR/result/`, `DATA_DIR/trajectory/` | 재분석으로 다시 만듦 |
| DB | Docker 볼륨 `uwb_pgdata` | 표마다 다름 |

백업은 호스트 cron이 `ops/backup.sh`로 합니다(raw·archive 10분마다, DB 매일). 설치는 `ops/crontab`, 복구와 분기 복구 연습은 [ops/restore.md](ops/restore.md)에 있습니다.

## 원본 삭제 스위치 (`PURGE_ENABLED`)

지금은 **꺼져 있습니다**(`.env`의 `PURGE_ENABLED=false`). 꺼져 있으면 core가 매일 `PURGE_AT_UTC`(18:00 UTC = 한국 03:00)에 지울 대상의 수만 로그(`purge_dry_run`)에 적고 아무것도 지우지 않습니다.

켜면 지우는 것은 **수신 30일(`RAW_RETENTION_DAYS`)이 지난 전체 원본**뿐입니다. raw 파일과 그 `intake.chunk`·`health`·`system` 행인데, 그 안의 모든 묶음이 구간에 들어가 분석과 보관을 마친 파일만 지웁니다(SPEC AR-6). 거절한 묶음은 30일이 지나면 조건 없이 지웁니다. **걷기 원본(archive)은 지우지 않고 영구히 남습니다.** 사람 입력, survey 블록, 구간과 분석 결과도 남습니다.

켜면 30일이 지난, 걷기로 판정되지 않은 시간의 원본은 다시 볼 수 없습니다. 그래서 다음 두 가지를 마친 뒤에만 켭니다(AR-7).

1. AI 모델(gaitfusion의 걷기 구간 찾기)이 실제 기록에서 걷기를 빠뜨리지 않는지 확인했음.
2. 복구 연습(`ops/restore.md` 1절)을 한 번 통과했음.

지운 raw 파일은 `archive.purged` 표에 경로, 레코드 수, 시각이 영구히 남습니다. 파일 안의 레코드 하나라도 DB 행이 없거나, 분석 뒤에 더해진 묶음이 있거나, 보관 범위에 빠진 묶음이 있으면 그 파일은 지우지 않고 로그(`kept_reasons`)에 이유를 남깁니다.

켜기 전에 며칠 치 `purge_dry_run` 로그의 수가 예상과 맞는지 봅니다. 켜는 법: `.env`에서 `PURGE_ENABLED=true`로 바꾸고 `docker compose up -d core`.
