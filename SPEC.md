# uwb-health-server 스펙

이 서버가 무엇을 해야 하는지 정한 문서입니다. 이 문서만 읽고 서버를 만들고 검사할 수 있게 썼습니다. 예전 `server-architecture.md`(계획)와 `server-schema.md`(DDL)를 합친 것이고, 두 파일의 마지막 내용은 커밋 `727d0aa`에 있습니다.

## 0. 읽는 법

- **필수와 선택**: "합니다", "해야 합니다", "하지 않습니다"는 필수입니다. "할 수 있습니다"는 선택입니다.
- **요구사항 번호**: `IN-3`처럼 영역 머리글자와 번호를 붙였습니다. 시험(11절)과 코드 주석에서 이 번호로 가리킵니다. PL 공용, RG 사람 입력, IN 받기, SG 구간, AN 분석 호출, AR 원본 보관, AP 앱 API, TL 도구, OP 운영입니다.
- **범위**: 이 문서의 1–13절이 **1단계**이고 지금 만듭니다. 14절은 **2단계 초안**이고 만들지 않습니다(1.2절).
- **다른 문서**: 이 서버는 세 문서와 계약을 맺습니다. 문서가 서로 다르면 계약 문서가 이깁니다.

| 문서 | 무엇 | 소유 |
|---|---|---|
| [wire.md](../uwb-firmware/docs/wire.md) | C1: Head → 서버 바이트 형식, 응답, 검사 규칙, 시험 벡터 | 펌웨어 저장소 |
| 이 문서 7절 | C2: 서버 → gaitfusion 요청·응답 | 이 저장소 |
| 이 문서 8절 | C3: 앱 → 서버 REST API | 이 저장소 |
| [gait-features.md](../gaitfusion/gait-features.md) | C2 응답 안의 분석 특징 필드(2단계) | gaitfusion 저장소 |

## 1. 목표와 범위

### 1.1 하는 일

집이나 병실에 UWB 앵커 4개(Head A0와 A1–A3)를 설치하고, 착용자가 발목 Tag(1개 기본, 2개는 빌드 선택)를 찬 채 생활합니다. Head는 1초마다 UWB 거리와 Tag IMU를 바이너리 묶음으로 묶어 이 서버에 HTTPS로 올립니다. 서버는 이것을 잃지 않고 받아, 분석할 수 있는 단위(구간)로 정돈하고, 분석 서비스(gaitfusion)에 보내 걷기 구간과 발 궤적을 받아 둡니다.

### 1.2 단계

| 단계 | 내용 | 이 문서 |
|---|---|---|
| **1단계: 원본 정돈** | 받기, 장치·착용자 등록, 구간 만들기, gaitfusion 호출과 응답 보관(걷기 구간의 시간 범위, 궤적, 응답 원문), 걷기 원본 영구 보관, 30일 지난 원본 삭제, 이것들을 보고 고치는 API | 1–13절. 지금 만듦 |
| **2단계: 분석 특징** | 응답의 보폭·걸음·회전·창·삽화를 표로 풀기, 일별 요약, 선별 지수, 앱의 요약 조회 | 14절 초안. gaitfusion의 특징이 정해지면 별도 스펙으로 확정 |

경계는 이렇습니다. 1단계 서버는 gaitfusion 응답 중 **걷기 구간이 언제였는지, 어느 발이었는지, 궤적 파일**만 해석합니다. 나머지 필드는 해석하지 않고 응답 원문 파일로 남깁니다. 그래서 2단계는 gaitfusion을 다시 돌리지 않고도 남긴 원문에서 표를 채울 수 있습니다.

### 1.3 목표와 성공 기준

| # | 목표 | 성공 기준 |
|---|---|---|
| G1 | **원본을 잃지 않고 쌓는다** | 수십 초 인터넷 끊김에서 묶음 손실 0. 더 긴 끊김은 빠진 묶음 수가 구간 표에 숫자로 보임. 서버 디스크가 죽어도 잃는 원본은 최근 10분 이하. 걷기 구간 원본은 영구 보관되어 언제든 재분석 가능 |
| G2 | **원본을 분석 단위로 정돈한다** | 구간마다 빈자리 수, 찬 발과 Tag, 앵커 배치, 시각 기준이 표에 있음. 같은 묶음 집합에서는 언제 받든 같은 구간이 나옴(13절 예외 하나) |
| G3 | **분석 서비스를 부르고 결과를 남긴다** | 구간이 닫힌 뒤 30분 안에 run, 걷기 구간, 궤적, 응답 원문이 저장됨. 다시 분석해도 이전 run이 남고 "현재"는 가장 최근 run |

지금 대상은 팀원 시험입니다. 실제 대상자로 넘어갈 때의 요건은 13절에 있습니다.

### 1.4 하지 않는 것

- 보폭·일별 요약·지수 계산(2단계).
- Head 펌웨어의 전송부(펌웨어 저장소의 일). 1단계는 가짜 Head(TL-1)로 검사합니다.
- gaitfusion 서비스 구현(gaitfusion 저장소의 일). 1단계는 가짜 gaitfusion(TL-2)으로 검사하고, 진짜 서비스가 나오면 같은 시험을 돌립니다.
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
| **registry** | 사람이 넣는 것: 장치·착용자·착용·계정 | api | — | `registry.*` |
| **pipeline** | 공용: DB·파일·작업 큐·읽기 위치·시계·설정·마이그레이션 | 전부 | — | `pipeline.*` |

