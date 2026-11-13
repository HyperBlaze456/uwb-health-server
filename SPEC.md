# uwb-health-server 스펙

이 서버가 무엇을 해야 하는지 정한 문서입니다. 이 문서만 읽고 서버를 만들고 검사할 수 있게 썼습니다. 예전 `server-architecture.md`(계획)와 `server-schema.md`(DDL)를 합친 것이고, 두 파일의 마지막 내용은 커밋 `727d0aa`에 있습니다.

## 0. 읽는 법

- **필수와 선택**: "합니다", "해야 합니다", "하지 않습니다"는 필수입니다. "할 수 있습니다"는 선택입니다.
- **요구사항 번호**: `IN-3`처럼 영역 머리글자와 번호를 붙였습니다. 시험(11절)과 코드 주석에서 이 번호로 가리킵니다. PL 공용, RG 사람 입력, IN 받기, SG 구간, AN 분석 호출, AR 원본 보관, AP 앱 API, TL 도구, OP 운영입니다. 2단계는 MC 지표 규약, FT 특징 풀기, SM 일별 값, RP 보고서입니다.
- **범위**: 이 문서의 1–13절이 **1단계**이고 지금 만듭니다. 14절은 **2단계 계획**입니다(1.2절). 1단계 다음에 M7부터 차례로 만듭니다(14.10절).
- **다른 문서**: 이 서버는 다음 문서와 계약을 맺습니다. 문서가 서로 다르면 계약 문서가 이깁니다.