- **쓰기는 자기 스키마만.** 그 밖에는 `pipeline.job`에 작업을 넣는 것뿐입니다. 예외 하나: archive가 30일 지난 `intake` 행과 raw·rejected 파일을 지웁니다. intake·api는 DB 역할로, core 안의 세 단계는 시험(`tests/test_ownership.py`)으로 지킵니다.
- **읽기는 SQL로 어느 스키마든** 됩니다.
- **코드 import는 아래로만**(3.2절). 흐름 단계 넷은 서로 import하지 않습니다.

## 3. 저장소와 코드 구조

### 3.1 기술

Python 3.14, FastAPI + uvicorn, psycopg 3, httpx, pytest, import-linter, argon2-cffi, PostgreSQL 17, Caddy 2, Docker Compose. 버전은 구현을 시작할 때 `pyproject.toml`과 `compose.yaml`에 고정합니다.

Python 버전, 의존성, 가상 환경, 실행은 모두 uv 하나로 관리합니다. 버전은 `.python-version`, 의존성은 `pyproject.toml`(정확한 버전)과 `uv.lock`에 둡니다. 명령은 `uv sync`, `uv run pytest`, `uv run python tools/<도구>.py`처럼 씁니다. Docker 이미지도 `uv sync --frozen`으로 같은 잠금 파일에서 설치합니다.

### 3.2 폴더

```
uwb-health-server/
  SPEC.md  README.md
  pyproject.toml  compose.yaml  compose.dev.yaml  Caddyfile  .env.example  Dockerfile
  migrations/   001_pipeline.sql 002_registry.sql 003_intake.sql 004_segment.sql
                005_analysis.sql 006_archive.sql 007_roles.sql          ← 10절 그대로
  src/uwbserver/
    pipeline/   db.py files.py jobs.py clock.py settings.py migrate.py wire.py log.py alert.py
    registry/   rules.py store.py routes.py access.py
    intake/     rules.py store.py run.py routes.py
    segment/    rules.py store.py run.py routes.py
    analysis/   rules.py store.py run.py routes.py gaitfusion.py
    archive/    rules.py store.py run.py
    process/    intake.py core.py api.py
  tests/        pipeline/ registry/ intake/ segment/ analysis/ archive/
                contract/wire_vectors/  contract/gaitfusion/  e2e/  test_ownership.py
  tools/        fake_head.py fake_gaitfusion.py reindex_raw.py verify_archive.py
                reingest_rejected.py admin.py import_usb.py bench.py
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
- `pipeline/`: `db.py`(연결·트랜잭션·잠금), `files.py`(5.2절 파일 형식), `jobs.py`(작업 넣기·가져가기·끝·실패·되살리기, 읽기 위치), `clock.py`(시험에서 바꿔 끼우는 시계), `settings.py`(4.3절), `migrate.py`, `log.py`(PL-5 한 줄 JSON), `alert.py`(OP-2 알림).
- 모든 DB 연결은 autocommit이고, 쓰기는 늘 `with conn.transaction():` 블록 안에서 합니다. 블록이 끝나야 커밋되므로 받기 응답은 커밋 뒤에 나갑니다.
- `analysis/gaitfusion.py`: C2 HTTP 클라이언트. `registry/access.py`: 로그인 확인과 권한 검사.
- `process/`: 프로세스 셋의 조립 지점. `intake.py` = intake의 `device_router` + 시작 때 재색인(IN-9). `core.py` = 5초 루프(`segment.run.step`) + 작업 스레드(작업 종류 → `analysis`·`archive`의 `run.handle`) + 매일 `archive.run.purge`. `api.py` = 모든 단계의 `app_router`.

**import 규칙.** 아래로만 import하고 CI에서 import-linter로 막습니다. `tools/`는 무엇이든 import할 수 있습니다.

```
process ──▶ intake · segment · analysis · archive ──▶ registry ──▶ pipeline
                    (넷은 서로 import 금지)
```

```ini
[importlinter]
root_package = uwbserver

[importlinter:contract:layers]
name = 아래로만
type = layers
layers =
    uwbserver.process
    uwbserver.intake | uwbserver.segment | uwbserver.analysis | uwbserver.archive
    uwbserver.registry
    uwbserver.pipeline

[importlinter:contract:rules]
name = rules.py는 바깥을 모름
type = forbidden
source_modules = uwbserver.*.rules
forbidden_modules = psycopg httpx fastapi uwbserver.pipeline.db uwbserver.pipeline.files uwbserver.pipeline.jobs
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
| `core_rw` | 전부 읽기. `segment.*` INSERT·UPDATE, `analysis.*` INSERT(궤적 행만 DELETE), `archive.*` INSERT, `pipeline.job` INSERT·UPDATE·DELETE, `pipeline.cursor` UPDATE, `intake.chunk`·`health`·`system`·`rejected` DELETE(30일 삭제만) |
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
  - 같은 대상의 `queued`·`running` 작업은 하나뿐입니다(부분 고유 색인). 넣기는 `ON CONFLICT (…) WHERE … DO NOTHING`입니다. 작업이 끝나며 같은 대상의 다음 작업을 넣을 때는 같은 트랜잭션에서 자기를 먼저 `done`으로 바꾼 뒤 넣습니다.
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

- **IN-1 엔드포인트.** `POST /d/v1/chunks` 하나입니다. 요청과 응답의 바이트, 결과 코드, HTTP 상태는 [wire.md](../uwb-firmware/docs/wire.md) "전송"을 그대로 따릅니다.
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
- **SG-6 Tag와 발.** 묶음의 `tag_mask`에 있는 발 자리(bit0 = `left`, bit1 = `right`)마다 `segment_tag` 행이 없으면 넣습니다(`first_seen_at = c.received_at`). MAC은 `intake.chunk.tag0_mac`/`tag1_mac`에서 오고, 거리 프레임만 있던 발은 NULL로 넣었다가 그 발의 IMU 프레임이 처음 오면 채웁니다. 이미 MAC이 있는 행은 바꾸지 않습니다(다르면 P 뒤에서는 `tag_changed`, P 안쪽에서는 경고). `sides`는 `segment_tag` 행으로 정합니다(`left`·`right`·`both`).
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
| 200 | 7.2절 검사 → 결과 원문과 궤적 파일 쓰기(임시 이름 → fsync → 이름 바꾸기) → 한 트랜잭션: run(`done`)·bout·trajectory INSERT(COPY), 작업 `done`, `archive {run_id}` 작업 넣기, 보낸 뒤 구간 `revision`이 바뀌었으면 `analysis` 작업 다시 넣기 → 커밋 뒤 같은 구간의 옛 run 궤적 파일과 행 삭제 |
| 200인데 검사 실패 | 작업 `failed`(재시도 안 함), `last_error`에 어느 검사인지. 쓴 파일은 지움 |
| 422 | 한 트랜잭션: run(`skipped`, `skip_reason` = 응답의 이유), 작업 `done`, `archive {run_id}` 작업 넣기 |
| 400 | 작업 `failed`(재시도 안 함). 서버가 보낸 바이트가 C1을 어긴 것이므로 버그 |
| 5xx, 시간 초과, 연결 실패 | 재시도(PL-2). 쓴 파일은 지움 |

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
- **AP-3 상태 블록 풀기.** health·system·survey 조회는 `block` 바이트를 `pipeline/wire.py`로 풀어 wire.md의 필드 이름 그대로 JSON으로 줍니다.
- **AP-4 궤적 읽기.** TRJ1 파일의 머리를 읽고, 채널마다 시작 위치 `24 + 4n·k`에서 `rate`에 맞게 건너뛰며 읽습니다(25 Hz = 4개마다 1개). 해석이나 압축 해제는 하지 않습니다. 캐시가 지워졌으면 404입니다.
- **AP-5 쓰기 뒤 작업.** api가 넣는 작업은 재분석(`analysis`) 하나입니다. 착용 기록을 고쳐도 1단계에서는 작업을 넣지 않습니다(귀속은 조회 때 정해지므로).

### 6.8 도구 (TL)

- **TL-1 `fake_head.py`.** 합성 보행으로 묶음을 만들어(서버의 `pipeline/wire.py`가 아닌 별도의 인코더. 시험 벡터와 바이트가 같아야 함) intake에 보냅니다. 시나리오: 보통, 1-Tag·2-Tag, 인터넷 끊김(버퍼에 쌓았다 몰아 보냄), 버퍼 넘침(빈자리), 같은 요청 다시 보내기, 재부팅, Tag 교체, 배치 변경, T1이 중간에 나타남, Tag 꺼짐(프레임 없는 묶음만), 잘못된 묶음 각 종류. 장치 여러 대를 동시에 흉내 낼 수 있습니다.
- **TL-2 `fake_gaitfusion.py`.** C2를 지키는 가짜 서비스입니다. 받은 묶음을 해독해, IMU 프레임이 10초 이상 이어진 부분마다 걷기 구간 하나와 발마다 TRJ1 궤적 하나를 만듭니다(값은 결정적인 합성값, CRC는 맞음). 환경 변수로 422, 500, 느린 응답, 형식이 틀린 응답을 낼 수 있습니다.
- **TL-3 `reindex_raw.py [--since 시각] [--device]`.** raw 파일 → `intake.*` 행. 키가 이미 있으면 건너뜁니다. IN-9가 같은 코드를 씁니다.
- **TL-4 `verify_archive.py [--reindex]`.** archive 파일의 sha256을 `archive.range`와 대조합니다. `--reindex`는 파일에서 빠진 행을 다시 만듭니다.
- **TL-5 `reingest_rejected.py`.** IN-10.
- **TL-6 `admin.py`.** `create-admin`, `register-device <mac>`(토큰 출력), `rotate-token`, `reanalyze <segment_id>`. API와 같은 store 함수를 씁니다.
- **TL-7 `import_usb.py`.** 펌웨어의 USB 레코더 기록(Head `range`·`imu_batch`·`system` JSONL, [protocol.md](../uwb-firmware/docs/protocol.md))을 1초 묶음으로 바꿔 intake에 보냅니다. Head 전송부가 생기기 전에 실제 기록을 넣는 길입니다. health 블록은 만들지 않습니다. M3 뒤에 만듭니다.
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