| 문서 | 무엇 | 소유 |
|---|---|---|
| [wire.md](https://github.com/HyperBlaze456/uwb-firmware/blob/main/docs/wire.md) | C1: Head → 서버 바이트 형식, 응답, 검사 규칙, 시험 벡터 | 펌웨어 저장소 |
| 이 문서 7절 | C2: 서버 → gaitfusion 요청·응답 | 이 저장소 |
| 이 문서 8절 | C3: 앱 → 서버 REST API | 이 저장소 |
| 이 문서 14.4절과 `contracts/metrics.toml` | C4: 지표 규약. C2 응답 안의 분석 특징 필드와 서버가 계산하는 지표의 이름·단위·뜻(2단계) | 이 저장소 |
| [gait-features.md](https://github.com/HyperBlaze456/gaitfusion/blob/main/gait-features.md) | 지표를 고른 근거 문헌과 이유. 이름·단위·뜻이 C4와 다르면 C4가 이김 | gaitfusion 저장소 |

## 1. 목표와 범위

### 1.1 하는 일

집이나 병실에 UWB 앵커 4개(Head A0와 A1–A3)를 설치하고, 착용자가 발목 Tag(1개 기본, 2개는 빌드 선택)를 찬 채 생활합니다. Head는 1초마다 UWB 거리와 Tag IMU를 바이너리 묶음으로 묶어 이 서버에 HTTPS로 올립니다. 서버는 이것을 잃지 않고 받아, 분석할 수 있는 단위(구간)로 정돈하고, 분석 서비스(gaitfusion)에 보내 걷기 구간과 발 궤적을 받아 둡니다.

### 1.2 단계

| 단계 | 내용 | 이 문서 |
|---|---|---|
| **1단계: 원본 정돈** | 받기, 장치·착용자 등록, 구간 만들기, gaitfusion 호출과 응답 보관(걷기 구간의 시간 범위, 궤적, 응답 원문), 걷기 원본 영구 보관, 30일 지난 원본 삭제, 이것들을 보고 고치는 API | 1–13절. 지금 만듦 |
| **2단계: 지표 규약, 특징 풀기, 일별 값, 보고서** | 분석 결과를 주고받는 규약(C4), 응답의 보폭·걸음·회전·창·삽화를 줄 단위로 저장, 일별 지표 값과 추세 조회, 고른 기간의 보고서(지표 값 + 논문 근거 + 글)와 그 조회 | 14절 계획. gaitfusion이 낼 특징이 다 정해지기를 기다리지 않고, 형식이 바뀌어도 받아들임 |
| 3단계 | 질환별 선별 지수 | 14.1절. 자체 검증 데이터가 생긴 뒤 |

경계는 이렇습니다. 1단계 서버는 gaitfusion 응답 중 **걷기 구간이 언제였는지, 어느 발이었는지, 궤적 파일**만 해석합니다. 나머지 필드는 해석하지 않고 응답 원문 파일로 남깁니다. 그래서 2단계는 gaitfusion을 다시 돌리지 않고도 남긴 원문에서 표를 채울 수 있습니다.

### 1.3 목표와 성공 기준

| # | 목표 | 성공 기준 |
|---|---|---|
| G1 | **원본을 잃지 않고 쌓는다** | 수십 초 인터넷 끊김에서 묶음 손실 0. 더 긴 끊김은 빠진 묶음 수가 구간 표에 숫자로 보임. 서버 디스크가 죽어도 잃는 원본은 최근 10분 이하. 걷기 구간 원본은 영구 보관되어 언제든 재분석 가능 |
| G2 | **원본을 분석 단위로 정돈한다** | 구간마다 빈자리 수, 찬 발과 Tag, 앵커 배치, 시각 기준이 표에 있음. 같은 묶음 집합에서는 언제 받든 같은 구간이 나옴(13절 예외 하나) |
| G3 | **분석 서비스를 부르고 결과를 남긴다** | 구간이 닫힌 뒤 30분 안에 run, 걷기 구간, 궤적, 응답 원문이 저장됨. 다시 분석해도 이전 run이 남고 "현재"는 가장 최근 run |

지금 대상은 팀원 시험입니다. 실제 대상자로 넘어갈 때의 요건은 13절에 있습니다.

### 1.4 하지 않는 것

- 보폭 줄 저장, 일별 값, 보고서(2단계, 14절). 선별 지수 계산(3단계).
- Head 펌웨어의 전송부(펌웨어 저장소의 일). 1단계는 가짜 Head(TL-1)로 검사합니다.
- gaitfusion 서비스 구현(gaitfusion 저장소의 일). 1단계는 가짜 gaitfusion(TL-2)으로 검사하고, 진짜 서비스로는 C2 계약 시험(M3-7)과 진짜 서비스 전 구간 시험(M3-9)을 돌립니다.
- 원격 VM 배포와 proxy 인증서(12절 마지막 순서).

## 2. 구조

### 2.1 부품

| 부품 | 받는 것 → 내는 것 | 맡는 목표 |
|---|---|---|
| **Head 펌웨어** (펌웨어 저장소) | UWB 거리 + ESP-NOW IMU → 1초 바이너리 묶음을 HTTPS POST. 확인 못 받은 묶음은 PSRAM 버퍼(4 MB ≈ 14분)에서 다시 보냄 | G1 |
| **proxy** (Caddy) | 443 TLS → `/d/*`는 intake, 나머지는 api | — |
| **intake** | 묶음 → 전체 해독·필드 검사·CRC·중복 검사 → raw 파일 이어 쓰기 + `intake.*` 행 → 바이너리 응답 | G1 |
| **core** | `intake.chunk` → 구간(segment) → gaitfusion 호출(analysis) → 걷기 원본 보관과 30일 지난 원본 삭제(archive) | G1, G2, G3 |
| **gaitfusion** (별도 저장소, 상태 없는 HTTP) | 구간 바이트 + 문맥 → 결과 JSON + 궤적 바이너리(TRJ1) | G3 |
| **api** (FastAPI) | DB → 앱 REST, 로그인, 장치 등록, 착용 기록 | — |
| **db** (PostgreSQL 17) | 스키마 = 단계: `pipeline`·`registry`·`intake`·`segment`·`analysis`·`archive` | 전부 |

```
Tag ──ESP-NOW IMU 100 Hz──▶ Head(A0) ◀── UWB 거리 (A1–A3)
Head ──HTTPS POST 1초 묶음──▶ proxy ──▶ intake ──▶ raw 파일(30일) + intake.*
core: intake.chunk ──▶ segment ──▶ analysis(gaitfusion C2) ──▶ archive(걷기 원본 영구, 30일 지난 원본 삭제)
앱 ──HTTPS──▶ proxy ──▶ api ──▶ registry 쓰기, 나머지 읽기
```

### 2.2 설계 방식

| 방식 | 이 서버에서의 뜻 | 이유 |
|---|---|---|
| **단계별 흐름** (pipes and filters) | intake → segment → analysis → archive. 단계 하나 = 폴더 하나 = DB 스키마 하나. 단계 사이는 DB 표로만 이어짐 | 기능 하나가 한 곳에 모이고, 이름이 폴더·스키마·작업 종류에서 같음 |
| **계약 우선** | C1·C2·C3를 문서로 먼저 정하고, C1은 펌웨어가 만든 시험 벡터 파일을 두 쪽이 같이 읽음 | 세 저장소가 서로의 코드를 import하지 않고도 바이트가 맞음 |
| **같은 것을 두 번 받아도 결과가 같음** (멱등 받기) | Head는 확인 못 받으면 다시 보내고, 서버는 `(device_id, head_boot_id, chunk_seq)`로 중복을 걸러 냄 | "적어도 한 번" 보내기와 "정확히 한 번" 저장을 함께 얻음 |
| **원본은 추가만, 나머지는 다시 만들 수 있게** | 표와 파일마다 등급(5.1절). 되찾을 수 없는 것만 엄격히 보호 | 보호 비용을 필요한 곳에만 씀 |
| **결과는 덮어쓰지 않고 쌓음** | 재분석은 새 run. 현재 run은 "가장 최근에 끝난 run". 착용자 귀속은 조회 때 정함 | 결과를 제자리에서 고치는 UPDATE가 없어 실수로 잃을 길이 없음 |
| **순수 계산과 바깥 일을 나눔** | 규칙은 `rules.py`의 순수 함수. DB·파일·HTTP는 `store.py`·`run.py`·`pipeline/` | 규칙을 DB 없이 단위 시험으로 검사 |
| **DB 표로 된 작업 큐와 읽기 위치** | `pipeline.job`(`FOR UPDATE SKIP LOCKED`)과 `pipeline.cursor` | 메시지 브로커 없이 트랜잭션 하나로 "결과 저장 + 다음 작업 넣기" |
| **권한으로 경계를 강제** | 프로세스마다 DB 역할 하나, 자기 스키마만 쓰기 | 버그가 남의 단계 데이터를 고칠 수 없음 |

### 2.3 단계와 경계 규칙

| 단계 | 하는 일 | 프로세스 | 작업 종류 | 쓰는 것 |
|---|---|---|---|---|
| **intake** | C1 수신, 검사, 중복 제거, raw 파일 + 색인 | intake | — (HTTP) | `intake.*`, raw·rejected 파일 |
| **segment** | 구간 열고 닫기, system 블록 → 앵커 배치 | core (5초 루프) | — | `segment.*` |
| **analysis** | gaitfusion 호출, 응답 저장 | core | `analysis` | `analysis.*`, 결과·궤적 파일 |
| **archive** | 걷기 원본을 잘라 영구 보관, 30일 지난 원본 삭제 | core | `archive` | `archive.*`, archive 파일 |
| **feature** (2단계) | 결과 원문의 줄을 낸 그대로 저장(14.5절) | core | `feature` | `feature.*` |
| **summary** (2단계) | 착용자의 일별 지표 값(14.6절) | core | `summary` | `summary.*` |
| **report** (2단계) | 기간 보고서와 논문 저장소(14.7절). 지금은 논문 저장소만 있음 | core | `report` | `report.*`, 보고서·논문 파일 |
| **registry** | 사람이 넣는 것: 장치·착용자·착용·계정 | api | — | `registry.*` |
| **pipeline** | 공용: DB·파일·작업 큐·읽기 위치·시계·설정·마이그레이션 | 전부 | — | `pipeline.*` |

- **쓰기는 자기 스키마만.** 그 밖에는 `pipeline.job`에 작업을 넣는 것뿐입니다. 예외 하나: archive가 30일 지난 `intake` 행과 raw·rejected 파일을 지웁니다. intake·api는 DB 역할로, core 안의 세 단계는 시험(`tests/test_ownership.py`)으로 지킵니다.
- **읽기는 SQL로 어느 스키마든** 됩니다.
- **코드 import는 아래로만**(3.2절). 흐름 단계(intake, segment, analysis, archive, feature, summary, report)는 서로 import하지 않습니다. 둘 이상의 단계가 함께 쓰는 순수 계산(C1 해독, 지표 목록 풀이, 기간 계산)은 `pipeline/`에 둡니다.

## 3. 저장소와 코드 구조

### 3.1 기술

Python 3.14, FastAPI + uvicorn, psycopg 3, httpx, pytest, import-linter, argon2-cffi, PostgreSQL 17, Caddy 2, Docker Compose. 2단계의 글 쓰기는 anthropic SDK(`report/writer.py`에서만 import). 버전은 구현을 시작할 때 `pyproject.toml`과 `compose.yaml`에 고정합니다.

Python 버전, 의존성, 가상 환경, 실행은 모두 uv 하나로 관리합니다. 버전은 `.python-version`, 의존성은 `pyproject.toml`(정확한 버전)과 `uv.lock`에 둡니다. 명령은 `uv sync`, `uv run pytest`, `uv run python tools/<도구>.py`처럼 씁니다. Docker 이미지도 `uv sync --frozen`으로 같은 잠금 파일에서 설치합니다.

### 3.2 폴더

```
uwb-health-server/
  SPEC.md  README.md
  pyproject.toml  compose.yaml  compose.dev.yaml  Caddyfile  .env.example  Dockerfile
  migrations/   001_pipeline.sql 002_registry.sql 003_intake.sql 004_segment.sql
                005_analysis.sql 006_archive.sql 007_roles.sql
                008_analysis_result_status.sql                         ← 10절 그대로
                009_feature.sql 010_summary.sql                        ← 14.9절 그대로 (2단계)
  contracts/    metrics.toml                                           ← C4 지표 목록 (14.4절)
  src/uwbserver/
    pipeline/   db.py files.py jobs.py clock.py settings.py migrate.py wire.py log.py alert.py metrics.py
    registry/   rules.py store.py routes.py access.py
    intake/     rules.py store.py run.py routes.py
    segment/    rules.py store.py run.py routes.py
    analysis/   rules.py store.py run.py routes.py gaitfusion.py
    archive/    rules.py store.py run.py
    feature/    rules.py store.py run.py routes.py            (2단계)
    summary/    store.py run.py routes.py                     (2단계)
    report/     papers.py rules.py store.py writer.py chat.py (2단계. 작업·저장·앱 API는 아직)
    process/    intake.py core.py api.py
  tests/        pipeline/ registry/ intake/ segment/ analysis/ archive/
                contract/wire_vectors/  contract/gaitfusion/  e2e/(data/)  test_ownership.py
  tools/        fake_head.py fake_gaitfusion.py reindex_raw.py verify_archive.py
                reingest_rejected.py admin.py import_usb.py bench.py
                unpack_features.py resummarize.py papers.py ask.py     (2단계: TL-9, TL-11, TL-10, TL-12)
  ops/          backup.sh crontab restore.md
```

**파일 역할.** 단계 폴더 안 파일은 네 역할뿐이고, 없는 역할은 파일도 없습니다.

| 파일 | 들어가는 것 | 모르는 것 | 시험 |
|---|---|---|---|
| `rules.py` | 순수 계산(데이터클래스와 함수). intake는 검사 결과 코드, segment는 구간 규칙, analysis는 C2 응답 검사와 행 만들기, archive는 보관 범위와 삭제 조건, registry는 비밀번호·토큰 | DB·HTTP·파일 | 단위 시험 |
| `store.py` | 이 단계의 SQL 전부. 함수 하나 = 쿼리 하나 | HTTP | compose.dev의 db |
| `run.py` | 단계 실행: `step(conn)`(5초 루프) 또는 `handle(conn, job)`(작업). intake는 `receive`(받기 한 요청: 검사 → 파일 → fdatasync → DB)와 재색인·다시 넣기. HTTP와 도구가 같은 길을 쓰도록 여기 둠. rules·store·바깥 호출을 이음 | — | 가짜 gaitfusion + db |
| `routes.py` | FastAPI 라우터. 모든 단계가 `app_router`(C3), intake는 `device_router`(C1)도 | — | TestClient |

그 밖의 파일:
- `pipeline/wire.py`: C1 해독기와 검사기(wire.md "서버의 검사"). intake, segment, analysis, api, 도구가 모두 이것 하나를 씁니다. 바깥을 모르는 순수 코드입니다.
- `pipeline/metrics.py`: C4 지표 목록 읽기와 검사, 저장된 줄의 풀이(`normalize`), 기간의 지표 값 계산(`period_values`). feature, summary, report가 모두 이것 하나를 씁니다. 순수 코드입니다(14.4절, RP-3).
- `pipeline/`: `db.py`(연결·트랜잭션·잠금), `files.py`(5.2절 파일 형식), `jobs.py`(작업 넣기·가져가기·끝·실패·되살리기, 읽기 위치), `clock.py`(시험에서 바꿔 끼우는 시계), `settings.py`(4.3절), `migrate.py`, `log.py`(PL-5 한 줄 JSON), `alert.py`(OP-2 알림).
- 모든 DB 연결은 autocommit이고, 쓰기는 늘 `with conn.transaction():` 블록 안에서 합니다. 블록이 끝나야 커밋되므로 받기 응답은 커밋 뒤에 나갑니다.
- `analysis/gaitfusion.py`: C2 HTTP 클라이언트. `registry/access.py`: 로그인 확인과 권한 검사.
- `process/`: 프로세스 셋의 조립 지점. `intake.py` = intake의 `device_router` + 시작 때 재색인(IN-9). `core.py` = 5초 루프(`segment.run.step`) + 작업 스레드(작업 종류 → `analysis`·`archive`의 `run.handle`) + 매일 `archive.run.purge`. `api.py` = 모든 단계의 `app_router`.

**import 규칙.** 아래로만 import하고 CI에서 import-linter로 막습니다. `tools/`는 무엇이든 import할 수 있습니다.

```
process ──▶ intake · segment · analysis · archive · feature · summary · report ──▶ registry ──▶ pipeline
                    (흐름 단계끼리는 서로 import 금지)
```

```ini
[importlinter]
root_package = uwbserver

[importlinter:contract:layers]
name = 아래로만
type = layers
layers =
    uwbserver.process
    uwbserver.intake | uwbserver.segment | uwbserver.analysis | uwbserver.archive | uwbserver.feature | uwbserver.summary | uwbserver.report
    uwbserver.registry
    uwbserver.pipeline

[importlinter:contract:rules]
name = rules.py는 바깥을 모름
type = forbidden
source_modules = uwbserver.*.rules
forbidden_modules = psycopg httpx fastapi anthropic uwbserver.pipeline.db uwbserver.pipeline.files uwbserver.pipeline.jobs
```

`rules.py`가 `pipeline.wire`(순수 해독기)는 쓸 수 있습니다.

## 4. 실행과 배치

### 4.1 프로세스

| 프로세스 | 외부 포트 | DB 역할 | 볼륨 |
|---|---|---|---|
| proxy | 443 | — | certs |
| migrate | — | DB 소유자 | — |
| intake | 내부 8001 | `intake_rw` | raw·rejected (쓰기) |
| core | — | `core_rw` | raw·rejected (읽기·삭제), archive·result·trajectory (쓰기) |
| api | 내부 8000 | `api_rw` | raw·result·trajectory (읽기) |
| gaitfusion | 내부 8100 | — | — |
| db | — | — | pgdata |

```yaml
x-app: &app {build: ., image: uwb-server, env_file: .env}
x-ready: &ready {migrate: {condition: service_completed_successfully}}
services:
  proxy:   {image: caddy:2, ports: ["443:443"], volumes: [./Caddyfile:/etc/caddy/Caddyfile:ro, certs:/data]}
  db:      {image: postgres:17, environment: {POSTGRES_PASSWORD: "${DB_PASSWORD}"},
            volumes: [pgdata:/var/lib/postgresql/data]}
  migrate: {<<: *app, command: python -m uwbserver.pipeline.migrate, depends_on: [db]}
  intake:  {<<: *app, command: sh -c "python -m uwbserver.process.intake reindex && exec uvicorn uwbserver.process.intake:app --host 0.0.0.0 --port 8001",
            depends_on: *ready, volumes: ["${DATA_DIR}/raw:/data/raw", "${DATA_DIR}/rejected:/data/rejected"]}
  core:    {<<: *app, command: python -m uwbserver.process.core,
            depends_on: {<<: *ready, gaitfusion: {condition: service_started}},
            volumes: ["${DATA_DIR}:/data"]}
  api:     {<<: *app, command: uvicorn uwbserver.process.api:app --host 0.0.0.0 --port 8000,
            depends_on: *ready, volumes: ["${DATA_DIR}:/data:ro"]}
  gaitfusion: {image: ghcr.io/<org>/gaitfusion:<tag>, command: uvicorn gaitfusion.service:app --host 0.0.0.0 --port 8100}
volumes: {certs: {}, pgdata: {}}
```

- intake·core·api는 이미지 하나(`uwb-server`)이고 명령만 다릅니다. 셋을 나누는 이유는 재시작 단위와 DB 권한입니다.
- `migrate`는 시작할 때 한 번 돌고 끝납니다. 나머지는 그 뒤에 뜹니다. db 연결이 될 때까지 다시 시도합니다.
- 파일은 호스트 디렉토리 `DATA_DIR`(기본 `/srv/uwb`)에 둡니다. 호스트 cron이 `ops/backup.sh`로 바로 백업합니다.
- `compose.dev.yaml`은 proxy를 빼고 intake 8001·api 8000·db 5432를 호스트에 노출하며, gaitfusion 자리에 `tools/fake_gaitfusion.py`를 띄웁니다.
- intake는 uvicorn 작업자를 여럿 띄울 수 있습니다. 같은 장치의 요청은 DB 잠금으로 줄을 세웁니다(IN-4).

### 4.2 DB 역할

| 역할 | 할 수 있는 것 |
|---|---|
| `intake_rw` | `registry.device` 읽기, `intake.*` 읽기·INSERT, `archive.purged` 읽기(재색인이 삭제한 파일을 건너뜀). 커밋은 디스크를 기다리지 않음(`synchronous_commit = off`) |
| `core_rw` | 전부 읽기. `segment.*` INSERT·UPDATE, `analysis.*` INSERT(궤적 행만 DELETE), `archive.*` INSERT, `pipeline.job` INSERT·UPDATE·DELETE, `pipeline.cursor` UPDATE, `intake.chunk`·`health`·`system`·`rejected` DELETE(30일 삭제만). 2단계: `feature.*`·`summary.*` INSERT·DELETE(다시 풀기와 다시 계산은 지우고 새로 넣음) |
| `api_rw` | 전부 읽기. `registry.*` INSERT·UPDATE, `registry.app_token` DELETE, `pipeline.job` INSERT |

아무에게도 없는 권한: `archive.*` UPDATE·DELETE, `intake.*` UPDATE, `intake.survey` DELETE, `analysis.*`(궤적 제외) UPDATE·DELETE, `registry.*`(로그인 토큰 제외) DELETE, `segment.*` DELETE.

### 4.3 설정 (`.env`)

| 이름 | 기본값 | 뜻 |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME` | `db`, 5432, `uwb` | DB 주소. 프로세스와 도구가 같이 씀 |
| `DB_OWNER` | `uwb` | DB 소유자 이름(`migrate`가 씀). compose의 `POSTGRES_USER` |
| `DB_PASSWORD`, `DB_INTAKE_PASSWORD`, `DB_CORE_PASSWORD`, `DB_API_PASSWORD` | 없음 | 소유자와 역할 셋의 비밀번호. `migrate`가 역할에 적용 |
| `DATA_DIR` | `/srv/uwb` | 호스트의 파일 뿌리. compose가 컨테이너의 `/data`에 붙임 |
| `DATA_ROOT` | `/data` | 프로세스 안의 파일 뿌리. DB에는 늘 `/data/...` 꼴의 논리 경로를 적고 여기로 풂(그래서 덤프를 다른 호스트에 복구해도 경로 열이 맞음). 호스트에서 도구를 돌릴 때는 `DATA_DIR`과 같게 |
| `GAITFUSION_URL` | `http://gaitfusion:8100` | C2 주소 |
| `GAITFUSION_TIMEOUT_S` | 600 | C2 요청 하나의 시간 한도 |
| `ANALYSIS_WORKERS` | 1 | 동시에 도는 analysis 작업 수 |
| `ANALYSIS_PARAMS` | `{}` | C2 `context.params` 기본값(JSON). 비운 키는 서비스 기본값 |
| `SEGMENT_SILENCE_S` | 60 | 수신이 이만큼 없으면 구간을 닫음 |
| `SEGMENT_GAP_S` | 60 | Head 시각으로 이만큼 비면 새 구간 |
| `SEGMENT_MAX_S` | 3600 | 구간 최대 길이 |
| `ARCHIVE_PAD_S` | 15 | 걷기 구간 앞뒤로 더 보관하는 초 |
| `RAW_RETENTION_DAYS` | 30 | 전체 원본 보존 기간 |
| `PURGE_ENABLED` | `false` | 30일 삭제를 실제로 할지. 꺼져 있으면 지울 대상만 로그에 적음(AR-7) |
| `PURGE_AT_UTC` | `18:00` | 매일 삭제를 도는 시각(한국 03:00) |
| `APP_TOKEN_TTL_DAYS` | 30 | 앱 로그인 토큰 수명 |
| `INTAKE_WORKERS` | 2 | intake의 uvicorn 작업자 수(4.1절) |
| `ALERT_WEBHOOK_URL` | 없음 | 알림을 POST할 곳(OP-2). 없으면 로그(`event = alert`)와 cron 메일만 |
| `CORE_VERSION` | 이미지 빌드 때 git sha | `analysis.run.core_version` |

## 5. 데이터 등급, 파일 형식, 보존

### 5.1 등급

| 등급 | 뜻 | 대상 | 바뀌는 방식 | 백업 | 삭제 |
|---|---|---|---|---|---|
| **영구 원본** | 잃으면 되찾을 수 없음 | archive 파일, `archive.range`·`archive.cut` | 한 번 쓰고 수정 없음 | 10분마다 바깥 복사. 원격은 버전 보존, 삭제 금지 | 없음 |
| | | `registry.*` (사람 입력) | api가 INSERT·UPDATE | DB 덤프 | 없음(토큰 폐기는 UPDATE) |
| | | `intake.survey` | INSERT만 | DB 덤프 | 없음 |
| **임시 원본** | 30일 동안 전체 재분석용 | raw 파일, `intake.chunk`·`health`·`system` | 이어 쓰기·INSERT만 | raw는 10분마다 바깥 복사(원격 35일), 표는 DB 덤프 | 30일 + 삭제 조건(AR-6) |
| | 거절한 묶음(진단·다시 넣기용) | rejected 파일, `intake.rejected` | 이어 쓰기·INSERT만 | 안 함 | 30일, 조건 없음 |
| **결과** | 원본에서 다시 계산 가능. 영구 | `segment.*`\*, `analysis.*`(궤적 제외), 결과 원문 파일 | analysis는 INSERT만, segment는 열림·닫힘 UPDATE | DB 덤프. 결과 원문 파일은 안 함(재분석으로 되살림) | 없음. 옛 run도 비교용으로 남김 |
| | 결과 원문과 줄에서 다시 만들 수 있음 (2단계) | `feature.*`, `summary.*` | 다시 풀기·다시 계산은 그 run·그날의 행을 지우고 새로 넣음 | DB 덤프 | 없음 |
| **캐시** | 지워도 재분석으로 되살림 | 궤적 파일, `analysis.trajectory` | 새 run이 끝나면 같은 구간의 옛 run 것 삭제 | 안 함 | 옛 run 것은 즉시. 디스크가 모자라면 오래된 것부터 |
| **작업 기록** | 흐름을 돌리는 데만 씀 | `pipeline.*` | 자유 | DB 덤프에 함께 | `done` 작업은 30일 뒤 |

\* `segment`는 30일이 지나면 다시 만들 수 없습니다(수신 시각과 빈자리 수가 여기에만 남음). 결과이지만 지우지 않고 DB 덤프로 지킵니다.

### 5.2 파일 형식 (`pipeline/files.py`)

모든 값은 little-endian입니다. 파일 이름의 날짜·시각은 UTC입니다.

| 파일 | 경로 | 레코드 |
|---|---|---|
| raw | `/data/raw/<device_id>/<head_boot_id 8자리 16진>/<YYYY-MM-DDTHH>.bin` | `u32 len · i64 received_utc_us · len 바이트 묶음` (머리 12 B) |
| archive | `/data/archive/<device_id>/<YYYY-MM>/<start_at>_<head_boot_id 8자리 16진>_<first_seq>-<last_seq>.bin` (`start_at`은 `2026-10-26T03-12-05Z` 꼴, seq는 10진) | raw와 같음 |
| rejected | `/data/rejected/<device_id>/<YYYY-MM-DD>.bin` | `u32 len · i64 received_utc_us · u8 code · u8 check · u16 0 · len 바이트` (머리 16 B). 요청 프레이밍이 깨진 경우는 본문 전체를 `code` = 255로 한 레코드 |
| 결과 원문 | `/data/result/<run_id>.json.gz` | C2 응답의 `result` 파트 바이트를 gzip한 것 |
| 궤적 | `/data/trajectory/<run_id>/<bout idx>-<side>.trj` | C2 응답의 TRJ1 파트 그대로(7.3절) |

- raw·rejected는 장치마다 이어 쓰기만 합니다(`O_APPEND`). 같은 묶음 키가 raw에 두 번 있을 수 있습니다(응답 직전에 멈춘 뒤 Head가 다시 보낸 경우). 정본은 `intake.chunk`가 가리키는 쪽이고, 재색인은 먼저 나온 것을 씁니다.
- raw·archive를 읽을 때는 레코드마다 묶음의 magic과 CRC를 확인합니다. 쓰다 만 레코드(작업자가 쓰는 중에 죽음)가 파일 **중간**에 있어도 그 뒤에서 다음 온전한 레코드를 찾아 계속 읽습니다. 시작 때 재색인(IN-9)이 자르는 것은 마지막 온전한 레코드 **뒤**의 꼬리뿐입니다. 그래서 확인 받은 묶음을 자르지 않습니다.
- archive·결과 원문·궤적은 임시 이름으로 쓰고 `fsync` 뒤 이름을 바꿉니다. 이름을 바꾼 뒤에야 DB 행을 넣습니다.
- 파일만 있어도 색인을 다시 만들 수 있습니다: raw → `intake.*`(`reindex_raw.py`), archive → `archive.range`(`verify_archive.py --reindex`). archive 행을 파일에서 다시 만들 때 `device_id`·`head_boot_id`·seq 범위는 경로에서, `segment_id`는 `(device_id, head_boot_id, seq 범위)`를 덮는 구간에서 찾고, 그 범위를 만든 run은 알 수 없으므로 `from_run_id = NULL`입니다. 덮는 구간이 아직 없으면(DB를 복구한 직후) core가 raw 재색인분을 처리한 뒤에 다시 돌립니다.
- gaitfusion에 보낼 때는 raw·archive 레코드의 12 B 머리를 C1의 `u32 len`으로 바꿔 이어 붙입니다.

### 5.3 보존

- 전체 원본(raw 파일, `intake.chunk`·`health`·`system`)과 거절한 묶음: **30일**.
- 걷기 원본(archive), 사람 입력(`registry`), survey 블록(`intake.survey`), 구간과 분석 결과: **영구**. 궤적은 캐시입니다.
- 원본 삭제는 `archive.run.purge`(매일 1회) 한 곳에서만 합니다. 다른 코드는 원본을 지우지 않습니다.
- 30일이 지나면 걷기로 판정되지 않았던 시간은 다시 볼 수 없습니다. 그래서 gaitfusion의 걷기 구간 찾기를 실제 기록으로 확인하기 전에는 삭제를 켜지 않습니다(`PURGE_ENABLED = false`, AR-7).

## 6. 요구사항 (1단계)

### 6.1 공용 (PL)

- **PL-1 마이그레이션.** `migrate`는 `pipeline.migration(name, applied_at)`을 먼저 만들고, `migrations/`에서 아직 적용하지 않은 파일을 번호 순으로 하나씩 트랜잭션으로 적용해 기록합니다. 적용한 파일은 고치지 않고 `NNN_<단계>_<내용>.sql`을 추가합니다. 처음 묶음(001–007)은 GRANT를 모두 `007_roles.sql`에 두고, 008부터는 표를 만드는 파일이 그 표의 GRANT도 함께 적습니다. 역할은 클러스터 전체에 하나이므로 `007`은 이미 있는 역할을 만들지 않습니다(같은 클러스터에 시험 DB를 여럿 만들 수 있게). 그 뒤 역할 비밀번호를 `.env` 값으로 `ALTER ROLE … PASSWORD` 합니다. 다시 돌리면 아무것도 바뀌지 않아야 합니다.
- **PL-2 작업 큐.** 상태는 `queued → running → done | failed`입니다.
  - 가져가기: `state = 'queued' AND run_after <= now()`인 것을 `ORDER BY run_after FOR UPDATE SKIP LOCKED LIMIT 1`로 잡아 `running`, `locked_by`, `locked_at`을 씁니다.
  - 실패(재시도할 것): k번째 실패(k = 1, 2, 3)면 `attempts = k`, `run_after = now + 2^k 분`(2, 4, 8분), `queued`로 돌림. 4번째 실패면 `failed`. 처음 시도까지 모두 네 번 시도합니다.
  - 실패(재시도 안 할 것): 바로 `failed`, `last_error`에 이유.
  - 되살리기: core가 시작할 때와 매 루프마다 `locked_at`이 `GAITFUSION_TIMEOUT_S + 300초`보다 오래된 `running` 작업을 실패 한 번으로 셉니다(위 규칙과 같은 한도).
  - 같은 대상의 `queued`·`running` 작업은 하나뿐입니다(부분 고유 색인). 예외는 2단계의 `summary`입니다: `queued`만 하나로 묶어, 도는 중에 하나 더 대기할 수 있습니다(SM-1). 그래서 실패하거나 되살린 `summary` 작업을 `queued`로 돌릴 때 같은 대상의 대기 작업이 이미 있으면, 그 작업은 대기 작업이 대신하는 것으로 보고 `done`으로 닫습니다. 넣기는 `ON CONFLICT (…) WHERE … DO NOTHING`입니다. 작업이 끝나며 같은 대상의 다음 작업을 넣을 때는 같은 트랜잭션에서 자기를 먼저 `done`으로 바꾼 뒤 넣습니다.
  - 정리: `core.py`가 매일 `pipeline.jobs.cleanup()`을 불러 30일 지난 `done` 작업을 지웁니다. 원본이 아니므로 `PURGE_ENABLED`와 무관합니다.
- **PL-3 읽기 위치.** `pipeline.cursor('chunk')`는 segment가 처리한 `intake.chunk.id`의 최댓값입니다. 처리한 행들의 결과와 같은 트랜잭션에서 고칩니다. 이것이 맞으려면 **id 순서 = 커밋 순서**여야 하므로, `intake.chunk`에 INSERT하는 모든 트랜잭션(intake, 재색인, 다시 넣기)은 INSERT 직전에 전역 잠금 `pg_advisory_xact_lock(1)`을 잡고 커밋할 때까지 둡니다. 그러면 segment가 id k를 볼 때 k보다 작은 id는 모두 커밋되었거나 버려진 것입니다.
- **PL-4 시계.** 현재 시각은 모두 `pipeline/clock.py`에서 얻습니다. 시험은 시계를 바꿔 끼워 60초 침묵, 30일 삭제를 흉내 냅니다.
- **PL-5 로그.** 모든 프로세스는 표준 출력에 한 줄 JSON 로그를 씁니다. 받기 요청마다 장치, 묶음 수, 코드별 수, 단계별 소요(해독·파일·fdatasync·DB, ms)를 적습니다. 9절의 속도는 이 로그로 잽니다.
- **PL-6 상태 확인.** intake·api는 `GET /healthz`(DB 연결과 데이터 디렉토리 쓰기 가능 여부)를 둡니다. core는 마지막 루프 시각을 파일 `/data/core.alive`에 매 루프 씁니다.

### 6.2 사람 입력 (RG)

- **RG-1 장치 등록.** admin이 Head의 Wi-Fi MAC으로 장치를 만듭니다. `id = 'head-' + MAC 12자리 소문자`. 장치 토큰은 32 바이트 무작위값의 base64url(패딩 없음, 43자)이고, 만들 때 응답에 한 번만 보입니다. DB에는 그 ASCII 문자열(보낸 그대로)의 sha256만 둡니다. 로그인 토큰(RG-6)도 같은 방식입니다.
- **RG-2 토큰 재발급과 폐기.** 재발급하면 이전 토큰은 그 즉시 안 됩니다. 폐기하면 `token_hash = NULL`이고 그 장치의 받기는 401입니다.
- **RG-3 착용자.** admin이 만들고 고칩니다. `code`는 실명이 아닌 표시명입니다. 키, 다리 길이, 마비측(`paretic_side`: `left`·`right`·NULL)을 둡니다.
- **RG-4 착용 기록.** 착용자, 장치, 선택적인 `tag_mac`, 시작, 끝(NULL = 착용 중). 고칠 수 있는 것은 시작과 끝뿐이고, 착용자나 장치를 바꾸려면 끝내고 새로 만듭니다. 착용 기록이 바뀌어도 결과 표는 고치지 않습니다(귀속은 조회 때, SG-9).
- **RG-5 계정.** 이메일 + 비밀번호(argon2id). 역할은 `member`(자기 착용자 하나)와 `admin`. 첫 admin은 `tools/admin.py create-admin`으로 만듭니다.
- **RG-6 로그인 토큰.** 32 바이트 무작위값, DB에는 sha256. 수명 `APP_TOKEN_TTL_DAYS`. 로그아웃하면 `revoked_at`. 만료된 토큰은 로그인할 때 지웁니다.

### 6.3 받기 (IN)

- **IN-1 엔드포인트.** `POST /d/v1/chunks` 하나입니다. 요청과 응답의 바이트, 결과 코드, HTTP 상태는 [wire.md](https://github.com/HyperBlaze456/uwb-firmware/blob/main/docs/wire.md) "전송"을 그대로 따릅니다.
- **IN-2 인증.** `Authorization: Bearer`의 sha256으로 `registry.device`를 찾습니다. 없거나 폐기되었으면 401이고 아무것도 쓰지 않습니다. 요청 안의 값으로 장치를 정하지 않습니다.
- **IN-3 검사.** 묶음마다 wire.md "서버의 검사" 1–10을 순서대로 하고 처음 실패한 단계의 코드를 씁니다. 해독과 검사는 `pipeline/wire.py` 하나가 합니다. 값을 고쳐서 받지 않습니다.
- **IN-4 줄 세우기.** 요청 하나 = 트랜잭션 하나이고, 트랜잭션 처음에 장치 잠금 `pg_advisory_xact_lock(2, hashtext(device_id))`를 잡습니다. 같은 장치의 요청이 겹치면 차례로 처리됩니다. INSERT 직전에는 PL-3의 전역 잠금을 잡습니다(잠그는 순서는 늘 장치 → 전역). 전역 잠금은 INSERT부터 커밋까지만이라 1 ms 안팎입니다.
- **IN-5 중복.** 요청 안 모든 키를 한 번의 SELECT로 확인합니다. 이미 있으면 `duplicate`(1)이고 raw에 다시 쓰지 않습니다. 같은 요청 안의 반복도 뒤의 것이 `duplicate`입니다.
- **IN-6 쓰기 순서.** 아래 순서를 지킵니다. 0·1 코드는 3번이 끝난 묶음에만 줍니다.
  1. 검사를 모두 끝냅니다.
  2. 통과한 묶음을 raw 파일에(수신 시각의 hour 파일, 부팅 폴더), 거절한 묶음을 rejected 파일에 이어 씁니다.
  3. 건드린 파일마다 `fdatasync`를 한 번 합니다. 이번에 새로 만든 파일이나 폴더가 있으면 그 부모 폴더도 `fsync`합니다(전원이 나가도 새 파일이 사라지지 않게).
  4. 전역 잠금(PL-3)을 잡고 표마다 다중 행 INSERT 한 번씩: `intake.chunk`, 블록이 있으면 `intake.health`·`system`·`survey`, 거절이 있으면 `intake.rejected`.
  5. 커밋하고 응답합니다.
  파일 쓰기나 `fdatasync`나 DB가 실패하면 503이고 응답 코드를 주지 않습니다(Head가 다시 보냄).
  - **400**(프레이밍이 깨짐): 본문 전체를 rejected 파일에 `code` = 255 레코드로 쓰고 2–5와 같이 `fdatasync`, `intake.rejected` 행, 커밋한 뒤 400을 줍니다. 그 쓰기가 실패하면 503입니다.
  - **413**: `Content-Length`가 131,072를 넘으면 본문을 읽기 전에 413을 줍니다. `Content-Length`가 없으면 131,073 바이트까지만 읽고 넘으면 413입니다. 아무것도 쓰지 않습니다. proxy(Caddy)도 `/d/*`에 같은 한도(`request_body max_size`)를 둡니다.
- **IN-7 행의 값.**
  - `intake.chunk`: 머리 필드 복사, `status_mask`(bit0 system, bit1 health, bit2 survey), `tag_mask`, 그리고 Tag 자리마다 그 `tag_id`의 첫 IMU 프레임의 `tag_mac`·`tag_boot_id`(IMU 프레임이 없으면 NULL), raw 파일 위치.
  - 블록 행: 키는 묶음 키, `at`은 묶음 시각(flags bit0이면 `utc_us_start`, 아니면 수신 시각 − 1초) + `at_offset_us`. 조회 열은 10절 DDL의 주석대로. `block`은 payload 바이트 그대로입니다.
  - `intake.rejected`: 코드, 검사 단계 번호, 걸린 필드 이름(`detail`), 머리를 읽을 수 있었으면 `head_boot_id`·`chunk_seq`, rejected 파일 위치.
- **IN-8 받기는 다른 묶음을 읽지 않습니다.** 장치 토큰 조회와 중복 확인 말고는 표를 읽지 않습니다. 묶음 사이의 일관성은 segment가 봅니다.
- **IN-9 시작 때 재색인.** intake는 받기를 열기 전에 raw 파일 중 이름의 hour가 `(intake.chunk의 최대 received_at − 1시간)`의 hour 이후인 것을 모두 처음부터 읽어, `intake.chunk`에 없는 키를 같은 해독기로 다시 넣습니다(`ON CONFLICT DO NOTHING`). `synchronous_commit = off`라 서버가 비정상 종료되면 마지막 1초 안쪽 행이 빠질 수 있기 때문입니다. 기준이 재시작 시각이 아니라 마지막으로 남은 행이므로, 오래 꺼져 있다 켜도 빠진 행을 찾습니다. 표가 비어 있으면 raw 파일 전부입니다. 재색인은 intake 컨테이너가 uvicorn을 띄우기 전에 한 번 돌리고(`python -m uwbserver.process.intake reindex`), 그동안 받기는 열려 있지 않습니다(Head는 연결 실패로 보고 다시 보냄). 파일 하나 = 트랜잭션 하나이고, 각 트랜잭션은 받기와 같은 순서로 장치 잠금과 전역 잠금(PL-3)을 잡습니다. DB만 재시작한 경우(intake는 그대로)에도 같은 기준으로 재색인합니다: intake가 30초마다 `pg_postmaster_start_time()`을 보고, 바뀌었으면 받기를 연 채로 재색인합니다(이때는 꼬리를 자르지 않음).
- **IN-10 거절한 묶음 다시 넣기.** `tools/reingest_rejected.py <파일> [--since]`는 rejected 레코드를 받기와 같은 검사·쓰기 경로로 다시 넣습니다. 서버의 해독기를 고친 뒤(예: 새 `version` 지원) 씁니다. 토큰 대신 파일 경로의 `device_id`로 장치를 정하고, `intake_rw` 역할로 접속합니다. 통과한 것은 raw와 `intake.chunk`에 들어가고 rejected 레코드는 그대로 둡니다.

### 6.4 구간 (SG)

구간은 한 장치·한 부팅 안에서 끊김 없이 이어진 묶음들이고 분석 단위입니다. 묶음이 어느 구간에 들어가는지는 묶음 안의 값(seq, Head 시각, Tag, 배치)으로 정하고, 도착 시각은 **언제 닫을지**만 정합니다.

- **SG-1 루프.** core는 5초마다 `intake.chunk`에서 `id > cursor`인 행을 `id` 순으로 최대 1000개 읽어 차례로 처리하고, 결과와 읽기 위치를 한 트랜잭션에 씁니다.
- **SG-2 프레임 없는 묶음.** `n_range = 0`이고 `n_imu_frames = 0`인 묶음(상태 블록만 있음)은 구간에 넣지 않습니다. 그 안의 system 블록은 SG-8로 처리합니다.
- **SG-3 넣을 자리 찾기.** 묶음 c에 대해 같은 `(device_id, head_boot_id)`의 구간 중 `first_seq ≤ c.seq`인 것 가운데 `first_seq`가 가장 큰 구간을 P라 합니다.
  1. **P 안쪽**(`c.seq ≤ P.last_seq`): 늦게 온 묶음이 빈자리를 메운 것입니다. P에 더합니다. 나눔 조건(SG-4)은 보지 않습니다. 배치가 P와 다르거나 Tag가 P의 `segment_tag`와 다르면 경고 로그만 남깁니다.
  2. **P 뒤**(`c.seq > P.last_seq`): SG-4의 나눔 조건이 하나도 없으면 P에 더합니다. 조건이 있으면, P가 열려 있을 때만 그 이유로 닫고(이미 닫혀 있으면 P의 `closed_reason`은 그대로), c로 새 구간을 만듭니다.
  3. **P 없음**: c로 새 구간을 만듭니다.
  구간은 합치지 않습니다. 경우마다 바뀌는 열:

| 열 | 새 구간 | P 안쪽에 더함 | P 뒤에 더함 |
|---|---|---|---|
| `first_seq`, `first_head_us` | c의 값 | 그대로 | 그대로 |
| `last_seq`, `last_head_us` | c의 값 | 그대로 | c의 값 |
| `n_chunks` | 1 | + 1 | + 1 |
| `n_missing_chunks` | 0 | − 1 | + (c.seq − 옛 last_seq − 1) |
| `n_dropped_frames` | c의 `dropped_range + dropped_imu_frames` | + 같은 값 | + 같은 값 |
| `geometry_hash` | c의 값 | 그대로 | 그대로(같아야 더해짐) |
| `installation_id` | `(device_id, geometry_hash)`의 installation이 있으면 그 id, 없으면 NULL | 그대로 | 그대로 |
| `utc_offset_us`, `time_source`, `start_at`, `end_at` | SG-7 | SG-7(바뀔 수 있음) | SG-7 |
| `revision` | 0 | + 1 | + 1 |
| `last_chunk_at` | c.received_at | c.received_at | c.received_at |
| `state`, `closed_reason` | `open`, NULL | `open`, NULL(닫혀 있었으면 다시 엶) | 같음 |
| `segment_tag`, `sides` | SG-6 | SG-6 | SG-6 |
- **SG-4 나눔 조건**(P 뒤에 올 때, 위에서부터 먼저 맞는 것이 이유):
  1. `gap_60s`: `c.head_us_start − P.last_head_us − 1초 ≥ SEGMENT_GAP_S`, 또는 Head 시각이 거꾸로 감(`c.head_us_start ≤ P.last_head_us`, 경고 로그).
  2. `geometry_changed`: `c.geometry_hash ≠ P.geometry_hash`.
  3. `tag_changed`: c에 IMU 프레임이 있는 발 자리 중, P의 `segment_tag`에 MAC이 있고 `(tag_mac, tag_boot_id)`가 다른 것이 있음(Tag 교체·재부팅). 새 발 자리가 나타나는 것은 나눔 조건이 아닙니다.
  4. `max_length`: `c.head_us_start − P.first_head_us ≥ SEGMENT_MAX_S`.
- **SG-5 닫기.**
  - `silence_60s`: 매 루프, 열린 구간 중 `last_chunk_at < now − SEGMENT_SILENCE_S`인 것을 닫습니다.
  - `boot_changed`: c가 **그 부팅의 첫 구간**을 만들었을 때(같은 `(device_id, head_boot_id)`의 구간이 그때까지 없었음)만, 같은 장치의 열린 구간 중 부팅이 c와 다른 것을 닫습니다. 그래서 옛 부팅의 묶음이 늦게 와 옛 구간이 다시 열려도 새 부팅의 구간을 닫지 않고, 다시 열린 옛 구간은 침묵 60초로 닫힙니다. 프레임 없는 묶음(SG-2)은 이 규칙을 일으키지 않습니다.
  - SG-4의 이유로 닫힘.
  - 닫을 때마다 그 구간의 `analysis` 작업을 넣습니다(PL-2의 중복 방지). 인터넷이 60초 넘게 끊긴 뒤 밀린 묶음이 오면 구간이 다시 열리고, 다시 닫힐 때 재분석됩니다.
- **SG-6 Tag와 발.** 묶음의 `tag_mask`에 있는 발 자리(bit0 = `right`, bit1 = `left`)마다 `segment_tag` 행이 없으면 넣습니다(`first_seen_at = c.received_at`). MAC은 `intake.chunk.tag0_mac`/`tag1_mac`에서 오고, 거리 프레임만 있던 발은 NULL로 넣었다가 그 발의 IMU 프레임이 처음 오면 채웁니다. 이미 MAC이 있는 행은 바꾸지 않습니다(다르면 P 뒤에서는 `tag_changed`, P 안쪽에서는 경고). `sides`는 `segment_tag` 행으로 정합니다(`left`·`right`·`both`).
- **SG-7 시각.** 구간의 UTC는 `head_us + utc_offset_us` 하나로 셉니다.
  - 구간 안에 flags bit0(UTC 추정)이 켜진 묶음이 있으면 그중 처음 들어온 묶음의 `utc_us_start − head_us_start`가 `utc_offset_us`이고 `time_source = 'server'`입니다.
  - 없으면 구간 첫 묶음의 `received_at − 1초 − head_us_start`이고 `time_source = 'arrival'`입니다. 나중에 bit0 묶음이 더해지면 그 값으로 바꾸고 `'server'`로 고칩니다.
  - `start_at = first_head_us + offset`, `end_at = last_head_us + offset + 1초`.
- **SG-8 앵커 배치.** 처리한 묶음에 system 블록이 있으면(프레임 없는 묶음도) `(device_id, geometry_hash)`의 `segment.installation`을 넣거나 `last_seen_at`을 고칩니다. 시각은 묶음의 `received_at`입니다. 좌표는 블록의 `anchors`, `surveyed`는 flags bit0, `source`는 `geometry_source`입니다. 같은 장치·같은 hash로 `installation_id`가 비어 있는 구간이 있으면 채웁니다(새 구간은 만들 때 이미 채움, SG-3 표). 같은 묶음 안에서는 SG-8을 SG-3보다 먼저 합니다.
- **SG-9 귀속.** 구간과 걷기 구간에는 주인 열이 없습니다. 주인은 조회 때 뷰(`analysis.segment_subject`, `analysis.bout_subject`)가 정합니다. 규칙: 그 시작 시각에 `device_id`가 같고, `wear.tag_mac`이 NULL이거나 그 구간의 Tag 중 하나이고, 기간이 덮는 `wear`의 착용자입니다. 둘이면 `tag_mac`을 적은 쪽, 그래도 둘이면 주인 없음 + `ambiguous`.
- **SG-10 결정성.** 같은 묶음들을 `id` 순으로 처리하면 언제 돌리든 같은 구간이 나옵니다. 처리 순서가 seq 순서와 크게 다를 때(13절의 예외)만 나눔이 달라질 수 있습니다.

### 6.5 분석 호출 (AN)

- **AN-1 작업.** `analysis {segment_id}` 작업 하나가 run 하나(또는 0개)를 만듭니다.
- **AN-2 열린 구간.** 작업을 시작할 때 구간이 열려 있으면 run 없이 `done`입니다. 닫힐 때 새 작업이 들어옵니다. 이 판단과 입력 고르기(AN-4)는 구간 행을 잠근(`FOR UPDATE`) 짧은 트랜잭션 안에서 합니다. 구간을 닫는 segment 트랜잭션과 차례가 정해지므로, "열려 있어 끝냄"과 "닫으며 작업 넣기(실행 중인 작업이 있어 넣지 못함)"가 엇갈려 작업 없이 닫힌 구간이 남지 않습니다. C2 호출 동안에는 잠그지 않습니다.
- **AN-3 배치 기다리기.** 구간의 `installation_id`가 비어 있으면 작업을 10분 뒤로 미룹니다(`attempts`는 그대로, `last_error = 'waiting_installation'`). 작업을 만든 지 1시간이 지나면 기다리지 않고 `anchors = null`로 보냅니다.
- **AN-4 입력 고르기.**
  - 구간의 모든 묶음 행이 `intake.chunk`에 있으면 `input = 'raw'`이고 raw 파일에서 `chunk_seq` 순으로 읽습니다. 셀 때 프레임 없는 묶음(SG-2)과 segment가 아직 처리하지 않은 묶음(`id > cursor`)은 범위 안에 있어도 뺍니다(둘 다 구간의 `n_chunks`에 없음).
  - 아니면 `archive.range`가 있으면 `input = 'archive'`이고 그 구간의 archive 파일을 seq 순으로 이어 읽습니다. 범위 사이는 seq 빈자리로 보입니다.
  - 둘 다 없으면 run 없이 `done`(`last_error = 'no_input'`).
- **AN-5 문맥.** C2 `context`(7.1절)를 만듭니다.
  - `c2_version`: 1. `segment_id`, `run_id`(AN-6). `sides`: 보내는 때의 `segment.sides`.
  - `anchors`: installation이 있고 `surveyed`이면 그 좌표, 아니면 null(IMU 단독).
  - `subject`: `segment_subject`의 착용자의 키·다리 길이·마비측, 없으면 null.
  - `params`: `ANALYSIS_PARAMS`.
  - `origin_head_us`: 보내는 첫 묶음의 `head_us_start`. `origin_at = origin_head_us + utc_offset_us`.
- **AN-6 run 번호를 먼저 받음.** 파일 이름에 쓰려고 호출 전에 `nextval`로 run id를 받습니다. 실패한 시도의 번호는 버립니다.
- **AN-7 응답 처리.**

| C2 응답 | 처리 |
|---|---|
| 200 | 7.2절 검사 → 결과 원문과 궤적 파일 쓰기(임시 이름 → fsync → 이름 바꾸기) → 한 트랜잭션: run(`done`, `result_status` = 응답의 `status`)·bout·trajectory INSERT(COPY), 작업 `done`, `archive {run_id}` 작업 넣기, 보낸 뒤 구간 `revision`이 바뀌었으면 `analysis` 작업 다시 넣기 → 커밋 뒤 같은 구간의 옛 run 궤적 파일과 행 삭제 |
| 200인데 검사 실패 | 작업 `failed`(재시도 안 함), `last_error`에 어느 검사인지. 쓴 파일은 지움 |
| 422 | 한 트랜잭션: run(`skipped`, `skip_reason` = 응답의 이유), 작업 `done`, `archive {run_id}` 작업 넣기 |
| 400 | 작업 `failed`(재시도 안 함). 서버가 보낸 바이트가 C1을 어긴 것이므로 버그 |
| 5xx, 시간 초과, 연결 실패 | 재시도(PL-2). 쓴 파일은 지움 |

응답 `status`가 `partial`이어도(gaitfusion이 마감 시각에 끊겨 어떤 발의 궤적을 뺌) 처리는 200과 같습니다. run은 `done`이고 현재 run이 되며, 자동으로 다시 분석하지 않습니다. 앱은 `result_status`로 압니다(8절).

- **AN-8 현재 run.** `analysis.current_run` 뷰 = 구간마다 id가 가장 큰 run. `is_current` 같은 열은 두지 않습니다.
- **AN-9 재분석.** api의 재분석 요청은 `analysis {segment_id}` 작업을 넣을 뿐입니다. 새 run이 생기고 옛 run의 걷기 구간 행은 비교용으로 남으며, 궤적만 지웁니다.
- **AN-10 응답 원문.** `result` 파트를 gzip해 결과 원문 파일로 두고, 압축 전 바이트의 sha256과 크기를 run에 적습니다. 1단계가 해석하지 않는 필드(보폭, 창 등)는 여기에만 있습니다.

### 6.6 원본 보관 (AR)

- **AR-1 언제.** `archive {run_id}` 작업. run이 끝날 때(`done`·`skipped` 모두) 들어옵니다. 현재 run이든 아니든 모든 run의 걷기 구간이 대상이고, 보관 범위는 늘어나기만 합니다.
- **AR-2 범위.** 걷기 구간마다 시간 범위 `[start_at − ARCHIVE_PAD_S, end_at + ARCHIVE_PAD_S]`를 잡습니다. 그 구간의 `intake.chunk` 행 중 묶음 시간 `[head_us_start + utc_offset_us, 그 + 1초)`가 이 범위와 겹치는 것들의 최소·최대 seq가 seq 범위입니다(시간을 seq로 바꿀 때는 이 규칙 하나만 씀). 겹치거나 맞닿는(`다음 first_seq = last_seq + 1`) 범위는 합칩니다. 15초인 이유: 자세 필터 예열과 UWB 시계 맞춤에 앞 문맥이 필요하고, 나중에 걷기 기준을 느슨하게 바꿔도 걷기 사이가 메워집니다. 걷기 구간 하나에 약 144 kB가 더해집니다.
- **AR-3 겹침 없음.** 이미 `archive.range`가 덮는 seq와 raw에서 이미 지워진 seq는 빼고, 남은 연속 범위마다 파일 하나를 만듭니다. DB 제약(`EXCLUDE`)이 겹침을 막습니다. 원래 없던 seq(빈자리)는 파일에도 없습니다.
- **AR-4 파일.** raw에서 묶음을 seq 순으로 읽어 5.2절 형식으로 쓰고, sha256을 `archive.range`에 적습니다.
- **AR-5 끝 표시.** run마다 `archive.cut` 행을 남깁니다(걷기가 없어도 `n_ranges = 0`). 30일 삭제 조건의 근거입니다.
- **AR-6 30일 삭제 조건.** raw 파일 하나(수신 hour가 `RAW_RETENTION_DAYS`보다 이전)는 그 안의 모든 묶음이 다음을 만족할 때만 지웁니다.
  - 프레임 없는 묶음(SG-2): `id ≤ cursor`이면 됩니다.
  - 프레임 있는 묶음: `id ≤ cursor`이고, 속한 구간이 `closed`이고, 그 구간에 run이 하나 이상 있고, 그 구간의 모든 run에 `archive.cut`이 있고, 그 구간의 `analysis` 작업과 그 run들의 `archive` 작업이 `queued`·`running`이 아니고, 그 구간의 가장 최근(id가 가장 큰) `analysis` 작업이 `failed`가 아니고, 가장 최근 run의 `segment_revision`이 지금 구간 `revision`과 같고(그 뒤에 더해진 묶음을 아무 run도 못 본 경우를 막음), 그 구간의 `archive.range`마다 범위 안 `intake.chunk` 행 수가 범위의 `n_chunks`를 넘지 않음(보관한 범위의 빈자리를 늦은 묶음이 메운 경우를 막음, 15절 정할 것 6).
  - "그 안의 모든 묶음"은 파일을 읽어 얻은 **레코드**로 셉니다. 레코드의 키에 `intake.chunk` 행이 아예 없으면(DB만 재시작해 받기 커밋을 잃은 경우) 지우지 않고 오류 로그를 남깁니다. 검사부터 행 삭제까지 PL-3의 전역 잠금을 쥡니다(그 사이에 재색인이 넣은 행을 확인 없이 지우지 않게).
  - 프레임 있는 묶음이 어느 구간에도 속하지 않으면 파일을 지우지 않고 오류 로그를 남깁니다(버그 신호).
  - 그래서 분석이 실패한 채로 남은 구간의 원본은 지우지 않습니다. 관리자가 재분석해 성공하면 그다음 날 조건을 만족합니다.
  - 지우는 순서: `intake.health`·`intake.system` 행 → `intake.chunk` 행과 `archive.purged` 행(같은 트랜잭션) → 파일. `archive.purged`는 무엇을 언제 지웠는지의 영구 기록이고, 파일을 지우기 전에 멈췄으면 다음 날 이 행을 보고 파일을 마저 지웁니다. `intake.survey`는 지우지 않습니다.
  - rejected 파일과 `intake.rejected` 행은 30일이 지나면 조건 없이 지웁니다.
- **AR-7 삭제 스위치.** `PURGE_ENABLED = false`(기본)이면 purge는 아무것도 지우지 않고 지울 파일과 행 수만 로그에 적습니다. 켜는 조건: 복구 연습(OP-3)을 한 번 마쳤고, gaitfusion의 걷기 구간 찾기를 실제 기록으로 확인했음.
- **AR-8 30일 뒤 재분석.** 구간의 raw가 지워진 뒤의 재분석은 AN-4에 따라 archive를 씁니다. 30일 안의 재분석이 옛 run이 못 본 걷기를 찾으면 archive가 그만큼 늘어납니다.

### 6.7 앱 API (AP)

- **AP-1** 8절의 엔드포인트를 FastAPI로 만들고 OpenAPI를 냅니다. 앱 팀은 그것으로 클라이언트를 만듭니다.
- **AP-2 권한.** `member`는 자기 착용자와, 그 착용 기간에 귀속된 구간·걷기 구간만 봅니다. `admin`은 전부입니다. 남의 것을 요청하면 404입니다(있는지 알리지 않음). admin 전용 엔드포인트를 `member`가 부르면 403입니다. 장치는 자기 착용자가 찬 적 있는 것만 보이고, 그 장치의 health·system 블록도 자기 착용 기간 안의 것만 보입니다(같은 장치를 다른 사람이 찬 동안의 기록은 보이지 않음).
- **AP-3 상태 블록 풀기.** health·system·survey 조회는 `block` 바이트를 `pipeline/wire.py`로 풀어, wire.md "묶음 `.json`의 형식"의 `payload`와 같은 이름·모양의 JSON으로 줍니다(예: system `anchors`는 칸마다 `[x, y, z, range_bias_m]`, 빈 MAC은 `"00:00:00:00:00:00"`). 펌웨어 시험 벡터의 `.json`과 같은 형식이므로 계약 시험이 이것도 확인합니다.
- **AP-4 궤적 읽기.** TRJ1 파일의 머리를 읽고, 채널마다 시작 위치 `24 + 4n·k`에서 `rate`에 맞게 건너뛰며 읽습니다(25 Hz = 4개마다 1개). 해석이나 압축 해제는 하지 않습니다. 캐시가 지워졌으면 404입니다.
- **AP-5 쓰기 뒤 작업.** api가 넣는 작업은 재분석(`analysis`) 하나입니다. 착용 기록을 고쳐도 1단계에서는 작업을 넣지 않습니다(귀속은 조회 때 정해지므로). 2단계에서는 일별 값을 다시 계산하는 `summary` 작업을 넣습니다(SM-1).

### 6.8 도구 (TL)

- **TL-1 `fake_head.py`.** 합성 보행으로 묶음을 만들어(서버의 `pipeline/wire.py`가 아닌 별도의 인코더. 시험 벡터와 바이트가 같아야 함) intake에 보냅니다. 시나리오: 보통, 1-Tag·2-Tag, 인터넷 끊김(버퍼에 쌓았다 몰아 보냄), 버퍼 넘침(빈자리), 같은 요청 다시 보내기, 재부팅, Tag 교체, 배치 변경, T1이 중간에 나타남, Tag 꺼짐(프레임 없는 묶음만), 잘못된 묶음 각 종류. 장치 여러 대를 동시에 흉내 낼 수 있습니다.
- **TL-2 `fake_gaitfusion.py`.** C2를 지키는 가짜 서비스입니다. 받은 묶음을 해독해, IMU 프레임이 10초 이상 이어진 부분마다 걷기 구간 하나와 발마다 TRJ1 궤적 하나를 만듭니다(값은 결정적인 합성값, CRC는 맞음). 환경 변수로 422, 500, 느린 응답, 형식이 틀린 응답을 낼 수 있습니다.
- **TL-3 `reindex_raw.py [--since 시각] [--device]`.** raw 파일 → `intake.*` 행. 키가 이미 있으면 건너뜁니다. IN-9가 같은 코드를 씁니다.
- **TL-4 `verify_archive.py [--reindex]`.** archive 파일의 sha256을 `archive.range`와 대조합니다. `--reindex`는 파일에서 빠진 행을 다시 만듭니다.
- **TL-5 `reingest_rejected.py`.** IN-10.
- **TL-6 `admin.py`.** `create-admin`, `register-device <mac>`(토큰 출력), `rotate-token`, `reanalyze <segment_id>`. API와 같은 store 함수를 씁니다.
- **TL-7 `import_usb.py`.** 펌웨어의 USB 레코더 기록(Head `range`·`imu_batch`·`system`, [protocol.md](https://github.com/HyperBlaze456/uwb-firmware/blob/main/docs/protocol.md))을 1초 묶음으로 바꿔 intake에 보냅니다. Head 전송부가 생기기 전에 실제 기록을 넣는 길입니다. 레코더(`../uwb-firmware/scripts/record_usb.py`)는 줄마다 앞에 PC 단조 시각 µs와 TAB을 붙이며, 맨 JSON 줄도 받습니다. 묶음 나누기는 Head 규칙과 같습니다: 프레임도 블록도 없는 초는 묶음을 만들지 않고 seq도 쓰지 않음, 배치(`geometry_id`)가 바뀌면 그 이벤트에서 닫음, system 블록은 부팅 안 첫 번·내용이 바뀔 때·10분마다. 시각은 `--utc-start`(기록 첫 줄의 UTC, 시간대 필수)와 줄 앞 PC 시각으로 맞춥니다(부팅마다 PC 시각 − `head_rx_us`의 최솟값). 레코더가 포트를 열 때 보드 버퍼에 남아 있던 옛 줄이 있어도 시각이 밀리지 않습니다. 예전 펌웨어의 `system`(`device_id`·`set_members`가 없음)도 받고, 이때 `set_members`는 0입니다. health·survey 블록은 만들지 않습니다. 같은 파일과 같은 `--utc-start`는 같은 바이트가 되어, 다시 보내면 `duplicate`입니다. 보내기 전에 묶음마다 `pipeline/wire.py`의 검사기를 돌리고, `--dry-run`은 거기까지만 합니다. 자세한 규칙은 도구의 docstring에 있습니다.
- **TL-8 `bench.py`.** 9절의 속도를 재는 부하 도구입니다. 장치 N대로 초당 1요청씩 보내고, PL-5 로그에서 p95를 셉니다.

도구의 DB 역할: `reindex_raw.py`·`reingest_rejected.py`는 `intake_rw`, `verify_archive.py`는 `core_rw`(`--reindex`의 INSERT 포함), `admin.py`는 `api_rw`입니다. `fake_head.py`·`fake_gaitfusion.py`·`import_usb.py`·`bench.py`는 DB에 접속하지 않고 HTTP만 씁니다. 접속 정보는 4.3절의 `DB_*`입니다.

### 6.9 운영 (OP)

- **OP-1 백업.** `ops/backup.sh`와 `ops/crontab`:

| 대상 | 방법 | 주기 | 원격 보관 |
|---|---|---|---|
| raw 파일 | `rclone copy` | 10분 | 35일 |
| archive 파일 | `rclone copy`(삭제를 옮기는 `sync` 금지) | 10분 | 영구. 원격 버전 보존 켬 |
| DB | `pg_dump -Fc` → 원격 | 매일 | 14일. 매월 1일 덤프는 지우지 않음 |
| 결과 원문·궤적·rejected | 안 함 | — | — |

- **OP-2 알림.** 백업이 실패하면 알림을 보냅니다(`ALERT_WEBHOOK_URL`이 있으면 그곳에 POST, 없으면 cron 메일). 백업이 멈춘 채 30일이 지나면 원격에 없는 원본이 지워질 수 있기 때문입니다. 받기의 503이 1분에 10번을 넘어도 알립니다.
- **OP-3 복구 연습.** 분기마다 빈 compose에 덤프를 넣고, archive 일부를 받아 `verify_archive.py`를 돌리고, 구간 하나를 재분석합니다. 절차는 `ops/restore.md`입니다. 매주 `verify_archive.py`를 돌립니다.
- **OP-4 디스크가 죽으면.** 잃는 것은 최근 10분 이하의 raw(Head는 확인 뒤 지웠으므로)와 마지막 덤프 뒤 24시간 이하의 DB 행입니다. `intake.*`는 raw에서(TL-3), `archive.range`는 archive에서(TL-4) 다시 만듭니다. 구간과 분석 결과는 재분석으로 되살립니다. `registry`의 하루치 입력은 사람이 다시 넣습니다.
- **OP-5 스키마 변경.** 열이나 표를 지우는 변경 전에는 DB 덤프를 따로 받아 둡니다.

## 7. 계약 C2: 서버 → gaitfusion

이 저장소가 소유하고, gaitfusion 저장소가 구현합니다. 바꿀 때는 이 절을 먼저 고치고 `c2_version`을 올립니다. 응답 `bouts[]` 안의 분석 특징 필드(보폭, 창 등)의 이름·단위·뜻은 C4 지표 규약(14.4절)이 정하고, 1단계 서버는 그 필드를 해석하지 않습니다.

### 7.1 요청: `POST /v1/analyze` (`multipart/form-data`)

| 파트(순서대로) | 형식 | 내용 |
|---|---|---|
| `context` | `application/json` | 아래 |
| `chunks` | `application/octet-stream` | C1 요청 본문과 같은 레코드 배치(`u32 len` + 묶음 반복), `chunk_seq` 오름차순. C1의 16개·128 kB 한도는 **없습니다**(그 한도는 `POST /d/v1/chunks`에만 있음). raw 입력이면 구간의 묶음 전부(1-Tag 1시간 약 17 MB), archive 입력이면 보관 범위의 묶음만 |

```json
{
  "c2_version": 1,
  "segment_id": 1234,
  "run_id": 5678,
  "origin_head_us": 81234567890,
  "sides": "right",
  "anchors": [{"id": 0, "xyz_m": [0.0, 0.0, 1.2], "bias_m": 0.0}, {"id": 1, "xyz_m": [4.1, 0.0, 1.2], "bias_m": 0.0},
              {"id": 2, "xyz_m": [3.9, 3.2, 1.2], "bias_m": 0.0}, {"id": 3, "xyz_m": [0.1, 3.1, 0.4], "bias_m": 0.0}],
  "subject": {"height_m": 1.72, "leg_length_m": 0.90, "paretic_side": null},
  "params": {"stance_threshold": null, "stance_window": null, "bout_min_strides": 4, "bout_max_pause_s": 3.0, "bout_pad_s": 2.0}
}
```

- `anchors`가 null이면 IMU 단독 모드입니다. `subject`는 null일 수 있고, 그 안의 각 값도 null일 수 있습니다.
- 발은 묶음 프레임의 `tag_id`로 서비스가 정합니다(0 = 오른쪽, 1 = 왼쪽). `sides`는 구간에 있던 발입니다.
- `params`의 null은 서비스 기본값입니다. 걷기 구간 기준은 **정지와 swing 교대 4보폭 이상, 멈춤 3초 미만**입니다. 멈춤이 3초 미만이면 한 걷기 구간으로 합치므로, 서로 다른 걷기 구간 사이는 3초 이상 떨어집니다.
- `bout_pad_s`(기본 2초)는 **궤적에만** 붙는 앞뒤 여유입니다. 응답의 `start_s`·`end_s`는 여유 없는 걷기 범위입니다.
- 시각 0은 `origin_head_us`입니다. 시각은 표본 수가 아니라 묶음 머리의 `head_us_start`로 이어야 하고, archive 입력에서는 seq 빈자리가 수 분일 수 있습니다.
- `t_last` = `(보낸 마지막 묶음의 head_us_start − origin_head_us) / 10^6 + 1`초 = 보낸 데이터가 끝나는 시각입니다. 7.2절 검사에 씁니다.

### 7.2 응답 (200, `multipart/form-data`)

요청과 같은 형식이라 같은 해독기를 씁니다. 파트마다 `Content-Disposition: form-data; name="<이름>"`과 `Content-Type`이 있습니다. 첫 파트는 이름 `result`(`application/json`)이고 궤적을 뺀 결과 전부입니다. 이어서 걷기 구간·발마다 궤적 파트 하나(`application/octet-stream`, 이름 `traj-<bout idx>-<side>`, 예 `traj-0-right`, 7.3절 TRJ1)가 옵니다. 궤적을 숫자 글자로 쓰면 1시간 구간에 약 15 MB라 JSON에 넣지 않습니다.

```json
{
  "c2_version": 1,
  "gaitfusion_version": "1.3.0+gabc1234",
  "status": "complete",
  "summary": {"n_imu": 359800, "n_range": 358100, "seq_gaps": 3, "sides": ["right"], "sensor_site": "lateral_ankle"},
  "bouts": [{
    "start_s": 812.40, "end_s": 871.15, "mode": "fused", "sides": "right",
    "quality": {"uwb_accept_ratio": 0.93},
    "trajectories": [{"side": "right", "part": "traj-0-right"}],
    "strides": [], "turns": [], "steps": [], "windows": [], "events": []
  }]
}
```

1단계 서버가 쓰고 검사하는 필드는 아래뿐입니다. 하나라도 어기면 AN-7의 "검사 실패"입니다.

| 필드 | 규칙 |
|---|---|
| `c2_version` | 1 |
| `gaitfusion_version` | 비지 않은 문자열 |
| `status` | 없어도 됨. 있으면 `complete` 또는 `partial`(`null`도 틀림). 그대로 `analysis.run.result_status`에, 없으면 NULL. `partial` = gaitfusion이 자기 마감 시각(540초)에 끊겨 어떤 발의 궤적을 뺌. 걷기 구간 목록은 온전 |
| `summary` | JSON 객체. 그대로 `analysis.run.summary`에 |
| `bouts` | 배열(비어도 됨). `start_s` 오름차순. 앞 걷기 구간의 `end_s` < 다음 `start_s` |
| `bouts[].start_s`, `end_s` | 유한한 수. 0 ≤ `start_s` < `end_s` ≤ `t_last`(7.1절) |
| `bouts[].mode` | `fused` 또는 `imu_only`. 보낸 `anchors`가 null이면 `imu_only`만 |
| `bouts[].sides` | `left`·`right`·`both`, 구간 `sides`의 부분(`both`인 구간은 셋 다 가능, `left`인 구간은 `left`만) |
| `bouts[].quality` | JSON 객체 |
| `bouts[].trajectories` | 배열. 항목의 `side`는 그 걷기 구간의 발에 들고 발마다 하나 이하. `part`는 `traj-<그 bout의 배열 순번>-<side>` |
| 궤적 파트 | 7.3절 검사를 통과. 머리의 `side`가 이름과 같음. `0 ≤ t0_s`, `t0_s + (n − 1) / rate_hz ≤ t_last`. 모든 궤적 파트는 정확히 하나의 `trajectories` 항목이 가리키고, 모든 항목의 파트가 응답에 있음 |
| 파트 | 이름이 `result`이거나 위 규칙의 궤적 이름. 같은 이름이 둘이면 안 됨 |

나머지 필드(`strides`, `turns`, `steps`, `windows`, `events`와 앞으로 더해질 것)는 있어도 없어도 되고, 1단계는 결과 원문 파일에만 남깁니다. 2단계에서 줄 단위로 저장하고 지표 규약으로 풀이합니다(14.4–14.5절).

### 7.3 궤적 파트 TRJ1 (little-endian, 채널별 배열)

| offset | 크기 | 형 | 이름 | 내용 |
|---|---|---|---|---|
| 0 | 4 | char × 4 | `magic` | `TRJ1` |
| 4 | 1 | u8 | `side` | 0 왼쪽, 1 오른쪽 |
| 5 | 1 | u8 | `n_channels` | 13 |
| 6 | 2 | u16 | `rate_hz` | 100 |
| 8 | 4 | u32 | `n` | 채널당 표본 수 |
| 12 | 4 | u32 | `reserved` | 0 |
| 16 | 8 | f64 | `t0_s` | 첫 표본 시각, 응답 시각축(`origin_head_us` = 0)의 초 |
| 24 | 4 × n × 13 | f32 | 채널 배열 | `px py pz vx vy vz roll_deg pitch_deg yaw_deg stance sig_px sig_py sig_pz` 순서로 채널마다 `n`개씩. `stance`는 0/1. 계산 못 한 표본은 NaN |
| 24 + 52n | 4 | u32 | `crc32` | 앞 전체의 CRC-32(wire와 같은 다항식) |

core는 `magic`, `n_channels` = 13, `rate_hz` = 100, `reserved` = 0, `n` ≥ 1, 길이 = `28 + 52n`, CRC, 그리고 7.2절의 시각 범위만 확인하고 파일로 그대로 씁니다. 궤적은 걷기 구간 앞뒤로 `bout_pad_s`(2초)를 포함하되 `[0, t_last]` 밖으로는 나가지 않습니다. 이웃한 걷기 구간의 궤적끼리는 여유분이 겹칠 수 있습니다. 걷기 1분이 약 0.3 MB입니다.

### 7.4 오류와 그 밖

| 코드 | 뜻 | 본문 | core |
|---|---|---|---|
| 400 | 요청이 C1이나 이 절을 어김 | `{"reason": "...", "chunk_index": n}` | 작업 `failed` |
| 422 | 분석 불가(표본 부족, IMU 없음 등) | `{"reason": "..."}` | run `skipped` |
| 5xx, `GAITFUSION_TIMEOUT_S` 초과 | 서비스 문제 | — | 재시도 |

`GET /healthz`, `GET /v1/version` → `{"gaitfusion_version": "...", "c2_version": 1}`. core는 `GET /v1/version`이 한 번 성공해 `c2_version` = 1을 확인하기 전에는 analysis 작업을 가져가지 않습니다. 연결이 안 되면 매 루프 다시 묻고, 값이 1이 아니면 오류 로그를 남기고 계속 가져가지 않습니다. 확인한 뒤에는 다시 묻지 않습니다(호출 실패는 PL-2의 재시도).

## 8. 계약 C3: 앱 API (1단계)

REST + JSON, 경로 `/v1/`. 시각은 UTC ISO 8601. 인증은 `Authorization: Bearer <로그인 토큰>`. 목록은 `?limit=`(기본 100, 최대 1000)과 `?before_id=`로 나눕니다. 권한 "본인"은 AP-2의 뜻입니다.

| 메서드와 경로 | 내용 | 권한 | 폴더 |
|---|---|---|---|
| `POST /v1/auth/token` · `DELETE /v1/auth/token` | 로그인(`{email, password}` → `{token, expires_at}`) · 로그아웃 | 누구나 · 로그인 | registry |
| `GET /v1/me` | 내 계정과 착용자 | 로그인 | registry |
| `POST /v1/users` | 계정 만들기(`{email, password, role, subject_id}`) | admin | registry |
| `POST /v1/subjects` · `PATCH /v1/subjects/{id}` · `GET /v1/subjects/{id}` | 착용자 만들기 · 키, 다리 길이, 마비측 고치기 · 보기 | admin · admin · 본인, admin | registry |
| `POST /v1/wear` · `PATCH /v1/wear/{id}` · `GET /v1/subjects/{id}/wear` | 착용 시작(`{subject_id, device_id, tag_mac?, started_at}`) · 시작·끝 고치기 · 목록 | admin · admin · 본인, admin | registry |
| `POST /v1/devices` | 장치 등록(`{mac, note?}` → `{id, token}`, 토큰은 이때만) | admin | registry |
| `POST /v1/devices/{id}/token` · `DELETE /v1/devices/{id}/token` | 토큰 재발급(→ `{token}`) · 폐기 | admin | registry |
| `GET /v1/devices` · `GET /v1/devices/{id}` | 장치 목록 · 최신 system·health 블록을 풀어 함께 | 로그인(본인 장치만) · 본인, admin | registry |
| `GET /v1/devices/{id}/health?from=&to=` | 1분 기록(최근 30일), 풀어서 | 본인, admin | intake |
| `GET /v1/devices/{id}/surveys` | survey 블록 목록, 풀어서 | admin | intake |
| `GET /v1/devices/{id}/rejected?from=&to=` | 거절한 묶음: 코드, 검사 단계, `detail` | admin | intake |
| `GET /v1/segments?device=&from=&to=` · `GET /v1/segments/{id}` | 구간 목록 · 구간 상세(Tag, 배치, 빈자리, run 목록. run마다 `result_status`) | admin | segment |
| `POST /v1/segments/{id}/reanalyze` | 재분석 작업 넣기 → 202 `{segment_id, queued}`(이미 대기 중이면 `queued = false`). raw도 archive도 없으면 409 | admin | segment |
| `GET /v1/runs/{id}` · `GET /v1/runs/{id}/result` | run(`result_status` 포함)과 걷기 구간 목록 · 결과 원문(gzip 그대로, `Content-Encoding: gzip`) | admin | analysis |
| `GET /v1/subjects/{id}/bouts?from=&to=` | 현재 run의 걷기 구간 목록(시작, 끝, 발, 모드, 그 run의 `result_status`). `partial`이면 어떤 걷기 구간은 궤적이 없는 발이 있을 수 있음(궤적 조회 404). 귀속은 SG-9 | 본인, admin | analysis |
| `GET /v1/bouts/{id}/trajectory?side=&rate=25` | 한 발 또는 있는 발 전부의 궤적. `rate` 25(기본)·50·100. 응답은 `{bout_id, trajectories: [{side, rate_hz, t0, n, channels: {px: [...], ...}}]}`(발이 하나여도 배열). 계산 못 한 표본(NaN)은 `null` | 본인, admin | analysis |

## 9. 속도 요구

값은 권장 VM(13.2절) 기준 p95 목표이고 아직 재지 않았습니다. 네트워크와 TLS 시간은 빼고, PL-5 로그로 잽니다(TL-8). 표나 조회를 바꿀 때 오른쪽 열의 방법을 깨지 않습니다.

| 경로 | 요구 (p95) | 지키는 방법 |
|---|---|---|
| 받기 요청, 묶음 1개 | 서버 처리 2 ms 이하(`fdatasync` 제외) | 요청 하나 = 트랜잭션 하나. 표마다 다중 행 INSERT 한 번. 토큰 조회(고유 색인)와 중복 확인 SELECT 한 번 말고는 표를 읽지 않음. Tag 자리는 고정 열 두 칸이라 Tag가 둘이어도 행 수가 같음. `intake.chunk` 색인은 중복 키·장치 시각·파일 경로 셋뿐. 커밋이 디스크를 기다리지 않음 |
| 받기 요청, 묶음 16개(밀린 것) | 서버 처리 10 ms 이하(`fdatasync` 제외) | 같음 |
| 받기 DB 시간 | 묶음당 0.5 ms 이하 | 같음 |
| `fdatasync` | 재서 기록. 20 ms를 넘으면 디스크를 바꿈 | 요청당 파일마다 한 번 |
| 구간 잇기 (core segment) | 묶음당 0.5 ms 이하 | 구간을 `(device_id, head_boot_id, first_seq)` 색인으로 찾아 한 행 UPDATE. `segment_tag`는 발이 처음 보이거나 MAC이 채워질 때만. 읽기 위치는 묶음 여러 개를 처리한 뒤 한 번 |
| 결과 쓰기 (core analysis) | run 하나(1시간 구간)의 DB 트랜잭션 50 ms 이하 | 걷기 구간·궤적 행은 COPY. 결과 표에 트리거 없음. 궤적과 결과 원문은 해석하지 않고 파일로 씀(파일 쓰기 시간은 따로 기록) |
| 앱 요청 (api) | 요청 하나 20 ms 이하, 착용자 1년치 기준(궤적 제외) | 조회 키가 기본 키나 색인의 앞부분. `bout_subject`·`segment_subject`는 id나 기간으로 좁힌 뒤에만 씀 |
| 궤적 요청 | 걷기 1분, 25 Hz에 50 ms 이하 | TRJ1에서 필요한 채널만 건너뛰며 읽음 |

규칙:
- 조회 조건과 정렬에 쓰는 값은 열로 둡니다. `jsonb`(`quality`, `summary`, `context`, `anchors`)는 원문 보존용이고 `WHERE`·`ORDER BY`에 쓰지 않습니다. 받는 경로(intake)에는 JSON이 없습니다.
- 새 조회를 넣을 때는 착용자 1년치 크기의 시험 데이터에서 `EXPLAIN ANALYZE`로 색인을 타는지 확인합니다. 순차 스캔이면 색인을 먼저 추가하는 마이그레이션을 넣습니다.

## 10. DB 스키마 (1단계)

`migrations/001–008`로 그대로 옮깁니다. 파일 하나 = 단계 하나 = 스키마 하나입니다. 이 DDL은 PostgreSQL 17에 오류 없이 적용됨을 확인했습니다.

공통 규칙:
- **단위**: SI. 열 이름 끝에 단위(`_m`, `_s`, `_us`, `_ms`, `_hz`). 접미가 없는 비율은 0–1.
- **NULL은 "계산 못 함" 또는 "아직 모름" 하나의 뜻**입니다. 0으로 대신 쓰지 않습니다. 데이터가 아예 없는 것은 행이 없는 것으로 나타냅니다. 예외: `intake.chunk`의 머리 복사 열(`utc_us_start` 등)은 wire 값을 그대로 담으므로 wire의 뜻(예 `0` = 추정 없음)을 따릅니다. 블록에서 뽑은 조회 열은 이 규칙을 따릅니다(예 `range_age_ms`의 wire 0xFFFF → NULL).
- **발(`side`)**: `'left'`·`'right'`. 묶음 프레임의 `tag_id` 0 = 오른쪽(T0), 1 = 왼쪽(T1)이 정하고 사람이 입력하지 않습니다. 1-Tag 세트는 T0를 오른쪽 발목에 찹니다.
- **선택적인 것은 행으로**: 발마다 생기는 것은 `side` 열이 있는 행이고 찬 발의 행만 생깁니다. 한 표에 `left_*`·`right_*` 열을 두지 않습니다. 예외는 `intake.chunk`의 Tag 자리 두 칸뿐입니다(받기를 행 하나로 끝내려고).
- **`sides`**: `'left'`·`'right'`·`'both'`. 행에서 다시 셀 수 있지만 조회 조건에 자주 쓰므로 열로 둡니다.
- **시각**: DB는 `timestamptz`(UTC). 묶음의 µs 정수(`head_us_start`, `utc_us_start`)는 `bigint` 그대로.
- **부호 없는 정수**: wire의 `u32`는 `bigint`, `u16`은 `integer`에 담습니다.
- **열거형**: `text` + `CHECK`. `ENUM` 형은 쓰지 않습니다.
- **기본 키**: `bigserial`. 앱 API에 그대로 노출합니다.
- **등급**: 표마다 주석 첫 줄에 적습니다(5.1절).
- **결과 표는 추가만**: `analysis` 표에는 UPDATE·DELETE 권한이 아무에게도 없습니다(궤적 행 삭제만 예외). 그래서 FK에 `ON DELETE CASCADE`를 두지 않습니다.

### 001_pipeline.sql

```sql
CREATE SCHEMA IF NOT EXISTS pipeline;   -- migrate가 기록 표를 위해 먼저 만들었을 수 있음

-- 등급: 작업 기록. core가 가져가 끝냄. core·api가 넣음. 대상은 종류마다 열 하나(jsonb를 WHERE에 쓰지 않으려고).
CREATE TABLE pipeline.job (
  id          bigserial   PRIMARY KEY,
  kind        text        NOT NULL CHECK (kind IN ('analysis','archive')),
  segment_id  bigint,                           -- analysis 대상
  run_id      bigint,                           -- archive 대상
  state       text        NOT NULL DEFAULT 'queued'
                          CHECK (state IN ('queued','running','done','failed')),
  attempts    integer     NOT NULL DEFAULT 0,
  last_error  text,
  run_after   timestamptz NOT NULL DEFAULT now(),
  locked_by   text,
  locked_at   timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  CHECK ((kind = 'analysis') = (segment_id IS NOT NULL)),
  CHECK ((kind = 'archive')  = (run_id IS NOT NULL))
);
CREATE INDEX job_claim ON pipeline.job (run_after) WHERE state = 'queued';
-- 같은 대상의 대기·실행 작업은 하나(PL-2). 넣기는 ON CONFLICT DO NOTHING.
CREATE UNIQUE INDEX job_one_analysis ON pipeline.job (segment_id) WHERE kind = 'analysis' AND state IN ('queued','running');
CREATE UNIQUE INDEX job_one_archive  ON pipeline.job (run_id)     WHERE kind = 'archive'  AND state IN ('queued','running');
CREATE INDEX job_done ON pipeline.job (finished_at) WHERE state = 'done';   -- 30일 정리

-- 등급: 작업 기록. segment가 intake.chunk를 어디까지 처리했나. 상태 블록은 묶음과 함께 오므로 읽기 위치가 하나.
CREATE TABLE pipeline.cursor (
  name    text   PRIMARY KEY CHECK (name IN ('chunk')),
  last_id bigint NOT NULL DEFAULT 0
);
INSERT INTO pipeline.cursor VALUES ('chunk', 0);
```

### 002_registry.sql

```sql
CREATE SCHEMA registry;

-- 등급: 영구 원본(사람 입력). 장치 하나 = 행 하나. 등록과 토큰을 함께.
CREATE TABLE registry.device (
  id              text        PRIMARY KEY,      -- 'head-e072a1aca858'
  mac             macaddr     NOT NULL UNIQUE,  -- system 블록 head_mac과 대조(wire 검사 9)
  token_hash      bytea       UNIQUE,           -- sha256(토큰). NULL = 폐기됨
  token_issued_at timestamptz,
  registered_at   timestamptz NOT NULL DEFAULT now(),
  note            text,
  CHECK (id = 'head-' || replace(mac::text, ':', ''))
);

-- 등급: 영구 원본(사람 입력). 착용 발은 Tag 번호가 정하므로 여기 없음.
CREATE TABLE registry.subject (
  id           bigserial PRIMARY KEY,
  code         text      NOT NULL UNIQUE,       -- 표시명. 실명 아님
  height_m     real,
  leg_length_m real,
  paretic_side text      CHECK (paretic_side IN ('left','right'))   -- 마비측. NULL = 해당 없음/모름
);

-- 등급: 영구 원본(사람 입력). 착용 기간. 구간·걷기 구간의 주인은 이 표로 조회 때 정함(SG-9).
-- 세트 하나(Tag 1–2개)는 한 번에 한 사람이 참.
CREATE TABLE registry.wear (
  id         bigserial   PRIMARY KEY,
  subject_id bigint      NOT NULL REFERENCES registry.subject(id),
  device_id  text        NOT NULL REFERENCES registry.device(id),
  tag_mac    macaddr,                           -- NULL = 그 Head의 Tag 전부. 값 = 구간에 이 Tag가 있을 때만
  started_at timestamptz NOT NULL,
  ended_at   timestamptz,                       -- NULL = 착용 중
  CHECK (ended_at IS NULL OR ended_at > started_at)
);
CREATE INDEX wear_lookup ON registry.wear (device_id, started_at);

-- 등급: 영구 원본(사람 입력).
CREATE TABLE registry.app_user (
  id            bigserial   PRIMARY KEY,
  email         text        NOT NULL UNIQUE,
  password_hash text        NOT NULL,           -- argon2id
  role          text        NOT NULL CHECK (role IN ('member','admin')),
  subject_id    bigint      REFERENCES registry.subject(id),   -- member의 본인 착용자
  created_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (role = 'admin' OR subject_id IS NOT NULL)
);

-- 등급: 작업 기록. 로그인 토큰. 만료된 것은 지워도 됨.
CREATE TABLE registry.app_token (
  token_hash bytea       PRIMARY KEY,           -- sha256(토큰)
  user_id    bigint      NOT NULL REFERENCES registry.app_user(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
```

### 003_intake.sql

```sql
CREATE SCHEMA intake;

-- 등급: 임시 원본(30일). 받은 묶음 하나 = 행 하나. 바이트는 raw 파일에, 여기는 머리 복사 + 파일 위치.
-- 파일에서 다시 만들 수 있음(TL-3).
CREATE TABLE intake.chunk (
  id                 bigserial   PRIMARY KEY,   -- segment의 처리 순서(cursor)
  device_id          text        NOT NULL REFERENCES registry.device(id),
  head_boot_id       bigint      NOT NULL,      -- wire u32
  chunk_seq          bigint      NOT NULL,      -- wire u32
  received_at        timestamptz NOT NULL,      -- raw 레코드 머리의 received_utc_us와 같은 값
  head_us_start      bigint      NOT NULL,      -- Head 단조 µs
  utc_us_start       bigint      NOT NULL,      -- 0 = 추정 없음 (flags bit0 = 0)
  geometry_hash      bigint      NOT NULL,
  flags              integer     NOT NULL,
  n_range            integer     NOT NULL,
  n_imu_frames       integer     NOT NULL,
  dropped_range      integer     NOT NULL,
  dropped_imu_frames integer     NOT NULL,
  status_mask        smallint    NOT NULL,      -- 묶음 안 상태 블록. bit0 system, bit1 health, bit2 survey
  -- Tag 자리 두 칸(펌웨어 한계 = 세트당 2). 받기를 행 하나로 끝내려고 둔 고정 열(공통 규칙의 예외).
  tag_mask           smallint    NOT NULL,      -- wire 머리 tag_mask. bit0 T0(오른쪽), bit1 T1(왼쪽) 프레임 있음
  tag0_mac           macaddr,                   -- 묶음 안 T0의 첫 IMU 프레임. IMU 프레임이 없으면 NULL
  tag0_boot_id       bigint,
  tag1_mac           macaddr,                   -- T1의 첫 IMU 프레임
  tag1_boot_id       bigint,
  raw_path           text        NOT NULL,      -- '/data/raw/<device>/<boot>/<YYYY-MM-DDTHH>.bin'
  raw_offset         bigint      NOT NULL,      -- 레코드 머리 12 B 뒤, 묶음 첫 바이트
  raw_len            integer     NOT NULL,
  UNIQUE (device_id, head_boot_id, chunk_seq)   -- 중복 제거 키
);
CREATE INDEX chunk_device_received ON intake.chunk (device_id, received_at);
CREATE INDEX chunk_path ON intake.chunk (raw_path);              -- 30일 삭제

-- 묶음 안 상태 블록(wire.md "상태 블록"). intake가 끝까지 해독해 검사한 뒤 payload 바이트를 그대로 두고,
-- 조회에 쓰는 값만 열로 뺌. 나머지 필드는 읽는 쪽(api, segment)이 pipeline/wire.py로 풂.
-- 키는 묶음 키. 30일 뒤 intake.chunk가 지워져도 survey는 남아야 하므로 FK 대신 같은 키를 둠.
-- at = 묶음 시각(flags bit0이면 utc_us_start, 아니면 수신 − 1초) + 블록 at_offset_us.

-- 등급: 임시 원본(30일). health 블록(1분마다).
CREATE TABLE intake.health (
  device_id    text        NOT NULL REFERENCES registry.device(id),
  head_boot_id bigint      NOT NULL,
  chunk_seq    bigint      NOT NULL,
  at           timestamptz NOT NULL,
  flags        integer     NOT NULL,            -- wire health flags
  buffer_used  integer     NOT NULL,            -- Head 버퍼에 쌓인 묶음 수
  range_age_ms integer,                         -- NULL = 부팅 뒤 거리 없음(wire 0xFFFF)
  imu_age_ms   integer,                         -- NULL = 부팅 뒤 IMU 없음
  block        bytea       NOT NULL CHECK (length(block) = 192),
  PRIMARY KEY (device_id, head_boot_id, chunk_seq)
);
CREATE INDEX health_device_time ON intake.health (device_id, at DESC);

-- 등급: 임시 원본(30일). system 블록(부팅, 설정이 바뀔 때, 10분마다). segment가 installation을 채움(SG-8).
CREATE TABLE intake.system (
  device_id     text        NOT NULL REFERENCES registry.device(id),
  head_boot_id  bigint      NOT NULL,
  chunk_seq     bigint      NOT NULL,
  at            timestamptz NOT NULL,
  tag_count     smallint    NOT NULL CHECK (tag_count IN (1,2)),
  geometry_hash bigint      NOT NULL,           -- 같은 묶음 머리의 값과 같음(wire 검사 8)
  firmware_id   bytea       NOT NULL CHECK (length(firmware_id) = 8),
  block         bytea       NOT NULL CHECK (length(block) = 152),
  PRIMARY KEY (device_id, head_boot_id, chunk_seq)
);
CREATE INDEX system_device_time ON intake.system (device_id, at DESC);

-- 등급: 영구 원본. survey 블록(배치 측량이 끝날 때). raw 파일이 지워져도 남음.
CREATE TABLE intake.survey (
  device_id    text        NOT NULL REFERENCES registry.device(id),
  head_boot_id bigint      NOT NULL,
  chunk_seq    bigint      NOT NULL,
  at           timestamptz NOT NULL,
  solved       boolean     NOT NULL,            -- status = 1
  survey_id    integer     NOT NULL,
  revision     integer     NOT NULL,
  block        bytea       NOT NULL CHECK (length(block) = 304),
  PRIMARY KEY (device_id, head_boot_id, chunk_seq)
);

-- 등급: 거절한 묶음(30일, 조건 없이 삭제). 바이트는 rejected 파일에. 분석에 쓰지 않음.
CREATE TABLE intake.rejected (
  id           bigserial   PRIMARY KEY,
  device_id    text        NOT NULL REFERENCES registry.device(id),
  received_at  timestamptz NOT NULL,
  code         smallint    NOT NULL CHECK (code BETWEEN 2 AND 9 OR code = 255),   -- wire 응답 코드. 255 = 요청 프레이밍(HTTP 400)
  check_step   smallint,                        -- wire "서버의 검사" 단계 번호
  detail       text,                            -- 걸린 필드 이름 등
  head_boot_id bigint,                          -- 머리를 읽을 수 있었으면
  chunk_seq    bigint,
  path         text        NOT NULL,            -- '/data/rejected/<device>/<YYYY-MM-DD>.bin'
  file_offset  bigint      NOT NULL,            -- 레코드 머리 16 B 뒤
  len          integer     NOT NULL
);
CREATE INDEX rejected_device_time ON intake.rejected (device_id, received_at);
```

### 004_segment.sql

```sql
CREATE SCHEMA segment;
CREATE EXTENSION IF NOT EXISTS btree_gist;       -- EXCLUDE에서 text·bigint 같음 비교용

-- 등급: 결과. 한 방의 앵커 배치. system 블록에서 채움(SG-8). 같은 hash는 같은 좌표.
CREATE TABLE segment.installation (
  id            bigserial   PRIMARY KEY,
  device_id     text        NOT NULL REFERENCES registry.device(id),
  geometry_hash bigint      NOT NULL,           -- crc32(geometry_id)
  geometry_id   text        NOT NULL,           -- 'default' | 'sv-<boot>-<survey>-<rev>' | 'manual-<boot>-<n>'
  source        text        NOT NULL CHECK (source IN ('default','survey','manual')),
  surveyed      boolean     NOT NULL,           -- system flags bit0. false면 C2에 anchors = null
  anchors       jsonb       NOT NULL,           -- [{"id":0,"xyz_m":[x,y,z],"bias_m":0.0}, …] A0–A3. C2 문맥용
  first_seen_at timestamptz NOT NULL,
  last_seen_at  timestamptz NOT NULL,
  UNIQUE (device_id, geometry_hash)
);

-- 등급: 결과. 단, 30일 뒤에는 다시 만들 수 없으므로 지우지 않음(수신 시각·빈자리 수가 여기에만 남음).
-- 한 부팅 안에서 끊김 없이 이어진 묶음들(SG-3). 묶음과는 (device_id, head_boot_id, seq 범위)로 이어짐.
CREATE TABLE segment.segment (
  id                bigserial   PRIMARY KEY,
  device_id         text        NOT NULL REFERENCES registry.device(id),
  head_boot_id      bigint      NOT NULL,
  geometry_hash     bigint      NOT NULL,
  installation_id   bigint      REFERENCES segment.installation(id),   -- NULL = 이 hash의 system 블록을 아직 못 봄
  sides             text        NOT NULL CHECK (sides IN ('left','right','both')),   -- segment_tag의 요약
  first_seq         bigint      NOT NULL,
  last_seq          bigint      NOT NULL,
  n_chunks          integer     NOT NULL,
  n_missing_chunks  integer     NOT NULL DEFAULT 0,   -- seq 빈자리
  n_dropped_frames  integer     NOT NULL DEFAULT 0,   -- 머리 dropped_* 합
  first_head_us     bigint      NOT NULL,             -- 첫 묶음의 head_us_start
  last_head_us      bigint      NOT NULL,             -- seq가 가장 큰 묶음의 head_us_start
  utc_offset_us     bigint      NOT NULL,             -- UTC µs = head_us + 이 값(SG-7)
  time_source       text        NOT NULL CHECK (time_source IN ('server','arrival')),
  start_at          timestamptz NOT NULL,
  end_at            timestamptz NOT NULL,
  state             text        NOT NULL CHECK (state IN ('open','closed')),   -- 분석 여부는 analysis.run으로 앎
  closed_reason     text        CHECK (closed_reason IN
                      ('silence_60s','gap_60s','boot_changed','tag_changed','geometry_changed','max_length')),
  revision          integer     NOT NULL DEFAULT 0,   -- 묶음이 더해질 때마다 +1. run이 본 것과 비교
  last_chunk_at     timestamptz NOT NULL,             -- 마지막으로 더한 묶음의 received_at(침묵 60초 기준)
  CHECK (last_seq >= first_seq),
  CHECK ((state = 'closed') = (closed_reason IS NOT NULL)),
  EXCLUDE USING gist (device_id WITH =, head_boot_id WITH =,
                      int8range(first_seq, last_seq, '[]') WITH &&)   -- 같은 부팅 안 seq 겹침 금지
);
CREATE INDEX segment_device_time ON segment.segment (device_id, start_at);
CREATE INDEX segment_seq ON segment.segment (device_id, head_boot_id, first_seq);
CREATE INDEX segment_open ON segment.segment (device_id) WHERE state = 'open';
CREATE INDEX segment_no_installation ON segment.segment (device_id, geometry_hash) WHERE installation_id IS NULL;

-- 등급: 결과. 구간에 있던 발과 Tag. 발마다 한 행(SG-6). 1-Tag 세트는 한 행.
CREATE TABLE segment.segment_tag (
  segment_id    bigint      NOT NULL REFERENCES segment.segment(id),
  side          text        NOT NULL CHECK (side IN ('left','right')),   -- tag_id 0 = right, 1 = left
  tag_mac       macaddr,                          -- NULL = 이 발은 아직 거리 프레임만(IMU 프레임 없음)
  tag_boot_id   bigint,
  first_seen_at timestamptz NOT NULL,
  PRIMARY KEY (segment_id, side),
  CHECK ((tag_mac IS NULL) = (tag_boot_id IS NULL))
);
CREATE INDEX segment_tag_mac ON segment.segment_tag (tag_mac);   -- wear.tag_mac 귀속
```

### 005_analysis.sql

```sql
CREATE SCHEMA analysis;

-- 등급: 결과. 구간을 한 번 분석한 기록. 끝날 때 한 번 INSERT. 실패하면 행을 만들지 않음(pipeline.job에 남음).
-- id는 core가 호출 전에 nextval로 받아 파일 이름에 씀(AN-6).
CREATE TABLE analysis.run (
  id                 bigserial   PRIMARY KEY,
  segment_id         bigint      NOT NULL REFERENCES segment.segment(id),
  segment_revision   integer     NOT NULL,      -- 보낸 때의 segment.revision
  input              text        NOT NULL CHECK (input IN ('raw','archive')),
  n_chunks_sent      integer     NOT NULL,
  origin_head_us     bigint      NOT NULL,      -- C2 시각 0 = 보낸 첫 묶음의 head_us_start
  origin_at          timestamptz NOT NULL,      -- 그 순간의 UTC
  gaitfusion_version text,                      -- 응답 값. skipped면 NULL일 수 있음
  core_version       text        NOT NULL,      -- 서버 이미지 git sha
  context            jsonb       NOT NULL,      -- 보낸 C2 context 그대로(재현용)
  summary            jsonb,                     -- 응답 result.summary. skipped면 NULL
  status             text        NOT NULL CHECK (status IN ('done','skipped')),
  skip_reason        text,                      -- 422 응답의 reason
  result_path        text        UNIQUE,        -- '/data/result/<run_id>.json.gz'. skipped면 NULL
  result_bytes       bigint,                    -- 압축 전 크기
  result_sha256      bytea,                     -- 압축 전 바이트의 sha256
  started_at         timestamptz NOT NULL,
  finished_at        timestamptz NOT NULL,
  CHECK ((status = 'done') = (result_path IS NOT NULL)),
  CHECK ((status = 'skipped') = (skip_reason IS NOT NULL))
);
CREATE INDEX run_segment ON analysis.run (segment_id, id DESC);

-- 현재 run = 구간마다 id가 가장 큰 run. is_current 열은 없음(AN-8).
-- "더 큰 id의 run이 없음"으로 적어, 조회가 구간 id로 좁힐 때 run_segment 색인 하나로 끝나게 함.
CREATE VIEW analysis.current_run AS
  SELECT r.segment_id, r.id AS run_id
  FROM analysis.run r
  WHERE NOT EXISTS (SELECT 1 FROM analysis.run n WHERE n.segment_id = r.segment_id AND n.id > r.id);

-- 등급: 결과. 걷기 구간 = 사람이 걸은 한 동안. 1단계는 시간 범위·발·모드만(1.2절). 발이 둘이어도 bout는 하나.
-- 주인 열 없음(analysis.bout_subject).
CREATE TABLE analysis.bout (
  id       bigserial   PRIMARY KEY,
  run_id   bigint      NOT NULL REFERENCES analysis.run(id),
  idx      integer     NOT NULL,                -- 응답 bouts 배열의 순번. 궤적 파일 이름에 씀
  start_at timestamptz NOT NULL,
  end_at   timestamptz NOT NULL,
  mode     text        NOT NULL CHECK (mode IN ('fused','imu_only')),
  sides    text        NOT NULL CHECK (sides IN ('left','right','both')),
  quality  jsonb       NOT NULL,                -- 응답 quality 그대로
  UNIQUE (run_id, idx),
  CHECK (end_at > start_at)
);
CREATE INDEX bout_time ON analysis.bout (start_at);

-- 구간의 주인(SG-9). 구간 시작 시각의 착용 기록과 맞춤. C2 context.subject에 씀.
-- 구간마다 맞는 착용 기록을 세는 LATERAL로 적어, 바깥 조건(구간 id·기간)이 먼저 걸리고 그 구간만 계산함(9절).
CREATE VIEW analysis.segment_subject AS
SELECT s.id AS segment_id, o.subject_id, o.ambiguous
FROM segment.segment s
CROSS JOIN LATERAL (
  SELECT CASE WHEN count(*) = 1 THEN min(m.subject_id) END AS subject_id,   -- 같은 우선순위에 둘이면 NULL
         count(*) > 1 AS ambiguous
  FROM (
    SELECT w.subject_id, (w.tag_mac IS NOT NULL) AS exact,
           bool_or(w.tag_mac IS NOT NULL) OVER () AS any_exact          -- tag_mac 명시 쪽이 있으면 그쪽만
    FROM registry.wear w
    WHERE w.device_id = s.device_id
      AND (w.tag_mac IS NULL OR EXISTS (SELECT 1 FROM segment.segment_tag st
                                        WHERE st.segment_id = s.id AND st.tag_mac = w.tag_mac))
      AND w.started_at <= s.start_at
      AND (w.ended_at IS NULL OR s.start_at < w.ended_at)
  ) m
  WHERE m.exact = m.any_exact
) o;

-- 걷기 구간의 주인(SG-9). 걷기 구간 시작 시각의 착용 기록과 맞춤.
-- 속도: 바깥 조건(bout id·기간)이 먼저 걸리고, 남은 bout마다 착용 기록 몇 줄만 셈(9절).
CREATE VIEW analysis.bout_subject AS
SELECT b.id AS bout_id, o.subject_id, o.ambiguous
FROM analysis.bout b
JOIN analysis.run r    ON r.id = b.run_id
JOIN segment.segment s ON s.id = r.segment_id
CROSS JOIN LATERAL (
  SELECT CASE WHEN count(*) = 1 THEN min(m.subject_id) END AS subject_id,   -- 같은 우선순위에 둘이면 NULL
         count(*) > 1 AS ambiguous
  FROM (
    SELECT w.subject_id, (w.tag_mac IS NOT NULL) AS exact,
           bool_or(w.tag_mac IS NOT NULL) OVER () AS any_exact          -- tag_mac 명시 쪽이 있으면 그쪽만
    FROM registry.wear w
    WHERE w.device_id = s.device_id
      AND (w.tag_mac IS NULL OR EXISTS (SELECT 1 FROM segment.segment_tag st
                                        WHERE st.segment_id = s.id AND st.tag_mac = w.tag_mac))
      AND w.started_at <= b.start_at
      AND (w.ended_at IS NULL OR b.start_at < w.ended_at)
  ) m
  WHERE m.exact = m.any_exact
) o;

-- 등급: 캐시. 궤적은 발마다 TRJ1 파일 하나(7.3절), 행은 위치만. 같은 구간의 새 run이 끝나면 파일과 함께 지움.
CREATE TABLE analysis.trajectory (
  bout_id  bigint      NOT NULL REFERENCES analysis.bout(id),
  side     text        NOT NULL CHECK (side IN ('left','right')),
  path     text        NOT NULL UNIQUE,         -- '/data/trajectory/<run_id>/<bout idx>-<side>.trj'
  rate_hz  integer     NOT NULL,                -- 100
  t0       timestamptz NOT NULL,                -- origin_at + t0_s
  n        integer     NOT NULL,
  bytes    bigint      NOT NULL,
  PRIMARY KEY (bout_id, side)
);
```

### 006_archive.sql

```sql
CREATE SCHEMA archive;

-- 등급: 영구 원본. 걷기 원본 조각. 파일 하나 = 행 하나. 한 번 쓰고 수정·삭제 없음.
-- 파일에서 다시 만들 수 있음(TL-4).
CREATE TABLE archive.range (
  id           bigserial   PRIMARY KEY,
  device_id    text        NOT NULL REFERENCES registry.device(id),
  head_boot_id bigint      NOT NULL,
  segment_id   bigint      NOT NULL REFERENCES segment.segment(id),
  first_seq    bigint      NOT NULL,
  last_seq     bigint      NOT NULL,
  n_chunks     integer     NOT NULL,            -- 실제 담긴 수(빈자리 제외)
  start_at     timestamptz NOT NULL,            -- 첫 묶음 시작 UTC(구간의 utc_offset_us로)
  end_at       timestamptz NOT NULL,
  path         text        NOT NULL UNIQUE,     -- '/data/archive/<device>/<YYYY-MM>/<start_at>_<boot>_<first>-<last>.bin'
  bytes        bigint      NOT NULL,
  sha256       bytea       NOT NULL,
  from_run_id  bigint      REFERENCES analysis.run(id),   -- 이 범위를 만든 run. NULL = 파일에서 다시 만든 행(TL-4)
  created_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (last_seq >= first_seq),
  EXCLUDE USING gist (device_id WITH =, head_boot_id WITH =,
                      int8range(first_seq, last_seq, '[]') WITH &&)   -- 같은 부팅 안 seq 겹침 금지
);
CREATE INDEX range_segment ON archive.range (segment_id);

-- 등급: 영구 원본. run마다 "보관 끝" 표시. 걷기가 없어도 행을 만듦(n_ranges = 0). 30일 삭제 조건의 근거.
CREATE TABLE archive.cut (
  run_id       bigint      PRIMARY KEY REFERENCES analysis.run(id),
  n_ranges     integer     NOT NULL,            -- 이번에 새로 만든 범위 수
  n_new_chunks integer     NOT NULL,
  done_at      timestamptz NOT NULL DEFAULT now()
);

-- 등급: 영구(삭제 기록). 30일 삭제(AR-6)가 지운 raw 파일 하나 = 행 하나. 무엇을 언제 지웠는지 남기고,
-- 행을 지운 뒤 파일을 지우기 전에 멈췄으면 다음 날 이 행을 보고 파일을 마저 지움(행이 없는 레코드를 "잃은 행"과 구별).
CREATE TABLE archive.purged (
  path       text        PRIMARY KEY,         -- '/data/raw/<device>/<boot>/<YYYY-MM-DDTHH>.bin'
  n_records  integer     NOT NULL,            -- 지울 때 파일에 있던 온전한 레코드 수
  n_chunks   integer     NOT NULL,            -- 지운 intake.chunk 행 수
  deleted_at timestamptz NOT NULL             -- 행을 지운 시각. 파일은 이 커밋 뒤에 지움
);
```

### 007_roles.sql

```sql
-- 비밀번호는 migrate가 .env 값으로 ALTER ROLE … PASSWORD(PL-1).
-- 역할은 클러스터 전체에 하나. 같은 클러스터의 다른 DB가 이미 만들었으면 건너뜀.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'intake_rw') THEN CREATE ROLE intake_rw LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'core_rw')   THEN CREATE ROLE core_rw   LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'api_rw')    THEN CREATE ROLE api_rw    LOGIN; END IF;
END $$;
-- 받기 커밋은 디스크 동기화를 기다리지 않음. 행은 raw 파일에서 다시 만듦(IN-9).
ALTER ROLE intake_rw SET synchronous_commit = off;

-- intake: 장치 토큰 조회, 자기 표 INSERT(중복 확인용 SELECT 포함).
GRANT USAGE ON SCHEMA registry, intake TO intake_rw;
GRANT SELECT ON registry.device TO intake_rw;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA intake TO intake_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA intake TO intake_rw;
GRANT USAGE ON SCHEMA archive TO intake_rw;
GRANT SELECT ON archive.purged TO intake_rw;                       -- 재색인이 삭제가 처리한 파일을 건너뜀

-- core: 전부 읽기. segment·analysis·archive·pipeline 쓰기. 결과와 영구 원본은 추가만.
GRANT USAGE ON SCHEMA pipeline, registry, intake, segment, analysis, archive TO core_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline, registry, intake, segment, analysis, archive TO core_rw;
GRANT INSERT, UPDATE, DELETE ON pipeline.job TO core_rw;          -- DELETE: done 30일 뒤 정리
GRANT UPDATE ON pipeline.cursor TO core_rw;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA segment TO core_rw;  -- 구간 열림·닫힘, 배치 last_seen_at, Tag MAC 채움
GRANT INSERT ON ALL TABLES IN SCHEMA analysis TO core_rw;         -- 결과는 추가만
GRANT DELETE ON analysis.trajectory TO core_rw;                   -- 옛 run 궤적 캐시 정리
GRANT INSERT ON ALL TABLES IN SCHEMA archive TO core_rw;          -- 걷기 원본: 추가만
GRANT DELETE ON intake.chunk, intake.health, intake.system, intake.rejected TO core_rw;   -- 30일 삭제만. survey는 영구
GRANT USAGE ON ALL SEQUENCES IN SCHEMA pipeline, segment, analysis, archive TO core_rw;

-- api: registry 쓰기, 재분석 작업 넣기, 나머지 읽기.
GRANT USAGE ON SCHEMA pipeline, registry, intake, segment, analysis, archive TO api_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline, registry, intake, segment, analysis, archive TO api_rw;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA registry TO api_rw;
GRANT DELETE ON registry.app_token TO api_rw;                     -- 만료 토큰 정리
GRANT INSERT ON pipeline.job TO api_rw;                           -- 재분석
GRANT USAGE ON ALL SEQUENCES IN SCHEMA registry, pipeline TO api_rw;

-- 아무에게도 없는 권한: archive.* UPDATE·DELETE, intake.* UPDATE, intake.survey DELETE,
-- analysis.*(trajectory 제외) UPDATE·DELETE, registry.*(app_token 제외) DELETE, segment.* DELETE.
```

### 008_analysis_result_status.sql

15절 정할 것 7(2026-11-10)로 더한 열.

```sql
-- 응답 result.status(7.2절). 'partial' = gaitfusion이 자기 마감 시각에 끊겨 궤적 일부가 빠짐(걷기 구간 목록은 온전).
-- NULL = 응답에 없음(옛 gaitfusion), skipped run, 이 파일 전에 만든 run.
-- 표에 열만 더하므로 새 GRANT는 없음(007의 표 권한이 새 열에도 미침).
ALTER TABLE analysis.run
  ADD COLUMN result_status text CHECK (result_status IN ('complete','partial')),
  ADD CHECK (status = 'done' OR result_status IS NULL);
```

관계:

```
registry.device 1─n segment.segment 1─n analysis.run 1─n analysis.bout 1─0..2 analysis.trajectory (발별 파일)
                │                  │                  └─ 1─1 archive.cut
                │                  ├─ 1─2 segment.segment_tag (발 자리별 Tag)
                │                  └─ 1─n archive.range (걷기 원본 조각)
                └─ 1─n segment.installation (앵커 배치)
registry.subject 1─n registry.wear      구간·걷기 구간의 주인 = segment_subject·bout_subject 뷰(조회 때)
intake.chunk ↔ segment.segment는 (device_id, head_boot_id, seq 범위)로 이어짐. FK 없음(묶음 행은 30일 뒤 사라짐)
```

## 11. 시험과 합격 기준

모든 시험은 `compose.dev.yaml` 위에서 `pytest`로 돕니다. 각 단계(M)는 아래 항목이 모두 통과해야 끝난 것입니다. 괄호는 확인하는 요구사항입니다.

**공통 (모든 단계)**
- import-linter 통과(3.2절). `tests/test_ownership.py`: 각 `store.py`의 INSERT·UPDATE·DELETE 대상 스키마가 자기 단계뿐(예외: archive의 intake 삭제).
- 역할 시험: 각 DB 역할로 남의 스키마에 쓰기를 시도하면 권한 오류(4.2절).
- 마이그레이션: 빈 DB에 적용한 뒤 다시 돌리면 아무것도 바뀌지 않음. 같은 클러스터에 두 번째 빈 DB를 만들어 적용해도 오류가 없음(PL-1).

**M1 받기 (PL, RG-1·2, IN, TL-1·3·5)**
1. `tests/contract/wire_vectors/`의 모든 묶음 벡터: 해독값과 결과 코드가 `.json`과 같음. 요청 벡터: HTTP 상태와 `codes`가 같음(IN-1, IN-3).
2. `fake_head` 보통 10분: 보낸 묶음 수 = `intake.chunk` 행 수. 응답 코드가 모두 0.
3. 같은 요청을 다시 보내면 모두 1이고 행 수와 raw 파일 크기가 그대로(IN-5).
4. 모르는 토큰·폐기한 토큰: 401, 파일과 행이 늘지 않음(IN-2).
5. 거절 시나리오마다: 기대 코드, `intake.rejected` 행, rejected 파일 레코드가 있음. `reingest_rejected.py`는 고치지 않은 서버에서 같은 코드를 냄(IN-10).
6. 재색인: `intake.*`를 비운 뒤 `reindex_raw.py`를 돌리면 키, 머리 열, 블록 행이 처음과 같음(TL-3).
7. 비정상 종료: 받는 중에 intake와 db를 `kill -9`하고 다시 띄우면, 응답 코드 0·1을 받은 모든 키가 `intake.chunk`에 있음(IN-6, IN-9).
8. 동시성: 같은 장치로 같은 요청 두 개를 동시에 보내면 한쪽만 0이고 다른 쪽은 1. raw에 한 번만 씀(IN-4). 장치 여러 대를 작업자 여럿으로 동시에 받는 동안 `intake.chunk`의 id 순서와 커밋 순서가 같음: 받는 중에 `id > cursor`를 반복해 읽는 시험 루프가 한 번 본 최대 id보다 작은 id를 나중에 새로 보지 않음(PL-3).
9. 속도: `bench.py` 장치 10대 10분에서 9절의 받기 줄을 만족. `fdatasync` p95를 기록(9절).

**M2 구간 (SG)**
1. 시나리오(가짜 Head, 시계 바꿔 끼움)마다 구간 수, 경계, `closed_reason`, `n_missing_chunks`, `sides`, `segment_tag`가 기대와 같음:
   보통 2시간 → `max_length`로 2개 · Tag 꺼짐 2분(실시간으로 흘러감) → 앞 구간은 `silence_60s`, 다시 켜진 뒤 새 구간 · 인터넷이 끊긴 동안 Tag 꺼짐 2분(밀린 묶음이 한꺼번에 옴) → `gap_60s` · 재부팅 → `boot_changed` · 옛 부팅의 묶음이 늦게 옴 → 옛 구간만 다시 열리고 새 부팅의 구간은 열린 채 · 오른발 Tag(T0) 교체 → `tag_changed` · 중간에 T1 나타남 → 한 구간, `both`, 행 2개 · 배치 변경 → `geometry_changed`, 새 구간의 `installation_id`가 만들 때부터 있음 · 버퍼 넘침 → 빈자리 수 · 프레임 없는 묶음만 → 구간 없음.
2. 인터넷 5분 끊김(밀렸다 몰아 옴): 구간이 `silence_60s`로 닫혔다가 다시 열리고, 최종으로 닫힌 뒤 `analysis` 작업이 정확히 1개 대기(SG-5, PL-2).
3. 결정성: 같은 묶음을 새 DB에 다시 넣어 처리하면 구간 표가 같음(id 제외)(SG-10).
4. `installation`: system 블록마다 행이 생기거나 `last_seen_at`이 바뀌고, 비어 있던 `installation_id`가 채워짐(SG-8).
5. 시각: UTC 추정이 없던 부팅에서 나중에 추정이 생기면 구간의 `time_source`가 `server`로 바뀌고 `start_at`이 그 값으로 다시 셈됨(SG-7).
6. 속도: 묶음 10만 개를 처리하는 시간이 묶음당 0.5 ms 이하(9절).

**M3 분석 호출 (AN, TL-2)**
1. 닫힌 구간 → run 1개(`done`), 걷기 구간 행, 궤적 파일(TRJ1 검사 통과), 결과 원문 파일(sha256이 run과 같음), `archive` 작업 1개.
2. 422 → run `skipped`, `archive` 작업 1개. 500 → 2, 4, 8분 뒤 다시 시도해 모두 네 번 실패하면 `failed`, run 없음, 쓴 파일 없음(PL-2). 형식이 틀린 응답(7.2절 규칙마다 하나씩) → 바로 `failed`(AN-7).
3. 재분석 → 새 run. 옛 run의 걷기 구간 행은 남고 궤적 파일·행은 지워짐(AN-9).
4. 작업 중 core를 죽였다 띄우면 작업이 되살아나 run이 정확히 1개(PL-2).
5. 분석 중에 구간에 묶음이 더해지면 run은 저장되고 `analysis` 작업이 하나 더 들어감(AN-7).
6. 배치 기다리기: system 블록 없이 닫힌 구간은 작업이 미뤄지고, 블록이 오면 그 좌표로 보냄. 1시간이 지나면 `anchors = null`(AN-3).
7. C2 계약 시험(`tests/contract/gaitfusion/`): 요청 파트 순서와 `context` 필드, 응답 검사 규칙 각각. 진짜 gaitfusion 이미지가 있으면 같은 시험을 그것으로도 돌림.
8. 속도: 1시간 구간 run의 DB 트랜잭션이 50 ms 이하(9절).
9. 진짜 gaitfusion 전 구간 시험(`tests/e2e/test_real_gaitfusion.py`, 이미지 이름을 줄 때만): gaitfusion 합성 보행으로 만든 양발 기록(`tests/e2e/data/walk_two_feet.*`, 만든 판과 해시를 같이 둠)을 받기로 보내 구간 → 분석(run의 `result_status`가 `complete`) → 보관 → 앱 궤적 조회까지. 이어서 30일 삭제가 지우는 원본 행을 지우고 재분석하면 보관본(수 분짜리 seq 빈자리)으로 같은 걷기 구간과 궤적이 나옴(AR-8). 가짜 Head(TL-1)의 보행 신호는 형식 시험용이라 진짜 서비스가 걸음으로 알아보지 못하므로 이 파일을 씁니다.

10. 응답 `status`: `complete` → run `result_status = 'complete'`. `partial`(가짜 gaitfusion이 한 발 궤적을 뺌) → run `done`, 현재 run, `result_status = 'partial'`, 다시 분석하는 작업 없음, 앱 API(run, 구간 상세, 착용자의 걷기 구간 목록)에 보임. 없으면 NULL. 다른 값 → 바로 `failed`(2의 규칙 하나). skipped run은 NULL(AN-7, 7.2절).

**M4 원본 보관과 삭제 (AR, TL-4)**
1. 보관 범위: 걷기 구간 ± 15초, 구간 안으로 자름, 겹침 합침, 이미 있는 범위 제외. 파일의 sha256이 행과 같음.
2. 걷기가 없는 run도 `archive.cut` 행(`n_ranges = 0`).
3. 시계를 31일 뒤로: 조건을 만족하는 raw 파일만 지워지고, 조건 하나씩 깨뜨린 경우마다 남음(AR-6). 삭제 순서가 지켜짐.
4. `PURGE_ENABLED = false`면 아무것도 지워지지 않고 지울 대상 수만 로그에 남음(AR-7).
5. raw가 지워진 구간의 재분석은 `input = 'archive'`로 보내고, 범위 사이가 seq 빈자리로 보임(AR-8, AN-4).
6. `verify_archive.py`가 파일 하나를 바꾸면 그것을 찾고, `--reindex`가 지운 행을 되살림.

**M5 앱 API (AP, RG, TL-6)**
1. 8절의 모든 엔드포인트: 정상, 권한 없음(남의 것은 404), 잘못된 입력(422).
2. 장치 토큰은 만들 때만 보이고, 재발급 뒤 이전 토큰으로 받기가 401.
3. health 조회가 wire.md 필드 이름으로 풀려 나옴(AP-3).
4. 궤적 `rate=25`는 100 Hz 파일의 4개마다 1개와 같음(AP-4).
5. 착용 기록을 고치면 다음 조회부터 걷기 구간의 주인이 바뀜(SG-9).
6. 속도: 착용자 1년치 합성 데이터에서 9절의 앱·궤적 줄을 만족, 주요 조회가 색인을 탐(`EXPLAIN`).

**M6 운영 (OP)**
1. 빈 호스트에서 `docker compose up`으로 전부 뜸. `migrate` 뒤 intake·api의 `/healthz`가 정상.
2. `ops/backup.sh`를 로컬 rclone 대상으로 돌려 raw·archive·DB 덤프가 복사됨. 실패하면 알림.
3. `ops/restore.md` 절차를 한 번 따라 빈 compose에서 구간 하나를 재분석함(OP-3).

## 12. 구현 순서와 선행 조건

| 순서 | 저장소 | 일 | 막히는 것 |
|---|---|---|---|
| 0 | 펌웨어 | **C++ 인코더와 시험 벡터**: wire.md의 묶음·상태 블록·응답 인코더(호스트 시험 전용, Head 전송부 아님)와 `tests/wire_vectors/`의 모든 벡터 | M1 시험 1 |
| 1 | 서버 | **M1** 공용, 장치 등록(도구), 받기, `fake_head`, 재색인. 벡터는 펌웨어 커밋을 적은 `tests/contract/wire_vectors/VERSION`과 함께 복사 | — |
| 2 | 서버 | **M2** 구간 | M1 |
| 3 | 서버 | **M3** 분석 호출, `fake_gaitfusion` | M2 |
| 4 | 서버 | **M4** 원본 보관과 삭제(삭제는 꺼 둔 채) | M3 |
| 5 | 서버 | **M5** 앱 API | M1–M4 |
| 6 | 서버 | **M6** compose, 백업, 복구 연습 | M5 |
| 7 | gaitfusion | `service.py`(C2), `wire.py`, 걷기 구간 찾기, 궤적 TRJ1, 이미지. 나오면 M3의 시험 7·9를 진짜 서비스로. 2026-10-30 gaitfusion `2c03531`로 통과. 메모리를 줄인 `8010070`(gaitfusion main)으로도 통과 | 서버 M3과 따로 진행 |
| 8 | 펌웨어 | Head TLS 메모리 확인(1초마다 5 kB POST를 keep-alive로 보내며 힙과 수집이 버티는지) → Head 전송부(묶음 인코더, PSRAM 링 버퍼, HTTPS, 재전송, UTC 추정, health 블록) | 실제 Head로 받기 |
| 9 | 서버 | `import_usb.py`로 실제 기록 넣기. 2026-11-10 만듦: 실제 기록 `wifi-save-head.jsonl`(10분, range·system, 예전 모양의 system)을 `--dry-run`으로 바꿔 묶음 602개가 모두 검사를 통과. `imu_batch` 경로는 그 줄이 든 실제 기록이 아직 없어 펌웨어 출력 코드대로 쓴 줄로만 시험함 | 서버 M3 |
| 10 | 서버 | proxy와 원격 VM 배포 | 15절의 결정 |

측량용 3D 뷰어는 지금처럼 현장 노트북 broker(`../uwb-firmware/server/mosquitto.conf`)로 쓰고, 설치가 끝나면 Head 설정을 서버 URL로 바꿉니다.

## 13. 보안, 용량, 알려진 한계

### 13.1 보안

- 외부 포트는 443 하나입니다. intake·api·gaitfusion·Postgres는 내부망에 둡니다.
- 장치 토큰은 발급 때 한 번 보이고 해시만 저장합니다. 할 수 있는 일은 `/d/v1/`에 올리기뿐입니다.
- Head는 서버 인증서의 루트 CA를 펌웨어에 넣고 검증합니다. 도메인이 없으면 자체 서명으로 시작하고 Head 쪽은 같습니다.
- 비밀값은 `.env`(git 제외)에 둡니다. DB 역할 셋으로 쓰기 범위를 제한합니다. `subject`에는 실명 대신 코드를 씁니다.
- 실제 대상자로 넘어갈 때: 건강정보는 개인정보보호법상 민감정보입니다. 국내 리전, 별도 동의서, 접근 기록(누가 어떤 착용자의 데이터를 봤는지), 저장·백업 암호화, 보존·파기 기간, 연구라면 IRB 요건을 갖춘 뒤에 갑니다.

### 13.2 용량

| 항목 | 값 |
|---|---|
| Head 1대 | 1-Tag 4.8 kB/s, 2-Tag 7.1 kB/s, 초당 요청 1개. 버퍼 PSRAM 4 MB ≈ 14분(1-Tag) |
| 전체 원본 (30일) | 1-Tag 시간당 17 MB, 24시간 착용 시 하루 0.41 GB. 3명 × 30일 = 37 GB (회전) |
| 걷기 원본 (영구) | 걷기 하루 1.5시간, 앞뒤 여유를 더해 그 1.5배로 가정: 3명 연 43 GB. 걷기 하루 1시간이면 29 GB, 2시간이면 57 GB |
| 궤적 캐시 | TRJ1 보행 1시간에 약 19 MB, 발이 둘이면 두 배: 1-Tag 3명·하루 1.5시간이면 연 31 GB |
| 결과 원문 | 1단계(걷기 구간만)는 run당 수 kB. 2단계 특징이 들어가면 1시간 구간에 약 1 MB, gzip 뒤 약 0.15 MB로 가정: 3명 연 약 4 GB |
| 색인·결과 표 | `intake.chunk` 30일 1.4 GB, 분석 결과 표 연 1 GB 이하(1단계) |
| 서버 디스크 | 1-Tag 기준 1년 차 약 120 GB, 이후 연 약 80 GB 증가. 250 GB면 약 2년 반. 2-Tag는 IMU와 궤적이 두 배라 더 빨리 참 |
| 원격 저장 | raw 37 GB(고정) + 걷기 원본 연 43 GB + DB 덤프(14일 + 매월 1개) |
| gaitfusion 메모리 | 양발로 1시간 내내 걸은 구간 하나(가장 무거운 경우)를 분석할 때 서비스와 작업 프로세스 2개가 함께 쓴 최고치. 메모리를 줄인 gaitfusion `8010070`(main, 응답은 줄이기 전과 바이트까지 같음)은 개발 PC 약 0.9 GB, 리눅스 컨테이너 약 0.6 GB. 줄이기 전 `2c03531`은 개발 PC 약 2.1 GB, 리눅스 컨테이너 약 1.5 GB. 2026-10-30 잼 |
| 권장 VM | 2 vCPU 이상, SSD 250 GB. 메모리 4 GB 이상: 메모리를 줄인 gaitfusion이면 위 최고치(1 GB 아래)와 DB, 서버 프로세스가 함께 들어감. 줄이기 전 판이면 4 GB는 빠듯함. 정확한 값은 호스팅을 정할 때(15절 1) |

실제 착용 시간은 Tag 전원에 달려 있어 이보다 훨씬 짧을 가능성이 큽니다.

### 13.3 알려진 한계

- **구간 결정성의 예외**: 늦게 온 묶음이 이미 갈라진 두 구간 사이를 이어도 구간을 합치지 않습니다(SG-3). Head는 오래된 묶음부터 보내므로 드뭅니다.
- **UTC**: Head의 UTC 추정 오차는 ±30 ms입니다. 부팅 뒤 서버에 한 번도 닿지 못한 동안의 묶음은 도착 시각 기준이라, 끊김이 길면 그 구간의 시각이 끊김만큼 늦게 찍힙니다(`time_source = 'arrival'`로 표시).
- **30일 뒤**: 걷기로 판정되지 않은 시간의 원본은 사라집니다(5.3절).
- **IMU와 앵커의 묶음 사이 검사**: 받을 때는 보지 않습니다(IN-8). IMU 프레임의 `tag_mac`이 세트 목록에 없는 경우 등은 지금 경고 로그도 없습니다. 2단계 전에 segment에 넣을지 정합니다.

## 14. 2단계 계획: 지표 규약, 특징 풀기, 일별 값, 보고서

2026-11-11에 정했습니다. 예전 초안은 보폭 표·일별 요약·선별 지수를 "gaitfusion의 특징이 다 정해진 뒤"로 미뤘습니다. 이것을 바꿉니다. 서버는 gaitfusion이 무엇을 얼마나 잘 계산하는지가 아니라 **결과를 어떤 형식으로 내는지**에만 기댑니다. 그래서 형식(지표 규약)을 먼저 정하고, 그 위에서 특징 풀기, 일별 값(추세), 보고서를 만듭니다. gaitfusion의 출력이 바뀌어도(필드가 늘거나, 빠지거나, 이름이 바뀌어도) 서버는 코드와 표를 고치지 않고 받아들입니다(MC-4).

1단계와의 약속은 그대로입니다.
- 2단계는 1단계 표를 바꾸지 않고 **표를 더합니다**(마이그레이션 009부터). 예외는 `pipeline.job`에 작업 종류와 대상 열을 더하는 것뿐입니다.
- 특징은 결과 원문 파일(AN-10)에서 채웁니다. 그래서 2단계를 켜는 날 gaitfusion을 다시 돌리지 않고 지난 run을 채울 수 있습니다.
- 100 Hz 신호(원본 묶음, 궤적)는 2단계에서도 DB에 넣지 않습니다. DB에 들어가는 가장 잘게 나뉜 것은 보폭 한 줄입니다.
- 1단계가 푸는 필드(걷기 구간의 시각·발·모드, 궤적)는 지금처럼 C2(7.2절)가 엄격히 검사합니다. 원본 보관 범위가 여기에 달려 있기 때문입니다. 아래의 "넓게 받기"는 그 밖의 분석 필드에만 적용합니다.

### 14.1 범위

| 부분 | 내용 | 번호 |
|---|---|---|
| **2A 지표 규약 (C4)** | 모든 부품이 분석 결과를 같은 뜻으로 읽게 하는 약속: 지표 목록 파일, 공통 규칙, 표 형식의 이름과 판, 형식이 바뀔 때의 규칙 | MC |
| **2B 특징 풀기** (`feature` 단계) | run이 끝날 때마다 결과 원문의 보폭·걸음·회전·창·삽화를 줄 단위로 `feature.record`에 넣음. 값은 낸 그대로 둠 | FT |
| **2C 일별 값** (`summary` 단계) | 착용자의 날마다 지표 값을 계산해 `summary.daily_value`에 둠. 앱의 추세 조회가 이것을 읽음 | SM |
| **2D 보고서** (`report` 단계) | 착용자와 기간을 받아, 그 기간의 지표 값을 계산하고 의학 논문 대목을 찾아 근거로 붙여 글을 쓰고 저장. 앱 API로 냄 | RP |
| 미룸 (3단계) | 질환별 선별 지수, gaitfusion의 모델 결과(`models[]`) 풀기 | — |

미룬 것은 자체 검증 데이터(진단, 임상 척도, 낙상 기록)가 있어야 뜻이 생기는 것들입니다.

### 14.2 목표와 성공 기준

| # | 목표 | 성공 기준 |
|---|---|---|
| G4 | **분석 결과를 한 규약으로 주고받고, 형식이 바뀌어도 받아들인다** | 풀이에 쓰는 모든 값이 지표 목록에 있음. gaitfusion이 필드를 더하거나 빼거나 이름을 바꿔도 서버의 코드와 표는 그대로이고, 필요한 일은 지표 목록 파일을 고치는 것뿐. 목록에 없는 필드나 범위 밖의 값은 run마다 숫자로 보임 |
| G5 | **특징을 줄 단위로 저장한다** | run이 끝난 뒤 5분 안에 `feature.record` 행이 있음. 지난 run을 결과 원문에서 다시 풀면 같은 행이 나옴 |
| G6 | **추세를 낸다** | run이 끝난 뒤 10분 안에 그날의 지표 값이 고쳐짐. 앱이 요청 하나로 착용자의 지표 몇 개의 일별 값 1년치를 받음 |
| G7 | **기간 보고서를 낸다** | 착용자와 기간을 주면 10분 안에 보고서가 저장됨. 글 안의 모든 수치는 저장된 지표 값에서 왔고, 뜻을 풀이한 모든 문단은 저장소에 있는 논문 대목을 가리킴. 보고서의 입력이 함께 저장되어 같은 입력으로 다시 쓸 수 있음. 진단이 아님 |

### 14.3 흐름

```
Head ─▶ intake ─▶ segment ─▶ analysis ──C2──▶ gaitfusion (다듬기 → 걷기 구간 → 궤적과 특징)
                                │ ◀── 결과 원문 + 궤적 ──┘
                                ▼
                  feature: 결과 원문 ─▶ feature.record (줄 단위, 낸 그대로)          ← run마다 자동
                                ▼
                  summary: 그날의 줄 ──지표 목록으로 풀이──▶ summary.daily_value ─▶ api ─▶ 앱의 추세
                                ▼
앱 ─▶ api ─ report 작업 ─▶ report: 기간의 줄 ─▶ 지표 값 + 지난 30일과 견줌 ─┐
                                   논문 저장소 ─────▶ 논문 대목 ───────────┴▶ 글 쓰기 ─▶ report.* + 파일 ─▶ api ─▶ 앱
```

- **분석 단위는 셋입니다.** 신호 분석(analysis)은 서버가 정한 구간(SG)마다 구간이 닫힐 때 한 번 돕니다. 일별 값은 날짜마다 자동으로 계산합니다. 보고서는 사람이 고른 기간(하루, 몇 시간, 몇 분)마다 돕니다. 일별 값과 보고서는 gaitfusion을 부르지 않고, 저장된 `feature.record`를 착용자와 시각으로 읽습니다. 기간에 구간이 몇 개 걸쳐 있든 같습니다.
- **쓰는 곳은 하나씩입니다.** gaitfusion은 DB와 파일을 만지지 않습니다(지금과 같음). `feature.*`는 feature 단계만, `summary.*`는 summary 단계만, `report.*`와 보고서 파일은 report 단계만 씁니다. 논문 저장소는 DB가 아니라 파일과 검색 색인이고 report 단계만 읽습니다(14.7절).
- **보고서를 누가 언제 시작하는지는 흐름과 무관합니다.** 작업은 "착용자, 시작, 끝"만 받습니다.
- **지표 값 계산은 한 곳입니다.** 일별 값과 보고서는 `pipeline/metrics.py`의 같은 순수 함수로 계산합니다. 하루를 기간으로 준 보고서의 값은 그날의 일별 값과 같습니다.

### 14.4 C4: 지표 규약 (MC)

지표 목록 파일은 이 저장소에 둡니다. gaitfusion 저장소는 이 파일을 복사해 자기 출력을 검사하는 데 쓰고, 복사한 커밋을 적어 둡니다. 어느 쪽이 먼저 바꿔도 됩니다: gaitfusion이 목록에 없는 필드를 먼저 내면 서버는 받아 두고(MC-4), 목록에 항목이 생긴 때부터 풀이에 씁니다.

- **MC-1 지표 목록.** `contracts/metrics.toml` 하나가 뜻의 기준입니다. 풀이에 쓰는 모든 값(gaitfusion이 내는 필드, 서버가 기간에 대해 계산하는 값)이 한 항목씩 있습니다. 일별 값, 보고서의 지표 값, 앱에 보이는 이름과 단위, 논문과의 연결이 모두 여기서 나옵니다. [gait-features.md](https://github.com/HyperBlaze456/gaitfusion/blob/main/gait-features.md)는 근거 문헌과 고른 이유를 설명하는 글로 남고, 이름·단위·뜻이 다르면 지표 목록이 이깁니다.

| 항목의 키 | 내용 |
|---|---|
| `id` | 고정 이름. `<수준>.<이름>` 꼴(예 `stride.length_m`, `window.freeze_index`, `period.speed_median_mps`). 한번 쓴 이름은 다른 뜻으로 다시 쓰지 않음 |
| `level` | `stride`(보폭) · `step`(양발 걸음) · `turn`(회전) · `window`(10초 창) · `event`(삽화) · `period`(기간). 줄의 종류는 아래 `[[table]]` 항목이 정하므로 더할 수 있음 |
| `type`, `unit`, `range` | 형(`number`·`integer`·`text`), 단위, 있을 수 있는 값의 범위(예 `length_m` 0–3). 범위 밖은 틀린 값으로 보고 풀이에 쓰지 않음 |
| `names` | gaitfusion 지표만. 응답에서 이 지표의 필드 이름이 `id`의 이름 부분과 다를 때, 표 형식의 판마다 이름과 단위 환산 배수를 적음(예 `{"gaitfusion.stride/2" = {name = "len_cm", scale = 0.01}}`). 없으면 같은 이름 |
| `producer` | `gaitfusion` 또는 `server` |
| `show` | `basic`(착용자·보호자에게도 기본으로 보임) · `detail`(의료진·연구자 보기에서만). 계산과 저장은 둘 다 똑같이 하고, 보이는 범위만 다름(MC-8) |
| `needs` | `one_foot` · `both_feet`. 발목 하나로 낼 수 있는지 |
| `trust` | `validated`(문헌이 같은 부위·방법으로 검증) · `proxy`(대리 지표) · `low`(소규모 연구) · `unverified`(우리 센서 위치에서 검증 없음) · `fact`(문헌으로 검증할 대상이 아닌 값: 자료가 있던 시간, 융합 비율 등). 보고서가 이 값에 따라 말의 세기를 정함. 자유 생활 검증은 거의 허리 센서라서, 지금 `validated`는 발목 센서로 인정된 보폭 속도와 그 95백분위뿐이고 나머지 보행 지표는 `unverified` |
| `label`, `meaning` | 앱과 보고서에 보이는 짧은 이름, 한 문장 정의. 논문 검색도 이 문장을 씀 |
| `sources` | 근거 논문의 key 목록(논문 저장소의 목록과 같은 key, 14.7절). key는 `<첫 저자 성 소문자><연도>`이고, 같은 저자·연도가 둘이면 gait-features.md 출처 순서대로 끝에 `a`, `b`를 붙임. 기관이 낸 문서는 기관의 줄인 이름(예 `ema2023`). 비어 있으면 보고서는 값만 적고 풀이하지 않음 |
| `from` | `period` 지표만. 계산에 쓰는 값: 줄의 지표 `id`(예 `stride.duration_s`) 또는 서버가 아는 `bout.duration_s`(걷기 구간의 길이). `rule = "given"`이면 없음 |
| `rule` | `period` 지표만. 계산 방법의 이름. 뜻은 RP-3의 표 |
| `where` | `period` 지표만. 쓸 걷기 구간의 조건: `bout_min_s`(이상), `bout_max_s`(미만), `mode`(`fused`·`imu_only`). 없으면 전부 |
| `by_side` | `period` 지표만. `true`면 발마다 값 하나, `false`면 값 하나(RP-3의 주 발 규칙) |
| `min_n` | `period` 지표만. 값을 믿을 만하다고 보는 최소 표본 수 |

```toml
version = 1

[[table]]                             # 응답 bouts[] 안의 배열 하나 = 줄의 종류 하나
field = "strides"
level = "stride"
formats = ["gaitfusion.stride/1"]     # 서버가 풀이할 수 있는 판
keys = { start = "start_s", end = "end_s", side = "side" }

[[metric]]
id = "stride.length_m"
level = "stride"
type = "number"
unit = "m"
range = [0.0, 3.0]
producer = "gaitfusion"
show = "detail"
needs = "one_foot"
trust = "unverified"
label = "보폭 길이"
meaning = "한 발이 한 번 정지한 곳에서 다음에 정지한 곳까지의 수평 거리"
sources = ["schlachetzki2017", "dias2023"]

[[metric]]
id = "period.stride_time_cv"
level = "period"
type = "number"
unit = "ratio"
producer = "server"
show = "basic"
needs = "one_foot"
trust = "unverified"
label = "보폭 시간 변동"
meaning = "보폭 시간의 변동 계수(표준편차 ÷ 평균). 걷기 구간마다 계산한 값들의 중앙값"
sources = ["hausdorff2001", "shah2020"]
from = "stride.duration_s"
rule = "cv"
where = { bout_min_s = 30 }
by_side = true
min_n = 3
```

- **MC-2 공통 규칙.** 모든 표 형식과 값 전달에 똑같이 적용합니다. 10절의 공통 규칙과 같은 뜻입니다.
  - **값 없음**: `null`은 "계산하지 못함", 필드가 없는 것은 "이 판의 서비스가 내지 않음"입니다. 0, 빈 문자열, NaN으로 대신 쓰지 않습니다.
  - **단위**: SI. 이름 끝에 단위를 붙입니다(`_m`, `_s`, `_mps`, `_deg`, `_dps`, `_hz`, `_g`, `_spm`). 접미가 없는 비율은 0–1입니다.
  - **시각**: 응답 안에서는 `origin_head_us`를 0으로 한 초(`*_s`), DB에서는 `timestamptz`(`origin_at + 초`)입니다. 길이는 초입니다.
  - **발**: `left`·`right`. 양발을 함께 보고 낸 값은 발이 `null`입니다.
  - **출처**: 모든 값은 run 하나로 거슬러 올라갑니다. run에는 gaitfusion 판과 계산 방법의 판(`summary.method_versions`)이 있고, 일별 값과 보고서에는 계산할 때의 지표 목록 판이 있습니다.
- **MC-3 표 형식의 이름과 판.** 줄의 종류마다 형식 이름과 판이 있습니다: `gaitfusion.stride/1`(지금 있음), `gaitfusion.step/1`, `gaitfusion.turn/1`, `gaitfusion.window/1`, `gaitfusion.event/1`. 서버가 만드는 것은 `uwbserver.report_input/1`입니다. 응답은 자기가 실은 형식을 `summary.formats`에 밝힙니다(예 `{"strides": "gaitfusion.stride/1"}`). 이 필드가 없는 지난 응답은 `strides`를 `gaitfusion.stride/1`로, 나머지 표는 `/1`로 봅니다. `c2_version`은 1 그대로입니다(더해지는 필드이므로).
- **MC-4 받는 것은 넓게, 풀이는 지표 목록으로.** feature 단계는 줄의 값을 고르거나 바꾸지 않고 낸 그대로 저장합니다. 이름을 지표 `id`로 옮기고, 단위를 환산하고, 형과 범위를 보는 일은 **읽을 때** `pipeline/metrics.py` 한 곳에서 지표 목록으로 합니다. 그래서 gaitfusion의 형식이 바뀌어도 서버의 코드와 표는 그대로이고, 지표 목록을 고친 뒤 지난 run을 다시 풀 필요도 없습니다.

| gaitfusion에서 바뀐 것 | 서버가 하는 일 | 사람이 할 일 |
|---|---|---|
| 줄에 값 필드가 늘어남 | 저장함. 이름을 `unknown_fields`에 적고 경고 로그 | 풀이에 쓰려면 지표 목록에 항목 추가 |
| 필드가 빠지거나 `null` | 그 지표는 그 줄에서 값 없음. 기간에 값이 하나도 없으면 `no_data` | 없음 |
| 필드 이름이 바뀜 | 저장함(새 이름은 `unknown_fields`에) | 지표 목록의 `names`에 한 줄 |
| 단위나 뜻이 바뀜(형식의 판이 오름) | 저장함. 지표 목록의 `formats`에 그 판이 없는 동안은 그 판의 줄을 풀이에 쓰지 않음 | `formats`에 판을 더하고, 달라진 지표에 `names`(이름, 배수) 한 줄 |
| 새 표(배열)가 생김 | 풀지 않고 이름을 `unknown_tables`에 적음. 결과 원문에는 남아 있음 | 지표 목록에 `[[table]]` 항목을 더한 뒤 다시 풀기(FT-5) |
| 표가 없어짐 | 그 종류의 줄이 없음 | 없음 |

- **MC-5 gaitfusion이 지킬 것.** 서버가 위처럼 받아들이려면 다음 둘은 지켜야 합니다. (1) 이미 내던 필드의 단위나 뜻을 바꿀 때는 그 표 형식의 판을 올립니다(`/2`). 같은 이름과 같은 판으로 뜻만 바꾸면 서버는 알 길이 없습니다. (2) 줄마다 시작·끝 시각이 있습니다. 필드를 더하거나 빼는 것, `null`을 내는 것은 판을 올리지 않습니다.
- **MC-6 지금 어긋난 곳의 정리.** 세 문서가 다르게 적은 것을 gaitfusion이 실제로 내는 값에 맞춥니다.
  - 보폭 한 줄은 **연속한 두 정지의 가운데 사이**입니다(예전 표 초안의 "착지부터 다음 착지까지"가 아님).
  - `uwb_support`는 IMU 단독이면 **`null`**입니다(예전 표 초안의 0이 아님. MC-2의 값 없음 규칙).
- **MC-7 검사.** `tests/contract/metrics/`가 다음을 확인합니다. gaitfusion 저장소도 복사한 목록 파일로 2번을 자기 출력에 돌립니다.
  1. 목록 자체: `id`가 겹치지 않음, 이름의 단위 접미와 `unit`이 맞음, `period` 지표의 `from`이 있는 지표나 `bout.duration_s`를 가리킴, `rule`과 `where`의 이름이 RP-3에 있는 것, `by_side = true`인 지표의 `from`이 줄의 지표인 것, `sources`의 key가 `<첫 저자 성 소문자><연도>` 꼴(논문 목록과의 대조는 `papers.py check`, M10).
  2. 응답 견본(`tests/contract/metrics/samples/`에 두는 진짜 gaitfusion 결과 하나와 가짜 gaitfusion 결과. 진짜 결과는 만든 gaitfusion 커밋을 같이 적음): 목록의 모든 gaitfusion 지표가 진짜 결과 견본에 실제로 있고 형과 범위가 맞음. 목록에 없는 필드가 있으면 이름을 보여 주며 실패(서버 운영에서는 경고일 뿐이지만, 시험에서는 목록을 맞추게 함).
  3. MC-4 표의 줄마다: 그렇게 바꾼 응답을 넣어도 풀기가 실패하지 않고 표의 "서버가 하는 일"대로 됨.

- **MC-8 처음 넣는 지표와 보이는 범위.** 지표 목록에는 **지금 실제로 나오는 값과 거기서 계산할 수 있는 값만** 넣습니다. gait-features.md의 점검표(전부 넣으면 100개가 넘음)를 미리 옮기지 않습니다. 서버는 목록에 없는 필드도 받아 두므로(MC-4), gaitfusion이 새 값을 내기 시작할 때 항목을 하나씩 더하면 됩니다. 계산의 기준은 의료진·연구자이고, 착용자에게는 `show = "basic"`인 것만 보입니다(2026-11-11 정함).

| 묶음 | 지표 (`id`) | 발마다 | `show` |
|---|---|---|---|
| 보폭 줄 (gaitfusion, 9) | `stride.duration_s`, `stride.length_m`, `stride.speed_mps`, `stride.clearance_m`, `stride.heading_deg`, `stride.swing_s`, `stride.stance_s`, `stride.stance_ratio`, `stride.uwb_support` | 예 | 모두 `detail` |
| 자료 범위 | `period.wear_time_s`(자료가 있던 시간) | — | `basic` |
| | `period.fused_ratio`(걷기 시간 중 UWB 융합으로 계산한 걷기 구간의 비율) | — | `detail` |
| 활동량 | `period.walking_time_s`, `period.bout_count`, `period.bout_duration_max_s` | — | `basic` |
| | `period.stride_count`, `period.distance_m`, `period.bout_duration_median_s`, `period.bout_duration_p90_s`, `period.walking_share_over_60s`(60초 이상 걷기 구간이 걷기 시간에서 차지하는 비율) | `stride_count`만 | `detail` |
| 속도 | `period.speed_median_mps` | — | `basic` |
| | `period.speed_p95_mps`, `period.speed_10_30s_median_mps`, `period.speed_30_60s_median_mps`, `period.speed_over_60s_median_mps` | — | `detail` |
| 보폭 | `period.stride_length_median_m` | 예 | `basic` |
| | `period.cadence_median_spm`, `period.stride_time_median_s` | `stride_time`만 | `detail` |
| 변동(30초 이상 걷기 구간) | `period.stride_time_cv` | 예 | `basic` |
| | `period.stride_time_sd_s`, `period.swing_time_cv`, `period.stride_length_cv` | 예 | `detail` |
| 발 움직임 | `period.clearance_median_m`, `period.stance_ratio_median` | 예 | `detail` |

  보폭 줄 9개와 기간 지표 24개입니다. 그중 `basic`은 7개입니다. 속도의 상위값은 문헌의 보폭 속도 95백분위(gait-features.md 3절)에 맞춰 `p95`로 둡니다. 회전, 10초 창, 삽화, 양발 걸음과 거기서 나오는 기간 지표(회전 속도, 동결 횟수, 좌우 비대칭 등)는 gaitfusion이 내기 시작할 때 더합니다.

### 14.5 특징 풀기 (FT)

- **FT-1 작업.** analysis는 run을 `done`으로 저장하는 트랜잭션에서 `feature {run_id}` 작업을 넣습니다(`skipped` run은 넣지 않음). 작업 하나가 `feature.unpack` 행 하나와 그 run의 줄들을 만듭니다. 푸는 일이 실패해도 run, 걷기 구간, 궤적, 원본 보관에는 영향이 없습니다.
- **FT-2 풀기.** 결과 원문 파일을 읽어 sha256을 run의 값과 대조합니다. `bouts[]`의 순번으로 `analysis.bout`를 찾고, 지표 목록의 `[[table]]`에 있는 배열마다 항목 하나를 `feature.record` 행 하나로 넣습니다. `kind`는 표의 `level`, `idx`는 배열 안 순번(버린 줄이 있어도 그대로), 시각은 MC-2대로 옮기고, `v`는 그 항목 전체입니다. `v`에서 바꾸는 것은 하나뿐입니다: JSON의 NaN과 무한은 jsonb에 넣을 수 없으므로 `null`로 둡니다(MC-2의 "계산하지 못함"). COPY 한 번, 전부 한 트랜잭션입니다.
- **FT-3 버리는 것은 하나뿐.** 줄의 시작·끝이 유한한 수가 아니거나(시각으로 나타낼 수 없을 만큼 큰 것 포함) 끝이 시작보다 앞서거나 발 값이 틀리거나 줄이 객체가 아니면 그 줄을 넣지 않고 `n_dropped_records`에 셉니다(행으로 만들 수 없으므로). 그 밖의 값은 형이나 범위가 틀려도 그대로 저장하고, 지표 목록으로 본 수를 `n_bad_values`에, 목록에 없는 필드를 `unknown_fields`에 `<배열 이름>.<필드>` 꼴로(예 `strides.x`), 목록에 없는 배열 이름을 `unknown_tables`에 적습니다. `unknown_tables`에 드는 것은 걷기 구간 바로 아래의 배열 중 지표 목록의 표가 아니고 `trajectories`도 아니며 항목이 모두 객체인 것입니다. 넷 중 하나라도 있으면 경고 로그를 남깁니다.
- **FT-3a 실패.** 결과 원문 파일이 없거나, gzip이나 JSON이 깨졌거나, sha256이 run과 다르거나, 원문의 걷기 구간 수가 `analysis.bout`와 다르면 작업을 바로 `failed`로 둡니다(다시 해도 같으므로). 그 밖의 파일·DB 오류와 지표 목록 읽기 실패는 PL-2의 재시도입니다. 어느 경우에도 run, 걷기 구간, 궤적, 원본 보관은 그대로입니다.
- **FT-4 읽기.** `feature.record`를 읽는 코드는 모두 `pipeline/metrics.py`의 `normalize(형식, 줄)`을 거쳐 `{지표 id: 값}`을 받습니다. 여기서 이름 옮기기, 단위 환산, 형과 범위 검사(틀리면 값 없음)를 합니다. 형식은 그 run의 `feature.unpack.formats`에서 옵니다. `v`를 직접 읽는 코드는 두지 않습니다.
- **FT-5 다시 풀기.** 같은 run을 다시 풀면 한 트랜잭션에서 그 run의 `feature.unpack`과 `feature.record` 행을 지우고 새로 넣습니다. 지표 목록에 새 표를 더했을 때만 필요합니다. `feature.*`는 결과 원문에서 다시 만들 수 있으므로 등급은 결과이고, 이 삭제만 허용합니다.
- **FT-6 지난 run 채우기.** `tools/unpack_features.py [--since 시각] [--all]`(TL-9)이 조건에 맞는 run마다 `feature` 작업을 넣습니다. 2단계를 처음 켤 때와 새 표를 더한 뒤에 씁니다. 그 순간 풀고 있는 run은 대기 작업을 하나 더 넣을 수 없어 건너뛰므로(PL-2), 도구가 건너뛴 수를 보이면 잠시 뒤 다시 돌립니다.
- **FT-7 현재 run.** 줄은 run마다 따로 있습니다. 읽는 쪽은 1단계와 같이 `analysis.current_run`으로 현재 run의 걷기 구간만 고릅니다.
- **FT-8 끝나면.** 푼 트랜잭션에서 `summary {device_id, date_from, date_to}` 작업을 넣습니다. 날짜는 그 run의 걷기 구간이 걸친 `Asia/Seoul` 날짜이고, 재분석이면 옛 run의 걷기 구간이 걸친 날짜도 합칩니다. 걷기 구간이 없으면 넣지 않습니다.
- **속도와 크기.** 1시간 구간 run 하나를 푸는 트랜잭션이 200 ms 이하입니다. 보폭 한 줄은 약 0.5 kB이므로, 하루 1.5시간 걷는 1-Tag 착용자 한 명이 연 약 1 GB입니다(13.2절의 가정).

### 14.6 일별 값과 추세 (SM)

- **SM-1 작업.** `summary {device_id, date_from, date_to}`. feature가 넣고(FT-8), api도 착용 기록을 만들거나 고칠 때 고치기 전·후 기간을 합쳐 넣습니다(1단계의 AP-5가 여기서 바뀜). 그 장치에 겹치는 착용 기록이 있는 착용자 전원을 날짜마다 다시 계산합니다. 같은 대상의 대기 작업은 하나이고, 도는 중에는 하나 더 대기할 수 있습니다(도는 사이 끝난 run을 놓치지 않으려고).
- **SM-2 하루.** `Asia/Seoul` 날짜입니다. 한국은 일광 절약 시간이 없으므로 코드는 UTC+9 고정값을 한 곳(`pipeline/clock.py`)에 두고 씁니다(이미지에 시간대 자료가 없어도 되게). 현재 run의 걷기 구간 중 그 날짜에 시작했고 `analysis.bout_subject`가 그 착용자로 정한 것을 씁니다. 자정을 넘는 걷기 구간은 시작한 날에 넣습니다.
- **SM-3 계산과 저장.** 지표 목록의 `period` 항목마다 `metrics.period_values`(14.7절 RP-3의 규칙)로 값을 계산합니다. 한 트랜잭션에서 그 착용자·날짜의 `summary.day`와 `summary.daily_value` 행을 지우고 새로 넣습니다. 값이 있는 지표만 행이 있고, 걷기가 없는 날은 행이 없습니다. 자료가 있던 시간과 걷기 시간도 지표입니다(`period.wear_time_s`, `period.walking_time_s`).
- **SM-3a 빈틈 없이 다시 계산.** 아직 풀리지 않은 run의 걷기 구간은 줄 없이 횟수와 시간만 셉니다. 그 run이 풀리면 FT-8이 같은 날짜를 다시 넣습니다. 재분석이 `skipped`로 끝나 걷기 구간이 사라진 때에도, analysis가 그 run을 저장하는 트랜잭션에서 앞선 run의 걷기 구간이 걸친 날짜의 `summary` 작업을 넣어 옛 값이 남지 않게 합니다. 착용 기록의 날짜 범위는 끝 시각 바로 앞 순간의 날짜까지이고, 끝이 없는 착용은 오늘까지입니다.
- **SM-4 지표 목록이 바뀌면.** `tools/resummarize.py [--since 날짜]`(TL-11)이 지난 날짜의 `summary` 작업을 넣습니다. `period` 지표를 더하거나 `names`를 고친 뒤에 씁니다. 표는 그대로입니다.
- **SM-5 추세 조회.** `summary.daily_value`를 착용자·지표·날짜 범위로 읽을 뿐이고 계산하지 않습니다. 앱은 지표의 이름·단위·믿을 만한 정도를 지표 목록 조회(`GET /v1/metrics`)로 받으므로, 지표가 늘어도 앱을 고치지 않고 보일 수 있습니다.
- **속도.** 착용자의 하루 계산은 300 ms 이하(그날 걷기 구간의 줄만 읽음), 추세 조회는 지표 5개 × 1년에 20 ms 이하입니다.

### 14.7 보고서 (RP)

- **RP-0 짜임 (2026-11-11 정함).** 글을 쓰는 것은 **대화 코어** 하나입니다. 코어는 착용자와 기간의 지표 값 전부를 맥락으로 받고, 논문 찾기 도구를 가진 모델과 묻고 답합니다. 모델은 여러 지표를 함께 보고 관련된 양상을 스스로 추론하며, 필요한 논문 대목을 스스로 찾습니다. **보고서**는 이 코어에 정해진 지시("이 기간의 보고서를 써라")를 주는 한 가지 쓰임이고, 사람이 이어서 묻는 대화도 같은 코어입니다. 지금 입구는 명령줄 도구(`tools/ask.py`, TL-12)이고, 작업 큐·저장·앱 API(RP-1, RP-9, 14.8절의 보고서 줄)는 그다음에 붙입니다.
- **RP-1 작업.** `report {subject_id, from_at, to_at}`. api가 넣습니다. 같은 대상의 대기·실행 작업은 하나입니다(PL-2). 보고서 작업은 자기 작업 스레드에서 돌아(`REPORT_WORKERS`, 기본 1) analysis 작업을 늦추지 않습니다.
- **RP-2 기간의 자료 고르기.** 현재 run의 걷기 구간 중 시작 시각이 `[from_at, to_at)` 안이고 `analysis.bout_subject`가 그 착용자로 정한 것을 고릅니다. 기간 경계에 걸친 걷기 구간은 시작한 쪽에 넣습니다. 아직 풀리지 않은 run(`feature.unpack` 행이 없음)이 있으면 작업을 1분 뒤로 미룹니다.
- **RP-3 지표 값 계산.** `pipeline/metrics.py`의 순수 함수 `period_values`가 고른 걷기 구간과 그 줄에서, 지표 목록의 `period` 항목마다 값 하나를 계산합니다. 수치 계산은 모두 여기서 하고, 글을 쓰는 모델은 계산하지 않습니다. 걷기 구간이 하나도 없으면 글을 쓰지 않고 보고서를 `skipped`(`no_walking`)로 남깁니다.
  - 계산 방법(`rule`)의 뜻. "값들"은 `where`에 맞는 걷기 구간에서 모은 `from`의 값입니다.

| `rule` | 값 | `n` |
|---|---|---|
| `given` | 호출하는 쪽이 넘긴 값. `period.wear_time_s` = 기간과 겹치는 그 착용자의 구간 길이의 합(기간 안쪽만) | 1 |
| `count` · `sum` · `max` | 값들의 개수 · 합 · 최댓값 | 값의 수 |
| `median` · `p90` · `p95` | 값들을 한데 모아 낸 중앙값 · 90백분위 · 95백분위(사이 값은 선형 보간) | 값의 수 |
| `cv` · `sd` | 걷기 구간마다 그 안의 값들로 변동 계수(표준편차 ÷ 평균) · 표준편차(표본, n − 1)를 내고, 그 값들의 중앙값. 값이 3개보다 적은 걷기 구간과 평균이 0인 걷기 구간(`cv`)은 뺌 | 쓴 걷기 구간의 수 |
| `ratio` | `where`에 맞는 걷기 구간의 값의 합 ÷ 모든 걷기 구간의 값의 합 | 모든 걷기 구간의 수 |
| `cadence` | 보폭 시간마다 120 ÷ 보폭 시간(분당 걸음 수)을 내고 그 중앙값 | 값의 수 |

  - 줄의 값에서 계산하는 지표는 길이 10초 이상인 걷기 구간만 씁니다(`where`의 `bout_min_s`가 10 이상). 변동(`cv`, `sd`)은 30초 이상입니다. 10초 미만 걷기 구간은 횟수와 시간(`bout.duration_s`에서 계산하는 지표)에만 셉니다.
  - **발.** `by_side = true`인 지표는 발마다 그 발의 줄만으로 값 하나를 냅니다. `by_side = false`인데 줄의 값을 쓰는 지표는 걷기 구간마다 **주 발**(그 걷기 구간에서 보폭 줄이 더 많은 발, 같으면 왼발)의 줄만 씁니다. 두 발의 줄을 섞으면 같은 걸음을 두 번 세기 때문입니다. 주 발은 값이 `null`인 줄도 세어 정합니다. 발이 없는 줄(`side`가 `null`)은 그 걷기 구간에 왼발·오른발 보폭 줄이 하나도 없을 때만 씁니다. 발 없이 나오는 종류의 줄(양발 걸음 등)에서 계산하는 지표를 더할 때는 이 규칙을 먼저 넓힙니다.
  - 값이 `null`인 줄은 그 지표의 계산에서 뺍니다. 양발이 있어야 하는 지표(`needs = "both_feet"`)는 양발 걷기 구간에서만 냅니다.
  - 계산에 든 값이 하나도 없으면 0이 아니라 값 없음(`no_data`)입니다. 횟수와 합도 같습니다. `ratio`는 분모가 0이면 값 없음이고, 조건에 맞는 것이 없으면 0입니다.
  - `by_side = true`는 줄의 값에서 계산하는 지표에만 씁니다. 보고할 발은 `where`와 상관없이 기간의 보폭 줄에 나온 발이고, 하나도 없으면 발 없는 값 없음 하나를 냅니다.
  - 지표 값의 형식:

```json
{"metric": "period.stride_time_cv", "side": "left", "value": 0.031, "unit": "ratio",
 "n": 14, "sufficient": true, "trust": "unverified", "where": {"bout_min_s": 30},
 "baseline": {"median": 0.027, "days": 19, "change": 0.004}}
```

  `value`가 `null`이면 `reason`이 붙습니다: `no_data`(기간의 줄에 그 값이 없음. 아직 나오지 않는 지표도 여기), `needs_both_feet`. `n`은 계산에 든 표본 수(지표에 따라 보폭 수 또는 걷기 구간 수), `sufficient`는 `n ≥ min_n`입니다.
- **RP-4 지난 날들과 견주기.** `baseline`은 `from_at` 앞 30일의 `summary.daily_value`에서 `sufficient`인 날의 중앙값(`median`), 그런 날의 수(`days`), 값과의 차이(`change`)입니다. 그런 날이 7일보다 적으면 `baseline`을 붙이지 않습니다. 기간이 하루보다 짧으면 일별 값과 조건이 달라 그대로 견주기 어려우므로, 보고서 입력에 `baseline_comparable = false`를 적고 글에 정해진 주의 문구를 넣습니다.
- **RP-5 논문 저장소.** DB가 아니라 파일입니다. report 단계만 읽고, 사람이 도구(`tools/papers.py`, TL-10)로 넣습니다.

```
/data/papers/
  papers.toml             ← 목록: key, 제목, 저자, 연도, doi, 원문 파일 이름, 쓸 수 있는 범위(사용 허락). 도구가 통째로 다시 씀
  source/<key>.<확장자>   ← 원문(PDF 등). 사람이 넣음. 등급: 영구 원본(백업 대상). 도구는 원문을 지우지 않음
  text/<key>.txt          ← 원문에서 뽑은 평문(전문, 또는 전문을 쓸 수 없으면 초록). 백업 대상
  passages/<key>.jsonl    ← 평문을 나눈 대목. 줄마다 {id: "<key>#<순번>", page, text}. 평문에서 다시 만들 수 있음
  index/                  ← 대목의 검색 색인: vectors.f32(대목마다 벡터 하나)와 meta.json. 등급: 캐시(지워도 passages에서 다시 만듦)
```

  `key`는 MC-1의 규칙을 따릅니다(예 `schlachetzki2017`). `papers_version`은 `papers.toml`의 sha256입니다. 논문은 수백 편 이하이므로 별도 검색 서비스를 두지 않고, report가 색인 파일을 읽어 프로세스 안에서 견줍니다. 대목은 빈 줄로 나뉜 문단이고 1200자를 넘으면 자릅니다. 색인은 만든 벡터 함수의 이름과 대목의 해시를 적어 두어, 대목이나 함수가 바뀌면 낡은 색인으로 보고 쓰지 않습니다. 처음 넣는 논문은 gait-features.md 출처 목록의 의학·보행 논문입니다: 공개 논문은 전문, 유료 논문은 초록과 서지만 넣습니다(2026-11-11 정함. 원문을 옮겨 적어도 되는 범위는 15절 10).
- **RP-6 근거 찾기.** 모델이 도구 `search_papers(query, metrics?, k?)`를 스스로 부릅니다. 질문은 모델이 만들고(논문이 영어이므로 영어로), 여러 번 찾을 수 있습니다.
  - **범위**는 논문 저장소 안뿐입니다. 인터넷 검색은 주지 않습니다.
  - **방식**은 둘을 섞습니다(2026-11-11 정함): 단어가 맞는 정도(BM25)와 벡터가 가까운 정도로 각각 순위를 매기고 두 순위를 합칩니다(순위 역수의 합). 벡터 쪽은 바꿔 끼우는 자리입니다. 기본 색인은 네트워크 없이 도는 글자 조각 해시(뜻을 아는 모델이 아닌 대역)로 만듭니다. 뜻을 아는 모델은 선택 사항으로 둘이 있습니다(2026-11-12 더함): OpenRouter의 임베딩 API(`openrouter:openai/text-embedding-3-large`, 키 `OPENROUTER_API_KEY`)와 이 PC에서 도는 작은 모델(`fastembed:BAAI/bge-small-en-v1.5`, 선택 의존성 묶음 `embed`). `tools/papers.py build --embedder <이름>`으로 색인을 만들면 찾을 때 질문도 같은 모델로 바꿉니다. 바깥 API를 쓰면 공개된 논문의 대목 글과 검색 질문이 그 API로 나갑니다(착용자 자료는 나가지 않음). 기본 색인을 어느 것으로 할지는 아직 정하지 않았습니다.
  - **평가**: `tools/eval_retrieval.py`가 질의 묶음 `eval/retrieval/queries.toml`(지표마다 하나, 논문마다 하나)로 방식마다 논문 단위 Hit@1·5·10과 MRR@10을 내고 `report/data/rag_retrieval.json`에 씁니다. 색인은 메모리에서 만들고, 바깥 API로 센 벡터는 `report/data/cache/`에 남겨 같은 글을 다시 보내지 않습니다.
  - `metrics`를 주면 그 지표들의 `sources` 논문 안에서만 찾고, 주지 않으면 모든 논문에서 찾습니다. 지표 목록의 "지표 → 논문" 연결은 벽이 아니라 출발점입니다.
  - 돌려준 대목에는 대화 안에서 이어지는 번호(`p1`, `p2`, …)가 붙고, 모델은 이 번호로만 인용합니다.
  - 모델이 무엇을 찾았고 무엇을 받았는지(질문, 조건, 돌려준 대목의 id와 순위)를 모두 기록에 남깁니다(RP-7).
- **RP-7 맥락과 기록.** 대화를 시작할 때 모델에 넘기는 맥락은 `uwbserver.report_input/1` 하나입니다. 대화가 끝나면 맥락, 주고받은 말, 찾은 기록을 한 덩어리(기록)로 돌려주어 저장할 수 있게 합니다. 모델이 찾는 방식은 같은 입력에서도 결과가 달라질 수 있으므로, 왜 그렇게 썼는지는 이 기록으로 되짚습니다.

| 맥락의 키 | 내용 |
|---|---|
| `schema`, `catalog_version` | `uwbserver.report_input/1`, 지표 목록의 판 |
| `subject` | 착용자 코드, 키, 다리 길이, 마비측(없으면 `null`). 실명은 없음 |
| `period`, `baseline_comparable` | `from_at`, `to_at`, RP-4의 표시 |
| `coverage` | 자료가 있던 시간(구간의 합), 걷기 시간, 걷기 구간 수, 찬 발, 센서 위치(`lateral_ankle`), UWB 융합 비율, `partial` run 수, 주인이 모호해 뺀 걷기 구간 수 |
| `values` | RP-3·RP-4의 지표 값 배열. 값마다 번호(`v1`, `v2`, …)와 지표의 `label`, `meaning`, `show`, `sources`를 함께. `baseline`에는 방향 `direction`(`higher`·`lower`·`same`)도 있습니다: 값과 중앙값을 보고서의 글 꼴로 바꾸어 같으면 `same`, 아니면 `change`의 부호. 2026-11-12에 더한 필드이므로 형식의 판은 `/1` 그대로입니다(MC-5와 같은 규칙) |
| `runs` | 읽은 run id 목록 |

- **RP-8 글 쓰기.** `report/writer.py`가 바깥의 글 쓰는 모델 API(Claude Opus 5.5, 2026-11-11 정함)를 부릅니다. 바깥으로 나가는 것은 맥락과 찾은 논문 대목뿐입니다: 착용자 코드, 지표 값, 공개된 논문 대목. 실명, 원본 신호, 궤적, 보폭 줄은 나가지 않습니다.
  - **수치.** 모델은 측정값을 직접 적지 않고 자리 표시(`{v3}` = 맥락의 3번 값)를 적습니다. 서버가 그 자리에 값과 단위를 넣습니다. 그래서 글 안의 수치가 계산한 값과 다를 수 없습니다.
  - **인용.** 문헌에 기대는 문장에는 인용 자리 표시(`[p2]`)를 붙입니다. 이 대화에서 `search_papers`가 실제로 돌려준 대목만 인용할 수 있습니다. 찾지 못했으면 찾지 못했다고 쓰고, 기억으로 채우지 않습니다.
  - **추론의 말씨.** 여러 지표가 함께 보이는 양상과 관련된 증상을 추론해 적되, "문헌에서 이런 양상은 ○○와 함께 보고되었다"까지만 씁니다. 의심·가능성·진단·치료 권고의 말은 쓰지 않습니다.
  - **한계.** 한계는 글의 중심으로 다루지 않습니다. 센서 위치와 자료 부족에 대한 정해진 짧은 문구를 서버가 글 끝에 한 번 붙이고, 자료가 부족한 값(`sufficient = false`)은 표에 표시합니다.
  - **서버의 검사.** 모든 자리 표시가 맥락과 이 대화의 찾은 기록에 있는 번호를 가리킴. 자리 표시 밖에 숫자로 적은 측정값이 없음. 평소와의 차이를 `baseline.direction`과 반대 방향의 말로 쓰지 않음. 보고서는 정해진 부분을 모두 갖추고, 요약은 `show = "basic"`인 값만 씀. 어기면 한 번 다시 쓰게 하고, 또 어기면 실패로 둡니다.
  - **"숫자로 적은 측정값"의 기준.** 단위(지표 목록의 단위, `%`, 우리말 단위 낱말)가 바로 붙은 숫자입니다. 맥락에 있는 조건의 숫자(예 "30초 이상")와, 인용 `[pN]`이 붙은 문장의 숫자(문헌이 보고한 값)는 뺍니다. 그래서 단위 없이 적은 값, 글자로 적은 수, 인용이 붙은 문장 속에 섞인 측정값은 잡지 못합니다. 이 검사는 실수를 줄이는 장치이고, 수치의 정확함은 자리 표시가 맡습니다.
  - **"반대 방향"의 기준.** 문장 단위로 봅니다. `{vN}`이 있고 그 값이 모두 `direction`을 가지며 인용 `[pN]`이 없는 문장에서, 지표 `label`을 지운 뒤 방향 낱말을 찾습니다. 값이 커짐은 늘다·증가·상승·웃돌다·높다·길다·빠르다·많다·크다, 작아짐은 줄다·감소·하락·밑돌다·낮다·짧다·느리다·적다·작다이고, 어간으로 맞춥니다. 문장의 값이 모두 `higher`(또는 모두 `lower`)이고 반대 방향의 낱말만 있을 때 문제로 봅니다. 값의 방향이 섞인 문장, 양쪽 낱말이 함께 있는 문장, 부정("않", "못", "아니", "없")이 있는 문장, `same`인 값은 판단하지 않습니다. 그래서 부정한 문장, 값 번호 없이 쓴 비교, 목록에 없는 낱말("덜", "나아졌다")은 잡지 못합니다(2026-11-12 더함, 글 지시 `report-chat/2`).
  - **값을 넣는 꼴.** 유효 숫자 3자리(1000 이상은 정수), 비율은 %, 횟수는 정수, 초는 60초·1시간을 넘으면 분·시간으로 적습니다.
  - **모델이 쓰는 꼴.** 보고서 요청에는 정해진 제목 넷(`## 요약`, `## 활동량`, `## 속도와 보폭`, `## 종합 풀이`)으로 쓰게 하고 서버가 제목으로 나눕니다. 응답 한 번의 길이 한도는 16,000 토큰입니다(넘으면 그 질문은 실패).
  - **보고서의 부분.**

| 순서 | 부분 | 내용 | 누가 |
|---|---|---|---|
| 1 | 머리 | 착용자 코드, 기간, "진단이 아니며 참고용" 문구, 쓴 자료의 판 | 서버 |
| 2 | 요약 | 얼마나 걸었고, 얼마나 빨랐고, 걸음이 고른지, 평소와 달랐는지. 쉬운 말로 3–5문장. `show = "basic"`인 값만 | 모델 |
| 3 | 자료 범위 | 자료가 있던 시간, 찬 발, UWB 융합 비율, 빠진 자료 | 서버 |
| 4 | 활동량 | 값의 표와 짧은 풀이 | 표는 서버, 풀이는 모델 |
| 5 | 속도와 보폭 | 값의 표와 짧은 풀이 | 표는 서버, 풀이는 모델 |
| 6 | 종합 풀이 | 걸음의 고르기, 발 움직임, 지난 30일과의 차이를 포함해 모든 값을 함께 보고 쓴 풀이와 관련 증상의 추론. 인용 포함 | 모델 |
| 7 | 나머지 값 | 4·5번에 없는 값의 표 | 서버 |
| 8 | 한계와 근거 문헌 | 정해진 짧은 한계 문구, 인용한 논문 목록 | 서버 |

  - **누구에게 보이나.** 착용자 보기(`view=basic`)는 1–3번, 의료진·연구자 보기는 전부입니다. 단, 지금의 디버그 단계에서는 종합 풀이(증상 추론)도 모두에게 보입니다(설정 `REPORT_INFERENCE_FOR_ALL`, 지금 기본 `true`). 실제 대상자로 넘어갈 때 끕니다(13.1절).
  - 호출 실패와 시간 초과는 재시도하고, 모델이 답을 거절하면(`refusal`) 실패로 두고 이유를 남깁니다. 안전 분류기의 거절은 서버 쪽 대체 모델로 한 번 넘깁니다(API의 `fallbacks`).
  - 질문 하나에 찾기는 `REPORT_MAX_SEARCHES`번(기본 12)까지입니다. 넘으면 도구가 오류를 돌려주어 모델이 그만 찾고 쓰게 합니다.
  - **명령줄 입구(TL-12).** `tools/ask.py --subject ID --from 시각 --to 시각`에 `--show-context`(맥락만 출력, 키가 없어도 됨), `--report`, `--question "…"`, 또는 아무것도 주지 않으면 대화(`/report`, `/quit`). `--trace-out 파일`은 기록을 저장하고, `--fake`는 키 없이 전 과정을 도는 정해진 대역 모델을 씁니다(진짜 저장소에서 한 번 찾고, 가짜임을 글에 밝힘). 설정: `REPORT_MODEL`(기본 `claude-opus-5-5`), `REPORT_EFFORT`(기본 `high`), `REPORT_MAX_SEARCHES`. API 키는 SDK가 환경의 `ANTHROPIC_API_KEY`에서 읽습니다.
- **RP-9 저장.** 호출 전에 보고서 id를 받고(AN-6과 같음), 맥락(`/data/report/<id>/input.json.gz`), 글(`/data/report/<id>/report.md`), 기록(`/data/report/<id>/trace.json.gz`: 주고받은 말과 찾은 기록)을 임시 이름 → fsync → 이름 바꾸기로 쓴 뒤, 한 트랜잭션에서 `report.report`, `report.report_run`, `report.citation` 행을 넣고 작업을 `done`으로 바꿉니다. 같은 기간을 다시 만들면 새 보고서이고 옛 것은 남습니다. 보고서 파일은 백업합니다(글 쓰는 모델의 답은 같은 입력에서도 달라질 수 있어 다시 만들 수 없음). 아직 만들지 않았습니다(RP-0).

### 14.8 앱 API에 더하는 것 (C3)

| 메서드와 경로 | 내용 | 권한 | 폴더 |
|---|---|---|---|
| `GET /v1/metrics` | 지표 목록: `id`, `level`, `label`, `unit`, `meaning`, `trust`, `needs`, `show`, 지표 목록의 판. 앱은 `show`로 착용자 화면에 보일 지표를 고름 | 로그인 | summary |
| `GET /v1/subjects/{id}/trend?metrics=&from=&to=` | 지표마다·발마다 일별 값의 배열 `{metric, side, points: [{date, value, n, sufficient}]}`. 값이 있는 지표·발만 나옴. `metrics`는 쉼표로 이은 `period.*` id(최대 10개). `to`의 기본은 오늘(한국 날짜), `from`의 기본은 `to` − 365일, 양 끝을 포함해 최대 366일. 모르는 지표·`period`가 아닌 지표·범위 초과는 422 | 본인, admin | summary |
| `GET /v1/bouts/{id}/records?kind=` | 걷기 구간 하나의 줄(보폭 등): `{bout_id, run_id, kind, unpacked, format, records: [{idx, side, start_at, end_at, values}]}`. `values`는 지표 `id`로 옮긴 것(FT-4). `kind`의 기본은 `stride`, 지표 목록에 없는 종류는 422. 아직 풀지 않은 run이면 `unpacked = false`와 빈 배열 | 본인, admin | feature |
| `POST /v1/subjects/{id}/reports` | `{from, to}` → 202 `{subject_id, from, to, queued}`(이미 대기 중이면 `queued = false`). 기간은 1분 이상 31일 이하 | 본인, admin | report |
| `GET /v1/subjects/{id}/reports?from=&to=` | 보고서 목록(id, 기간, 상태, 만든 시각) | 본인, admin | report |
| `GET /v1/reports/{id}?view=detail` | 보고서 하나: 글 원문, 지표 값 배열, 인용한 논문(제목, 저자, 연도, doi, 인용한 대목), 자료 범위(`coverage`). `view=basic`이면 1–3번 부분과 `show = "basic"`인 값(디버그 단계에서는 종합 풀이도, RP-8). 아직 만들지 않음 | 본인, admin | report |
| `GET /v1/runs/{id}` (바뀜) | 푼 기록을 함께: 줄 수, `n_dropped_records`, `n_bad_values`, `unknown_fields`, `unknown_tables` | admin | analysis |

### 14.9 DDL 초안

1단계 DDL(001–008) 위에 오류 없이 적용됨을 확인했습니다(2026-11-11, PostgreSQL 17.6). 분석 값의 이름·단위·뜻은 표가 아니라 지표 목록이 정하므로, 지표가 늘거나 바뀌어도 이 표들은 바뀌지 않습니다.

```sql
-- 009_feature.sql (초안)
CREATE SCHEMA feature;

-- 작업 종류 feature를 더함. 대상은 archive처럼 run 하나.
ALTER TABLE pipeline.job DROP CONSTRAINT job_kind_check;
ALTER TABLE pipeline.job ADD CONSTRAINT job_kind_check CHECK (kind IN ('analysis','archive','feature'));
ALTER TABLE pipeline.job DROP CONSTRAINT job_check1;
ALTER TABLE pipeline.job ADD CONSTRAINT job_run_target CHECK ((kind IN ('archive','feature')) = (run_id IS NOT NULL));
CREATE UNIQUE INDEX job_one_feature ON pipeline.job (run_id) WHERE kind = 'feature' AND state IN ('queued','running');

-- 등급: 결과(결과 원문 파일에서 다시 만들 수 있음). run 하나를 푼 기록. 다시 풀면 이 행과 그 run의 record 행을 지우고 새로 넣음.
CREATE TABLE feature.unpack (
  run_id            bigint      PRIMARY KEY REFERENCES analysis.run(id),
  catalog_version   integer     NOT NULL,       -- 풀 때의 지표 목록 판(아래 세 값은 이 판으로 본 것)
  formats           jsonb       NOT NULL,       -- 응답이 밝힌 표 형식. 예 {"strides": "gaitfusion.stride/1"}
  n_records         integer     NOT NULL,       -- 넣은 줄 수
  n_dropped_records integer     NOT NULL,       -- 시작·끝·발이 틀려 버린 줄 수
  n_bad_values      integer     NOT NULL,       -- 지표 목록의 형이나 범위에 맞지 않은 값 수(값은 그대로 저장됨)
  unknown_fields    text[]      NOT NULL,       -- 지표 목록에 없던 필드. '<표>.<필드>' 꼴
  unknown_tables    text[]      NOT NULL,       -- 지표 목록에 없던 표(풀지 않음)
  core_version      text        NOT NULL,
  unpacked_at       timestamptz NOT NULL
);

-- 등급: 결과. 분석 결과의 줄 하나: 보폭 하나, 걸음 하나, 회전 하나, 10초 창 하나, 삽화 하나.
-- 값은 gaitfusion이 낸 그대로 v에 둠. 이름·단위·뜻은 읽을 때 지표 목록이 정함(MC-4).
CREATE TABLE feature.record (
  id        bigserial   PRIMARY KEY,
  bout_id   bigint      NOT NULL REFERENCES analysis.bout(id),
  kind      text        NOT NULL,               -- 'stride'·'step'·'turn'·'window'·'event'와 지표 목록이 더하는 것. CHECK 없음
  idx       integer     NOT NULL,               -- 응답 배열 안의 순번, 0부터
  side      text        CHECK (side IN ('left','right')),   -- NULL = 발 구분 없음
  start_at  timestamptz NOT NULL,
  end_at    timestamptz NOT NULL,
  v         jsonb       NOT NULL,               -- 응답의 그 줄 전체. 조회 조건에 쓰지 않음(9절)
  UNIQUE (bout_id, kind, idx)                   -- 걷기 구간의 줄을 읽는 색인을 겸함
);

GRANT USAGE ON SCHEMA feature TO core_rw, api_rw;
GRANT SELECT, INSERT, DELETE ON ALL TABLES IN SCHEMA feature TO core_rw;   -- 다시 풀기는 지우고 새로 넣음
GRANT SELECT ON ALL TABLES IN SCHEMA feature TO api_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA feature TO core_rw;

-- 010_summary.sql (초안)
CREATE SCHEMA summary;

-- 작업 종류 summary를 더함. 대상은 장치와 날짜 범위(그 장치를 찬 착용자 전원을 다시 계산).
ALTER TABLE pipeline.job DROP CONSTRAINT job_kind_check;
ALTER TABLE pipeline.job ADD CONSTRAINT job_kind_check CHECK (kind IN ('analysis','archive','feature','summary'));
ALTER TABLE pipeline.job ADD COLUMN device_id text, ADD COLUMN date_from date, ADD COLUMN date_to date;
ALTER TABLE pipeline.job ADD CONSTRAINT job_summary_target
  CHECK ((kind = 'summary') = (device_id IS NOT NULL AND date_from IS NOT NULL AND date_to IS NOT NULL));
CREATE UNIQUE INDEX job_one_summary ON pipeline.job (device_id, date_from, date_to)
  WHERE kind = 'summary' AND state = 'queued';   -- 도는 중에 새 run이 끝나면 하나 더 대기할 수 있게 queued만

-- 등급: 결과(feature에서 다시 계산). 착용자의 하루를 계산한 기록. 걷기가 있던 날만 행이 있음.
CREATE TABLE summary.day (
  subject_id      bigint      NOT NULL REFERENCES registry.subject(id),
  date            date        NOT NULL,         -- Asia/Seoul
  catalog_version integer     NOT NULL,
  computed_at     timestamptz NOT NULL,
  PRIMARY KEY (subject_id, date)
);

-- 등급: 결과. 그날의 지표 값 하나. 값이 있는 것만 행이 있음. 지표가 늘어도 표는 바뀌지 않음.
CREATE TABLE summary.daily_value (
  subject_id bigint  NOT NULL,
  date       date    NOT NULL,
  metric     text    NOT NULL,                  -- 지표 목록의 id (period.*)
  side       text    CHECK (side IN ('left','right')),   -- NULL = 발 구분 없음
  value      double precision NOT NULL,
  n          integer NOT NULL,                  -- 계산에 든 표본 수
  sufficient boolean NOT NULL,                  -- n ≥ 지표의 min_n
  FOREIGN KEY (subject_id, date) REFERENCES summary.day(subject_id, date),
  UNIQUE NULLS NOT DISTINCT (subject_id, metric, side, date)   -- 추세 조회의 색인
);

GRANT USAGE ON SCHEMA summary TO core_rw, api_rw;
GRANT SELECT, INSERT, DELETE ON ALL TABLES IN SCHEMA summary TO core_rw;   -- 다시 계산은 그날 행을 지우고 새로 넣음
GRANT SELECT ON ALL TABLES IN SCHEMA summary TO api_rw;

-- 011_report.sql (초안)
CREATE SCHEMA report;

-- 작업 종류 report를 더함. 대상은 착용자와 기간.
ALTER TABLE pipeline.job DROP CONSTRAINT job_kind_check;
ALTER TABLE pipeline.job ADD CONSTRAINT job_kind_check CHECK (kind IN ('analysis','archive','feature','summary','report'));
ALTER TABLE pipeline.job ADD COLUMN subject_id bigint, ADD COLUMN from_at timestamptz, ADD COLUMN to_at timestamptz;
ALTER TABLE pipeline.job ADD CONSTRAINT job_report_target
  CHECK ((kind = 'report') = (subject_id IS NOT NULL AND from_at IS NOT NULL AND to_at IS NOT NULL));
CREATE UNIQUE INDEX job_one_report ON pipeline.job (subject_id, from_at, to_at)
  WHERE kind = 'report' AND state IN ('queued','running');

-- 등급: 결과. 기간 하나의 보고서. 끝날 때 한 번 INSERT. 실패하면 행을 만들지 않음(pipeline.job에 남음).
-- 같은 기간을 다시 만들면 새 행이고 옛 행은 남음. id는 core가 먼저 nextval로 받아 파일 경로에 씀.
CREATE TABLE report.report (
  id              bigserial   PRIMARY KEY,
  subject_id      bigint      NOT NULL REFERENCES registry.subject(id),
  from_at         timestamptz NOT NULL,
  to_at           timestamptz NOT NULL,
  status          text        NOT NULL CHECK (status IN ('done','skipped')),
  skip_reason     text,                         -- 'no_walking'(RP-3)
  catalog_version integer     NOT NULL,         -- 지표 목록의 판
  papers_version  text,                         -- 논문 목록 파일의 sha256(RP-5)
  writer_version  text,                         -- 글을 쓴 모델 id와 지시문 판
  core_version    text        NOT NULL,
  input_path      text        UNIQUE,           -- '/data/report/<id>/input.json.gz' (uwbserver.report_input/1)
  input_sha256    bytea,                        -- 압축 전 바이트의 sha256
  text_path       text        UNIQUE,           -- '/data/report/<id>/report.md'
  trace_path      text        UNIQUE,           -- '/data/report/<id>/trace.json.gz' (주고받은 말과 찾은 기록, RP-7)
  n_values        integer,                      -- 입력에 든 지표 값 수
  started_at      timestamptz NOT NULL,
  finished_at     timestamptz NOT NULL,
  CHECK (to_at > from_at),
  CHECK ((status = 'done') = (text_path IS NOT NULL AND input_path IS NOT NULL)),
  CHECK ((status = 'skipped') = (skip_reason IS NOT NULL))
);
CREATE INDEX report_subject ON report.report (subject_id, from_at, id DESC);

-- 등급: 결과. 보고서가 읽은 run(그때의 현재 run).
CREATE TABLE report.report_run (
  report_id bigint NOT NULL REFERENCES report.report(id),
  run_id    bigint NOT NULL REFERENCES analysis.run(id),
  PRIMARY KEY (report_id, run_id)
);

-- 등급: 결과. 보고서가 인용한 논문 대목.
CREATE TABLE report.citation (
  report_id  bigint  NOT NULL REFERENCES report.report(id),
  idx        integer NOT NULL,                  -- 글 안의 인용 번호, 1부터
  paper_key  text    NOT NULL,                  -- 논문 목록의 key
  passage_id text    NOT NULL,                  -- '<key>#<순번>'
  query      text    NOT NULL,                  -- 이 대목을 찾은 질문(모델이 만든 것)
  PRIMARY KEY (report_id, idx)
);

GRANT USAGE ON SCHEMA report TO core_rw, api_rw;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA report TO core_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA report TO api_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA report TO core_rw;
```

### 14.10 코드, 시험, 구현 순서

**폴더.** 단계 셋을 더합니다. import 규칙(3.2절)의 흐름 단계 줄에 셋을 넣고, 서로 import하지 않는 것도 같습니다.

```
contracts/      metrics.toml                                   ← C4 지표 목록
src/uwbserver/
  pipeline/     metrics.py                                     ← 지표 목록 읽기, normalize, period_values (순수 코드, wire.py와 같은 자리)
  feature/      rules.py store.py run.py routes.py
  summary/      store.py run.py routes.py
  report/       rules.py store.py run.py routes.py writer.py papers.py
tools/          unpack_features.py resummarize.py papers.py fake_writer.py
tests/          contract/metrics/  feature/  summary/  report/
```

**시험과 합격 기준.**

**M7 지표 규약 (MC)** — 2026-11-11 만듦. 3번(gaitfusion 저장소의 일)은 아직.
1. MC-7의 세 검사가 통과.
2. 지표 목록에 MC-8의 표 그대로 33개(보폭 줄 9, 기간 24)가 있고, 항목마다 `label`, `meaning`, `trust`, `show`가 차 있음. `sources`는 gait-features.md의 참고 문헌에서 옮긴 key(논문 원문은 M10에서 모음).
3. gaitfusion이 `summary.formats`를 내고, 복사한 지표 목록으로 자기 출력 검사를 통과(gaitfusion 저장소의 일).

**M8 특징 풀기 (FT)** — 2026-11-11 만듦. 5번은 11일 전에 만든 `gaitfusion:local` 이미지로 한 번 통과. 6번은 개발 PC에서 1시간 양발 run(줄 6,688개)의 트랜잭션 45–49 ms.
1. 가짜 gaitfusion의 run → `feature.unpack`과 줄. 줄 수와 `v`가 결과 원문과 같음.
2. MC-4 표의 경우를 하나씩 넣은 응답 → 표대로이고 숫자와 이름이 `feature.unpack`에 있음. run과 걷기 구간은 그대로. 지표 목록에 `names` 한 줄을 더하면 다시 풀지 않고도 그 값이 읽힘.
3. 시작·끝이 틀린 줄 → 그 줄만 빠지고 `n_dropped_records`에 셈.
4. 다시 풀기 → 행이 같음(id 제외). `unpack_features.py`로 지난 run이 채워짐.
5. 진짜 gaitfusion 전 구간 시험(M3-9)에 이어 그 run의 보폭 줄이 있고 `n_bad_values = 0`, `unknown_fields`가 빔.
6. 속도: 1시간 구간 run을 푸는 트랜잭션 200 ms 이하.

**M9 기간 계산, 일별 값, 추세 (RP-2, RP-3, SM)** — 2026-11-11 만듦. 6번은 개발 PC에서 하루 계산 중앙값 88 ms(보폭 줄 9,792개인 날), 추세 조회 중앙값 6.8 ms(지표 5개 × 1년). 속도 시험의 1년치 자료는 한 주만 실제 크기의 보폭 줄이고 나머지 날은 줄였음.
1. 손으로 답을 아는 합성 보폭 줄 → `period` 지표마다 기대 값. 30초 조건, 길이별 나눔, 발별·양발 지표, `no_data`·`needs_both_feet`.
2. 기간 경계: 경계에 걸친 걷기 구간이 시작한 쪽에만 들어감. 기간을 둘로 쪼갠 횟수·시간의 합이 통째로 계산한 값과 같음.
3. run이 끝나면 그날의 일별 값이 생김. 재분석 뒤에는 새 run의 값으로 바뀜. 착용 기록을 고치면 두 착용자의 그 날짜가 다시 계산됨(SM-1).
4. 하루를 기간으로 준 `period_values`의 값이 그날의 `summary.daily_value`와 같음.
5. 추세 조회: 정상, 남의 착용자(404), 모르는 지표(422). `GET /v1/metrics`가 지표 목록과 같음.
6. 속도: 착용자 1년치 합성 데이터에서 하루 계산 300 ms 이하, 추세 조회 20 ms 이하이고 색인을 탐.

**M10 논문 저장소 (RP-5, RP-6)** — 2026-11-11 저장소·도구·섞은 찾기를 만듦. 벡터 함수는 대역뿐이고 진짜 모델은 아직. gait-features.md 출처 목록의 논문 52편(전문 33, 초록만 19, 대목 2,894개)을 개발용 저장소(`data/papers`, git에 넣지 않음)에 넣고 찾기를 손으로 확인함. 글은 Europe PMC의 공개 XML에서 기계로 옮긴 것이고(식은 빠짐, 표는 제목만), EMA 문서는 PDF에서 CHMP 의견 부분만. 구하지 못한 것: 출처 12번(초록도 공개되지 않음), 24번의 Schootemeijer 2020(주소에 없음). 원문 파일(PDF)은 넣지 않아 `papers.py check`는 논문마다 `no_source`를 냄.
1. `papers.py add`로 넣은 논문이 목록·원문·대목에 있음. `papers.py build`로 색인을 지우고 다시 만들면 같은 검색 결과.
2. 지표 하나로 찾은 대목이 모두 그 지표의 `sources` 논문에서 옴. `sources`가 빈 지표는 대목 없음.
3. `papers.py check`: 지표 목록의 모든 `sources` key가 논문 목록에 있고 원문 파일이 있음.

**M11 보고서 (RP-1, RP-4, RP-7–RP-9)** — 2026-11-11 대화 코어, 맥락, 검사, 보고서 조립, 명령줄 입구를 가짜 모델 시험으로 만듦. 진짜 모델 호출은 한 번도 보내지 않았음(키 없음). 작업·저장·앱 API(아래 1·3·5번의 저장과 API 부분)는 아직.
1. 가짜 글 쓰기(`fake_writer.py`, 정해진 글을 돌려줌) → 보고서 `done`, 입력 파일(sha256이 행과 같음), 글 파일, run·인용 행. 글의 자리 표시가 값과 단위로 바뀜. `view=basic` 조회는 요약 부분과 `basic` 값만 줌.
2. RP-8의 검사를 어기는 글마다 → 한 번 다시 시도, 또 어기면 `failed`, 행과 파일 없음.
3. 걷기가 없는 기간 → `skipped`(`no_walking`). 풀리지 않은 run이 있으면 미뤄짐.
4. 지난 30일에 충분한 날이 7일 이상이면 `baseline`이 붙고, 아니면 없음. 하루보다 짧은 기간은 `baseline_comparable = false`(RP-4).
5. 앱 API: 정상, 남의 착용자(404), 잘못된 기간(422). 같은 기간을 두 번 요청하면 보고서 둘이 남음.
6. 진짜 글 쓰는 모델로 합성 보행 하루치 보고서 하나를 만들어 사람이 읽고 확인(수치, 인용, 주의 문구).

**순서.**

| 순서 | 저장소 | 일 | 막히는 것 |
|---|---|---|---|
| 11 | 서버 | **M7** 지표 목록(MC-8의 33개), `pipeline/metrics.py`, 계약 시험 | — |
| 12 | gaitfusion | `summary.formats`, 지표 목록 복사와 자기 출력 검사. service.md·gait-features.md가 지표 목록을 가리키게 고침 | M7 |
| 13 | 서버 | **M8** 특징 풀기, 지난 run 채우기 | M7 |
| 14 | 서버 | **M9** 기간 계산, 일별 값, 추세 조회 | M8 |
| 15 | 서버 | **M10** 논문 저장소와 도구. 논문 원문 모으기(사람) | 대목을 수치로 바꾸는 모델 고르기 |
| 16 | 서버 | **M11** 보고서 쓰기와 앱 API | M9, M10, 글 쓰는 모델 고르기 |
| 따로 | gaitfusion | 회전·창·삽화·보폭의 남은 열. 나오는 대로 서버는 고칠 것 없이 받아 두고, 풀이에 쓸 때 지표 목록에 항목을 더함 | 서버 순서와 무관 |

지금 시험할 수 있는 것은 합성 데이터와 gaitfusion이 이미 내는 보폭 12열까지입니다. 회전·창·삽화는 gaitfusion이 내기 시작하면 받아 두었다가 지표 목록에 더하고, 실제 보드 데이터가 들어오면 같은 시험에 그 데이터를 더합니다.

## 15. 정할 것과 확인할 것

정할 것(사용자):
1. 호스팅 업체와 리전.
2. 도메인 유무(있으면 Caddy 자동 인증서, 없으면 자체 서명).
3. 앱 기술 스택(API는 OpenAPI로 무관).
4. 원격 백업 저장소: S3 호환 오브젝트 스토리지(버전 보존 기능) 또는 두 번째 머신.
5. 30일 삭제를 켜는 때(AR-7의 조건을 언제 확인할지).
6. 보관한 범위 안의 빈자리를 늦은 묶음이 메운 경우(예: 해독기를 고친 뒤 IN-10으로 다시 넣은 묶음). `archive.range`는 seq 구간이고 겹침을 막으므로(AR-3) 그 묶음을 보관 파일에 넣을 길이 없습니다. 지금은 AR-6의 `archive_incomplete` 조건으로 그 구간의 raw를 지우지 않고 남깁니다. 정할 것: 보충 범위를 허용할지(겹침 규칙을 바꿈), 아니면 지금처럼 raw를 남길지.
7. ~~gaitfusion의 `status: "partial"` 응답. gaitfusion은 자기 마감 시각(540초) 안에 못 끝낸 발의 궤적을 빼고 `result.status = "partial"`로 답합니다(걷기 구간 목록은 그대로). 1단계 서버는 이 필드를 보지 않으므로(7.2절 표에 없음) 궤적이 빠진 run이 `done`이고 "현재"가 됩니다. 걷기 원본은 걷기 구간 시각으로 보관하므로 잃지 않습니다. 정할 것: 이 표시를 run에 저장해 앱에 보일지, 나중에 자동으로 다시 분석할지(gaitfusion은 같은 요청의 답을 15분 동안 다시 쓰므로 그 뒤에), 아니면 지금처럼 둘지.~~ 정함(2026-11-10): run에 저장해 앱에 보이고, 자동으로 다시 분석하지 않습니다. `analysis.run.result_status`(`008`), 7.2절 표의 `status`, AN-7, 8절. `partial` run도 `done`이고 현재 run이 됩니다. 다시 분석하려면 재분석 요청(AN-9)을 씁니다.
8. ~~(2단계) 글 쓰는 모델과 그 자리.~~ 정함(2026-11-11): 바깥 API를 씁니다. 착용자 코드와 지표 값이 바깥으로 나가는 것은 문제 삼지 않습니다(RP-8). 모델은 Claude Opus 5.5(`claude-opus-5-5`)입니다. API 키는 아직 넣지 않았고, 넣기 전까지는 가짜 모델로 시험합니다.
9. ~~(2단계) 논문 대목의 검색 색인.~~ 정함(2026-11-11): 논문은 수백 편 이하이므로 파일로 된 색인을 프로세스 안에서 읽습니다(RP-5). 찾기는 단어 맞춤(BM25)과 벡터를 섞습니다(RP-6). 벡터 쪽의 진짜 모델은 아직 고르지 않았습니다: Anthropic에는 이 용도의 모델이 없어 다른 회사의 키가 따로 필요합니다. 그때까지 벡터 쪽은 글자 조각 해시 대역입니다.
10. (2단계) 논문 원문을 저장하고 대목을 보고서에 옮겨 적어도 되는 범위. 논문은 모았고(M10), 논문마다 받은 곳과 사용 허락을 `papers.toml`의 `use`에 적어 두었습니다. 정할 것: 전문 33편 중 30편은 CC BY이고, 셋은 조건이 더 붙습니다(`arens2021` CC BY-NC-SA, `yarnall2026` CC BY-NC, `lanotte2024` CC BY-NC-ND). 초록만 넣은 19편은 공개 허락이 없는 학술지의 것입니다. 지금은 개발용 PC 안에서만 씁니다. 보고서와 앱 API가 대목 글을 그대로 내보내기 전에(RP-9, 14.8절) 이 셋과 초록의 대목을 내보내도 되는지 정합니다.
11. ~~(2단계) 추세와 지난 기간 견주기.~~ 정함(2026-11-11): 앱에 추세를 보일 수 있게 일별 값을 2단계에서 만들고(14.6절), 보고서는 지난 30일과 견준 값을 함께 냅니다(RP-4).

확인할 것(구현 중):
1. 권장 VM 디스크의 `fdatasync` 시간(9절). 20 ms를 넘으면 디스크 종류를 바꿉니다.
2. ~~gaitfusion이 수 분짜리 seq 빈자리(archive 입력)를 `head_us_start`로 잇는지(7.1절).~~ 확인함(2026-10-30, gaitfusion `2c03531`): 걷기 사이 약 5분을 쉰 양발 기록에서, 보관본으로 다시 분석한 걷기 구간 시각이 원본 분석과 µs까지 같고 궤적 값 차이는 1e-8 이하. M3-9가 같은 것을 확인합니다.
3. ~~1시간 구간 분석이 `GAITFUSION_TIMEOUT_S`(10분) 안에 끝나는지.~~ 확인함(2026-10-30, gaitfusion `2c03531`, 작업 프로세스 2개, 개발 PC): 양발로 1시간 내내 걸은 구간(묶음 3601개, 25 MB)이 52초. 메모리를 줄인 `8010070`도 약 55초. 실제 VM에서 다시 잽니다.