이 저장소가 소유하고, gaitfusion 저장소가 구현합니다. 바꿀 때는 이 절을 먼저 고치고 `c2_version`을 올립니다. 응답 `bouts[]` 안의 분석 특징 필드(보폭, 창 등)의 뜻은 [gait-features.md](../gaitfusion/gait-features.md)가 정하고, 1단계 서버는 그 필드를 해석하지 않습니다.

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
  "sides": "left",
  "anchors": [{"id": 0, "xyz_m": [0.0, 0.0, 1.2], "bias_m": 0.0}, {"id": 1, "xyz_m": [4.1, 0.0, 1.2], "bias_m": 0.0},
              {"id": 2, "xyz_m": [3.9, 3.2, 1.2], "bias_m": 0.0}, {"id": 3, "xyz_m": [0.1, 3.1, 0.4], "bias_m": 0.0}],
  "subject": {"height_m": 1.72, "leg_length_m": 0.90, "paretic_side": null},
  "params": {"stance_threshold": null, "stance_window": null, "bout_min_strides": 4, "bout_max_pause_s": 3.0, "bout_pad_s": 2.0}
}
```

- `anchors`가 null이면 IMU 단독 모드입니다. `subject`는 null일 수 있고, 그 안의 각 값도 null일 수 있습니다.
- 발은 묶음 프레임의 `tag_id`로 서비스가 정합니다(0 = 왼쪽, 1 = 오른쪽). `sides`는 구간에 있던 발입니다.
- `params`의 null은 서비스 기본값입니다. 걷기 구간 기준은 **정지와 swing 교대 4보폭 이상, 멈춤 3초 미만**입니다. 멈춤이 3초 미만이면 한 걷기 구간으로 합치므로, 서로 다른 걷기 구간 사이는 3초 이상 떨어집니다.
- `bout_pad_s`(기본 2초)는 **궤적에만** 붙는 앞뒤 여유입니다. 응답의 `start_s`·`end_s`는 여유 없는 걷기 범위입니다.
- 시각 0은 `origin_head_us`입니다. 시각은 표본 수가 아니라 묶음 머리의 `head_us_start`로 이어야 하고, archive 입력에서는 seq 빈자리가 수 분일 수 있습니다.
- `t_last` = `(보낸 마지막 묶음의 head_us_start − origin_head_us) / 10^6 + 1`초 = 보낸 데이터가 끝나는 시각입니다. 7.2절 검사에 씁니다.

### 7.2 응답 (200, `multipart/form-data`)

요청과 같은 형식이라 같은 해독기를 씁니다. 파트마다 `Content-Disposition: form-data; name="<이름>"`과 `Content-Type`이 있습니다. 첫 파트는 이름 `result`(`application/json`)이고 궤적을 뺀 결과 전부입니다. 이어서 걷기 구간·발마다 궤적 파트 하나(`application/octet-stream`, 이름 `traj-<bout idx>-<side>`, 예 `traj-0-left`, 7.3절 TRJ1)가 옵니다. 궤적을 숫자 글자로 쓰면 1시간 구간에 약 15 MB라 JSON에 넣지 않습니다.

```json
{
  "c2_version": 1,
  "gaitfusion_version": "1.3.0+gabc1234",
  "summary": {"n_imu": 359800, "n_range": 358100, "seq_gaps": 3, "sides": ["left"], "sensor_site": "lateral_ankle"},
  "bouts": [{
    "start_s": 812.40, "end_s": 871.15, "mode": "fused", "sides": "left",
    "quality": {"uwb_accept_ratio": 0.93},
    "trajectories": [{"side": "left", "part": "traj-0-left"}],
    "strides": [], "turns": [], "steps": [], "windows": [], "events": []
  }]
}
```

1단계 서버가 쓰고 검사하는 필드는 아래뿐입니다. 하나라도 어기면 AN-7의 "검사 실패"입니다.

| 필드 | 규칙 |
|---|---|
| `c2_version` | 1 |
| `gaitfusion_version` | 비지 않은 문자열 |
| `summary` | JSON 객체. 그대로 `analysis.run.summary`에 |
| `bouts` | 배열(비어도 됨). `start_s` 오름차순. 앞 걷기 구간의 `end_s` < 다음 `start_s` |
| `bouts[].start_s`, `end_s` | 유한한 수. 0 ≤ `start_s` < `end_s` ≤ `t_last`(7.1절) |
| `bouts[].mode` | `fused` 또는 `imu_only`. 보낸 `anchors`가 null이면 `imu_only`만 |
| `bouts[].sides` | `left`·`right`·`both`, 구간 `sides`의 부분(`both`인 구간은 셋 다 가능, `left`인 구간은 `left`만) |
| `bouts[].quality` | JSON 객체 |
| `bouts[].trajectories` | 배열. 항목의 `side`는 그 걷기 구간의 발에 들고 발마다 하나 이하. `part`는 `traj-<그 bout의 배열 순번>-<side>` |
| 궤적 파트 | 7.3절 검사를 통과. 머리의 `side`가 이름과 같음. `0 ≤ t0_s`, `t0_s + (n − 1) / rate_hz ≤ t_last`. 모든 궤적 파트는 정확히 하나의 `trajectories` 항목이 가리키고, 모든 항목의 파트가 응답에 있음 |
| 파트 | 이름이 `result`이거나 위 규칙의 궤적 이름. 같은 이름이 둘이면 안 됨 |

나머지 필드(`strides`, `turns`, `steps`, `windows`, `events`와 앞으로 더해질 것)는 있어도 없어도 되고, 1단계는 결과 원문 파일에만 남깁니다. 2단계에서 표로 풉니다(14절).

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
| `GET /v1/segments?device=&from=&to=` · `GET /v1/segments/{id}` | 구간 목록 · 구간 상세(Tag, 배치, 빈자리, run 목록) | admin | segment |
| `POST /v1/segments/{id}/reanalyze` | 재분석 작업 넣기 → 202 `{segment_id, queued}`(이미 대기 중이면 `queued = false`). raw도 archive도 없으면 409 | admin | segment |
| `GET /v1/runs/{id}` · `GET /v1/runs/{id}/result` | run과 걷기 구간 목록 · 결과 원문(gzip 그대로, `Content-Encoding: gzip`) | admin | analysis |
| `GET /v1/subjects/{id}/bouts?from=&to=` | 현재 run의 걷기 구간 목록(시작, 끝, 발, 모드). 귀속은 SG-9 | 본인, admin | analysis |
| `GET /v1/bouts/{id}/trajectory?side=&rate=25` | 한 발 또는 있는 발 전부의 궤적. `rate` 25(기본)·50·100. 응답은 `{bout_id, trajectories: [{side, rate_hz, t0, n, channels: {px: [...], ...}}]}`(발이 하나여도 배열). 계산 못 한 표본(NaN)은 `null` | 본인, admin | analysis |

## 9. 속도 요구

값은 권장 VM(2 vCPU, 4 GB RAM, SSD) 기준 p95 목표이고 아직 재지 않았습니다. 네트워크와 TLS 시간은 빼고, PL-5 로그로 잽니다(TL-8). 표나 조회를 바꿀 때 오른쪽 열의 방법을 깨지 않습니다.

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

`migrations/001–007`로 그대로 옮깁니다. 파일 하나 = 단계 하나 = 스키마 하나입니다. 이 DDL은 PostgreSQL 17에 오류 없이 적용됨을 확인했습니다.

공통 규칙:
- **단위**: SI. 열 이름 끝에 단위(`_m`, `_s`, `_us`, `_ms`, `_hz`). 접미가 없는 비율은 0–1.
- **NULL은 "계산 못 함" 또는 "아직 모름" 하나의 뜻**입니다. 0으로 대신 쓰지 않습니다. 데이터가 아예 없는 것은 행이 없는 것으로 나타냅니다. 예외: `intake.chunk`의 머리 복사 열(`utc_us_start` 등)은 wire 값을 그대로 담으므로 wire의 뜻(예 `0` = 추정 없음)을 따릅니다. 블록에서 뽑은 조회 열은 이 규칙을 따릅니다(예 `range_age_ms`의 wire 0xFFFF → NULL).
- **발(`side`)**: `'left'`·`'right'`. 묶음 프레임의 `tag_id` 0 = 왼쪽(T0), 1 = 오른쪽(T1)이 정하고 사람이 입력하지 않습니다. 1-Tag 세트는 T0를 왼쪽 발목에 찹니다.
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
  tag_mask           smallint    NOT NULL,      -- wire 머리 tag_mask. bit0 T0(왼쪽), bit1 T1(오른쪽) 프레임 있음
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
  side          text        NOT NULL CHECK (side IN ('left','right')),   -- tag_id 0 = left, 1 = right
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
   보통 2시간 → `max_length`로 2개 · Tag 꺼짐 2분(실시간으로 흘러감) → 앞 구간은 `silence_60s`, 다시 켜진 뒤 새 구간 · 인터넷이 끊긴 동안 Tag 꺼짐 2분(밀린 묶음이 한꺼번에 옴) → `gap_60s` · 재부팅 → `boot_changed` · 옛 부팅의 묶음이 늦게 옴 → 옛 구간만 다시 열리고 새 부팅의 구간은 열린 채 · 왼발 Tag 교체 → `tag_changed` · 중간에 T1 나타남 → 한 구간, `both`, 행 2개 · 배치 변경 → `geometry_changed`, 새 구간의 `installation_id`가 만들 때부터 있음 · 버퍼 넘침 → 빈자리 수 · 프레임 없는 묶음만 → 구간 없음.
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
| 7 | gaitfusion | `service.py`(C2), `wire.py`, 걷기 구간 찾기, 궤적 TRJ1, 이미지. 나오면 M3의 시험 7을 진짜 서비스로 | 서버 M3과 따로 진행 |
| 8 | 펌웨어 | Head TLS 메모리 확인(1초마다 5 kB POST를 keep-alive로 보내며 힙과 수집이 버티는지) → Head 전송부(묶음 인코더, PSRAM 링 버퍼, HTTPS, 재전송, UTC 추정, health 블록) | 실제 Head로 받기 |
| 9 | 서버 | `import_usb.py`로 실제 기록 넣기 | 서버 M3 |
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
| gaitfusion 메모리 | 10분 창 하나에 약 0.3 GB |
| 권장 VM | 2 vCPU, 4 GB RAM, SSD 250 GB |

실제 착용 시간은 Tag 전원에 달려 있어 이보다 훨씬 짧을 가능성이 큽니다.

### 13.3 알려진 한계

- **구간 결정성의 예외**: 늦게 온 묶음이 이미 갈라진 두 구간 사이를 이어도 구간을 합치지 않습니다(SG-3). Head는 오래된 묶음부터 보내므로 드뭅니다.
- **UTC**: Head의 UTC 추정 오차는 ±30 ms입니다. 부팅 뒤 서버에 한 번도 닿지 못한 동안의 묶음은 도착 시각 기준이라, 끊김이 길면 그 구간의 시각이 끊김만큼 늦게 찍힙니다(`time_source = 'arrival'`로 표시).
- **30일 뒤**: 걷기로 판정되지 않은 시간의 원본은 사라집니다(5.3절).
- **IMU와 앵커의 묶음 사이 검사**: 받을 때는 보지 않습니다(IN-8). IMU 프레임의 `tag_mac`이 세트 목록에 없는 경우 등은 지금 경고 로그도 없습니다. 2단계 전에 segment에 넣을지 정합니다.

## 14. 2단계 초안: 분석 특징 (만들지 않음)

gaitfusion의 특징 목록([gait-features.md](../gaitfusion/gait-features.md))이 정해지면 이 절을 별도 스펙으로 확정합니다. 1단계와의 약속은 셋입니다.
- 2단계는 1단계 표를 바꾸지 않고 **표를 더합니다**(새 마이그레이션 008부터).
- 특징 표는 결과 원문 파일(AN-10)에서 채울 수 있어야 합니다. 그래서 2단계를 켜는 날 gaitfusion을 다시 돌리지 않고 지난 run을 채울 수 있습니다(같은 `c2_version`일 때).
- 2단계 표도 "결과는 추가만", "선택적인 것은 행으로"를 따릅니다.

### 14.1 목표

| # | 목표 | 성공 기준 |
|---|---|---|
| G4 | 보행을 보폭 단위로 재구성 | 구간이 닫힌 뒤 30분 안에 보폭·회전·10초 창·삽화 표가 DB에 있음 |
| G5 | 신경학적 보행 이상(파킨슨, 뇌졸중 등)의 선별 지수와 개인 기준선 대비 변화 | 일별 요약에서 7일 중앙값과 30일 기준선 차이를 계산할 수 있음. 진단이 아님 |
| G6 | 앱에 일·주 단위로 | 앱이 요청 하나로 착용자의 일별 요약 90일을 받음 |

### 14.2 더할 것

- **응답 필드**: `bouts[].strides`·`turns`·`steps`(양발일 때)·`windows`·`events`, `summary.method_versions`. 뜻은 gait-features.md 3–8절. 걸음 항목은 `{"idx", "side", "start_s", "end_s", "step_time_s", "step_length_m", "step_width_m", "double_support_s", "single_support_s", "uwb_support"}`. 한 발이면 `steps`는 빈 배열이고, `events[].side`는 한 발 신호로 판정했으면 그 발, 양발을 함께 봤으면 null.
- **작업**: `summary {device_id, from, to}`. analysis는 run이 끝나면 구간 기간으로, api는 착용 기록을 만들거나 고치면 고치기 전·후 기간을 합쳐서 넣습니다. 그 기간의 날짜마다, 그 장치에 겹치는 착용 기록이 있는 착용자 전원을 다시 계산합니다.
- **규칙**:
  - 걷기 구간 집계: 케이던스 = 2 × 분당 보폭. 거리 = 보폭 길이 합. 변동성(CV, SD, 표본 엔트로피)은 30초 이상 구간에서만.
  - 일별 요약: `Asia/Seoul` 날짜에 시작한 현재 run의 걷기 구간. 자정을 넘는 것은 시작한 날. 속도·보폭 길이는 구간 길이별(10–30초, 30–60초, 60초 이상)로 따로. `wear_hours < 12`면 `data_sufficient = false`. 다시 계산하면 그 착용자·날짜의 세 표 행을 한 트랜잭션에서 지우고 새로 넣습니다.
- **API**: `GET /v1/subjects/{id}/daily?from=&to=`(일별 요약), `GET /v1/bouts/{id}`(발별 보폭, 양발 걸음, 회전, 삽화).
- **속도**: 일별 요약 다시 계산은 착용자 하루 10 ms 이하(그날 bout의 stride·step을 `(bout_id, …)` 색인으로만 읽음). 결과 쓰기는 run 하나 200 ms 이하(표마다 COPY 한 번).

### 14.3 DDL 초안

1단계 DDL 위에 오류 없이 적용됨을 확인했습니다. 열의 뜻과 근거는 gait-features.md입니다.

```sql
-- 008_analysis_features.sql (초안)
ALTER TABLE pipeline.job DROP CONSTRAINT job_kind_check;
ALTER TABLE pipeline.job ADD CONSTRAINT job_kind_check CHECK (kind IN ('analysis','archive','summary'));
ALTER TABLE pipeline.job ADD COLUMN device_id text, ADD COLUMN date_from date, ADD COLUMN date_to date;
ALTER TABLE pipeline.job ADD CHECK ((kind = 'summary') = (device_id IS NOT NULL AND date_from IS NOT NULL AND date_to IS NOT NULL));

-- 등급: 결과. 걷기 구간 하나의 집계. bout 하나에 한 행.
CREATE TABLE analysis.bout_stat (
  bout_id         bigint  PRIMARY KEY REFERENCES analysis.bout(id),
  n_strides       integer NOT NULL,             -- 한 발 기준 보폭 수. 양발이면 많은 쪽
  distance_m      real,
  duration_s      real    NOT NULL,
  mean_speed_mps  real,
  cadence_spm     real                          -- 2 × 분당 보폭
);

-- 등급: 결과. 회전. stride.turn_id가 가리키므로 stride보다 먼저.
CREATE TABLE analysis.turn (
  id                bigserial   PRIMARY KEY,
  bout_id           bigint      NOT NULL REFERENCES analysis.bout(id),
  start_at          timestamptz NOT NULL,
  end_at            timestamptz NOT NULL,
  angle_deg         real        NOT NULL,       -- 부호 있음(좌회전 +)
  peak_velocity_dps real,
  mean_velocity_dps real,
  n_strides         integer,
  jerk              real,
  UNIQUE (bout_id, id)                          -- stride의 복합 FK용
);

-- 등급: 결과. 한 발의 보폭 하나(그 발 착지부터 다음 착지까지). 수치 열은 real, NULL = 계산 못 함.
CREATE TABLE analysis.stride (
  id                   bigserial   PRIMARY KEY,
  bout_id              bigint      NOT NULL REFERENCES analysis.bout(id),
  side                 text        NOT NULL CHECK (side IN ('left','right')),
  idx                  integer     NOT NULL,    -- 구간 안, 그 발의 순번 0부터
  start_at             timestamptz NOT NULL,
  end_at               timestamptz NOT NULL,
  duration_s           real        NOT NULL,
  length_m             real,
  speed_mps            real,
  heading_deg          real,
  clearance_m          real,                    -- swing 최대 높이 − 시작 높이
  min_clearance_m      real,                    -- swing 중 최소 발 높이(MTC)
  min_clearance_t_s    real,                    -- 그 시각, 보폭 시작 기준 초
  stance_s             real,
  swing_s              real,
  stance_ratio         real,
  lateral_dev_m        real,                    -- 진행선에서 최대 측방 편차
  pitch_at_strike_deg  real,                    -- 정강이 pitch (발 각도의 대리)
  pitch_at_toeoff_deg  real,
  swing_peak_gyro_dps  real,
  stance_min_gyro_dps  real,
  strike_peak_g        real,
  swing_peak_speed_mps real,
  uwb_support          real        NOT NULL,    -- 0–1. IMU 단독이면 0
  turn_id              bigint,                  -- NULL = 직진
  UNIQUE (bout_id, side, idx),                  -- bout 상세·일별 계산의 조회 색인을 겸함
  FOREIGN KEY (bout_id, turn_id) REFERENCES analysis.turn(bout_id, id)   -- 같은 bout의 turn만
);

-- 등급: 결과. 양발 걸음 하나: 반대 발 착지부터 이 발 착지까지. 양발 bout에만 행이 있음.
CREATE TABLE analysis.step (
  id                bigserial   PRIMARY KEY,
  bout_id           bigint      NOT NULL REFERENCES analysis.bout(id),
  idx               integer     NOT NULL,       -- 구간 안 순번 0부터(발 구분 없이 시간 순)
  side              text        NOT NULL CHECK (side IN ('left','right')),   -- 이 걸음을 끝내며 착지한 발
  start_at          timestamptz NOT NULL,       -- 반대 발 착지
  end_at            timestamptz NOT NULL,       -- 이 발 착지
  step_time_s       real        NOT NULL,
  step_length_m     real,                       -- 착지 순간 두 발 사이, 진행 방향 성분
  step_width_m      real,                       -- 같은 순간, 진행 방향에 수직 성분
  double_support_s  real,                       -- 걸음 시작의 양발 접지 시간
  single_support_s  real,                       -- 반대 발 swing 동안 이 발만 접지한 시간
  uwb_support       real        NOT NULL,       -- 두 발 UWB 수락 비율 중 작은 값. IMU 단독이면 0
  UNIQUE (bout_id, idx)
);

-- 등급: 결과. 걷기 구간 안 10초 창(겹침 50 %), 발마다 그 발의 원시 신호에서 gaitfusion이 계산.
CREATE TABLE analysis.bout_window (
  id                      bigserial   PRIMARY KEY,
  bout_id                 bigint      NOT NULL REFERENCES analysis.bout(id),
  side                    text        NOT NULL CHECK (side IN ('left','right')),
  start_at                timestamptz NOT NULL,
  end_at                  timestamptz NOT NULL,
  freeze_index            real,                 -- P(3–8 Hz) / P(0.5–3 Hz)
  power_locomotor         real,
  power_freeze            real,
  dominant_freq_hz        real,
  dominant_amp            real,
  stride_regularity       real,                 -- 자기상관, 보폭 지연
  harmonic_ratio_ap       real,
  harmonic_ratio_v        real,
  harmonic_ratio_ml       real,
  local_dynamic_stability real,
  sample_entropy          real,
  jerk_rms                real,
  acc_rms_v               real,
  acc_rms_ap              real,
  acc_rms_ml              real,
  gyro_rms                real,
  mean_speed_mps          real,
  n_strides               integer,
  uwb_support             real
);
CREATE INDEX bout_window_bout ON analysis.bout_window (bout_id, side, start_at);

-- 등급: 결과. 삽화. gaitfusion 안의 원시 창 모델 결과.
CREATE TABLE analysis.gait_event (
  id             bigserial   PRIMARY KEY,
  bout_id        bigint      NOT NULL REFERENCES analysis.bout(id),
  side           text        CHECK (side IN ('left','right')),   -- NULL = 양발 신호를 함께 보고 판정
  start_at       timestamptz NOT NULL,
  end_at         timestamptz NOT NULL,
  type           text        NOT NULL CHECK (type IN ('fog')),
  score          real        NOT NULL,          -- 0–1
  method_version text        NOT NULL           -- 'fi-1.0', 'cnn-0.3' …
);
CREATE INDEX gait_event_bout ON analysis.gait_event (bout_id, start_at);

GRANT SELECT, INSERT ON analysis.bout_stat, analysis.turn, analysis.stride, analysis.step,
                        analysis.bout_window, analysis.gait_event TO core_rw;
GRANT SELECT ON analysis.bout_stat, analysis.turn, analysis.stride, analysis.step,
                analysis.bout_window, analysis.gait_event TO api_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA analysis TO core_rw;

-- 009_summary.sql (초안)
CREATE SCHEMA summary;

-- 일별 요약은 표 셋: 사람(daily, 날마다 한 행), 발(daily_side, 그날 데이터가 있던 발만),
-- 양발(daily_bilateral, 양발 bout가 있던 날만). 셋 다 현재 run으로 계산.

-- 등급: 결과. 착용자의 하루, 어느 발을 찼는지와 무관한 값.
CREATE TABLE summary.daily (
  subject_id                bigint  NOT NULL REFERENCES registry.subject(id),
  date                      date    NOT NULL,   -- Asia/Seoul
  sides                     text    CHECK (sides IN ('left','right','both')),   -- NULL = 걷기 없음
  wear_hours                real,               -- 앵커 방 안에서 데이터가 있던 시간(segment에서 계산)
  walking_time_s            real,
  n_bouts                   integer,
  n_strides                 integer,            -- bout_stat.n_strides 합(한 발 기준)
  est_steps                 integer,            -- 2 × n_strides
  distance_m                real,
  bout_duration_median_s    real,
  bout_duration_p90_s       real,
  longest_bout_s            real,
  share_time_bouts_over_60s real,
  speed_median_mps          real,
  speed_p90_mps             real,
  speed_10_30s_median_mps   real,
  speed_30_60s_median_mps   real,
  speed_over_60s_median_mps real,
  stride_length_median_m    real,
  cadence_median_spm        real,
  n_turns                   integer,
  turn_angle_median_deg     real,
  turn_velocity_median_dps  real,
  steps_per_turn_median     real,
  steps_per_turn_cv         real,
  n_fog                     integer,
  fog_time_ratio            real,
  fused_ratio               real,               -- 융합 모드 보폭 비율
  data_sufficient           boolean NOT NULL,   -- wear_hours 기준 통과
  computed_at               timestamptz NOT NULL,
  PRIMARY KEY (subject_id, date)
);

-- 등급: 결과. 한 발의 하루. 그 발의 보폭 행이 있던 날만.
CREATE TABLE summary.daily_side (
  subject_id               bigint  NOT NULL REFERENCES registry.subject(id),
  date                     date    NOT NULL,
  side                     text    NOT NULL CHECK (side IN ('left','right')),
  n_strides                integer NOT NULL,
  stride_time_median_s     real,
  stride_time_cv           real,                -- 변동성은 ≥ 30초 구간만
  swing_time_cv            real,
  stride_length_median_m   real,
  stride_length_cv         real,
  lateral_dev_sd_m         real,
  clearance_median_m       real,
  min_clearance_median_m   real,
  min_clearance_cv         real,
  stance_ratio_median      real,
  freeze_index_median      real,
  stride_regularity_median real,
  harmonic_ratio_ap_median real,
  computed_at              timestamptz NOT NULL,
  PRIMARY KEY (subject_id, date, side),
  FOREIGN KEY (subject_id, date) REFERENCES summary.daily(subject_id, date)
);

-- 등급: 결과. 양발 하루. 양발 bout가 있던 날만. 비대칭 = |왼 − 오른| ÷ 두 값의 평균(두 발 중앙값으로).
CREATE TABLE summary.daily_bilateral (
  subject_id                  bigint  NOT NULL REFERENCES registry.subject(id),
  date                        date    NOT NULL,
  bilateral_walking_time_s    real    NOT NULL,
  n_steps                     integer NOT NULL,
  step_length_median_m        real,
  step_length_asymmetry       real,
  step_time_asymmetry         real,
  swing_time_asymmetry        real,
  stance_time_asymmetry       real,
  double_support_ratio_median real,             -- 양발 지지 시간 ÷ 보폭 시간
  double_support_cv           real,             -- ≥ 30초 구간만
  step_width_median_m         real,
  step_width_cv               real,             -- ≥ 30초 구간만
  computed_at                 timestamptz NOT NULL,
  PRIMARY KEY (subject_id, date),
  FOREIGN KEY (subject_id, date) REFERENCES summary.daily(subject_id, date)
);

GRANT USAGE ON SCHEMA summary TO core_rw, api_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA summary TO core_rw;   -- 다시 계산은 지우고 새로 넣음
GRANT SELECT ON ALL TABLES IN SCHEMA summary TO api_rw;
```

## 15. 정할 것과 확인할 것

정할 것(사용자):
1. 호스팅 업체와 리전.
2. 도메인 유무(있으면 Caddy 자동 인증서, 없으면 자체 서명).
3. 앱 기술 스택(API는 OpenAPI로 무관).
4. 원격 백업 저장소: S3 호환 오브젝트 스토리지(버전 보존 기능) 또는 두 번째 머신.
5. 30일 삭제를 켜는 때(AR-7의 조건을 언제 확인할지).
6. 보관한 범위 안의 빈자리를 늦은 묶음이 메운 경우(예: 해독기를 고친 뒤 IN-10으로 다시 넣은 묶음). `archive.range`는 seq 구간이고 겹침을 막으므로(AR-3) 그 묶음을 보관 파일에 넣을 길이 없습니다. 지금은 AR-6의 `archive_incomplete` 조건으로 그 구간의 raw를 지우지 않고 남깁니다. 정할 것: 보충 범위를 허용할지(겹침 규칙을 바꿈), 아니면 지금처럼 raw를 남길지.
7. wire.md의 "내용" 열에만 적힌 값 규칙(예: 측량이 실패하면 좌표 0)을 받을 때 검사할지. 지금은 "검사" 열만 검사합니다.

확인할 것(구현 중):
1. 권장 VM 디스크의 `fdatasync` 시간(9절). 20 ms를 넘으면 디스크 종류를 바꿉니다.
2. gaitfusion이 수 분짜리 seq 빈자리(archive 입력)를 `head_us_start`로 잇는지(7.1절).
3. 1시간 구간 분석이 `GAITFUSION_TIMEOUT_S`(10분) 안에 끝나는지. 아니면 시간 한도나 구간 최대 길이를 바꿉니다.
