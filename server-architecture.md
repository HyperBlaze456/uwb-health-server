# 원격 서버 계획

제안. 코드 없음. 서버와 분석 서비스는 펌웨어 저장소(`../uwb-firmware`) 밖에서 만들고, 이 문서는 그 작업자가 받을 계획입니다. 펌웨어 저장소가 책임지는 것은 Head 펌웨어와 Head→서버 데이터 규약([wire.md](../uwb-firmware/docs/wire.md))입니다.

## 1. 목표

집이나 병실에 앵커 4개를 설치하고 착용자가 발목 Tag를 찬 채 생활합니다. 서버는 다음 넷을 합니다.

| # | 목표 | 성공 기준 |
|---|---|---|
| G1 | **원본을 잃지 않고 쌓는다** | 수십 초 인터넷 끊김에서 묶음 손실 0. 더 긴 끊김은 빠진 묶음 수가 구간 표에 숫자로 보임. 서버 디스크가 죽어도 잃는 원본은 최근 10분 이하. 걷기 구간 원본은 영구 보관되어 언제든 재분석 가능 |
| G2 | **보행을 보폭 단위로 재구성한다** | 구간이 닫힌 뒤 30분 안에 보폭·회전·10초 창·삽화·궤적 표가 DB에 있음 |
| G3 | **신경학적 보행 이상(파킨슨, 뇌졸중 등)의 선별 지수와 개인 기준선 대비 변화를 낸다** | 일별 요약에서 7일 중앙값과 30일 기준선 차이를 계산할 수 있음. 진단이 아님 |
| G4 | **앱에 일·주 단위로 내준다** | 앱이 요청 하나로 착용자의 일별 요약 90일을 받음. 결과를 다시 계산해도 이전 결과가 남고 앱은 current만 봄 |

지금 대상은 팀원 시험입니다. 실제 대상자 요건은 13절.

## 2. 부품과 맡는 목표

| 부품 | 받는 것 → 내는 것 | 맡는 목표 | 왜 이렇게 |
|---|---|---|---|
| **Head 펌웨어** (펌웨어 저장소) | UWB 거리 + ESP-NOW IMU → 1초 바이너리 묶음을 HTTPS POST. 확인 못 받은 묶음은 PSRAM 버퍼(4 MB ≈ 14분)에서 다시 보냄 | G1 | 끊긴 동안 데이터는 Head에만 있으므로 손실을 막는 것은 전송 방식이 아니라 버퍼와 "확인 뒤 삭제" |
| **proxy** (Caddy) | 443 TLS → `/d/*`는 intake, 나머지는 api | — | 외부 포트가 HTTPS 하나라 Caddy로 충분 |
| **intake** | 묶음 → CRC·중복 검사 → raw 파일 이어 쓰기 + `intake.chunk` 행 → 확인 응답 | G1 | 전송 처리만. 구간·Tag·착용자를 모름. 전송 수단이 바뀌어도 뒤가 안 바뀜 |
| **core** | `intake.chunk` → 구간(segment) → gaitfusion 호출(analysis) → 일별 집계(summary), 걷기 원본 보관과 30일 지난 원본 삭제(archive) | G1, G2, G3, G4 | 비즈니스 규칙 전부가 여기. "60초 침묵이면 닫는다"는 메시지가 안 올 때 발동해야 하므로 수신기와 분리 |
| **gaitfusion** (별도 저장소, 상태 없는 HTTP) | 구간 바이트 + 문맥 → 보폭·회전·10초 창·삽화·궤적 JSON | G2 | 원본을 보는 유일한 분석 단계. 원본이 필요한 지표(동결 검출, 창 단위 신호 질)를 전부 여기서 내서 뒤 단계가 원본을 안 보게 함([gait-features.md](../gaitfusion/gait-features.md)) |
| **C 선별 서비스** (나중) | 집계 → 지수·추세 | G3 | 특징 기반 모델. 바뀌는 주기가 달라 분리 |
| **D LLM 해석** (나중) | 집계 + C 출력 + 불확실도 → 서술 | G3 | 해석 층. 분류기가 아니고 산술은 코드로 |
| **api** (FastAPI) | DB → 앱 REST, 로그인, 장치 등록, 착용 기록 | G4 | 조회와 사람 입력(registry)만. 규칙 없음 |
| **db** (PostgreSQL) | 스키마 = 단계: `pipeline`·`registry`·`intake`·`segment`·`analysis`·`summary`·`archive` | 전부 | 프로세스별 역할로 쓰기 범위를 제한해 경계를 권한으로 강제 |

```
Tag ──ESP-NOW IMU 100 Hz──▶ Head(A0) ◀── UWB 거리 (A1–A3)
Head ──HTTPS POST 1초 묶음──▶ proxy ──▶ intake ──▶ raw 파일(30일) + intake.chunk
core: intake ──▶ segment ──▶ analysis(gaitfusion C2) ──┬──▶ summary.daily ──▶ (C 선별 → D 해석)
                                                       └──▶ archive(걷기 원본 영구, 30일 지난 원본 삭제)
앱 ──HTTPS──▶ proxy ──▶ api ──▶ registry 쓰기, 나머지 읽기
```

## 3. 계약 셋

| 계약 | 방향 | 형식 | 소유 | 문서 |
|---|---|---|---|---|
| **C1** | Head → 서버 | HTTPS, 바이너리 묶음 | 펌웨어 저장소 | [wire.md](../uwb-firmware/docs/wire.md) |
| **C2** | 서버 → gaitfusion | HTTP multipart 요청, JSON 응답 | gaitfusion 저장소 | 8절 |
| **C3** | 앱 → 서버 | REST + JSON, OpenAPI | 서버 저장소 | 9절 |

계약을 바꾸는 쪽은 소유 저장소이고, 소비하는 저장소는 버전을 고정해 복사합니다. 세 저장소는 서로의 코드를 import하지 않습니다.

## 4. 서버 저장소 구조

이 서버의 핵심은 데이터 보존입니다. 구조는 두 질문에 바로 답하도록 짭니다. "이 데이터는 어느 단계가 만드나"(단계)와 "잃으면 되찾을 수 있나"(등급).

### 설계 방식

| 방식 | 뜻 | 이유 |
|---|---|---|
| **단계별 흐름** (pipes and filters) | 데이터는 intake → segment → analysis → summary·archive 순서로 흐름. 단계 하나 = 폴더 하나 = DB 스키마 하나 = 이름 하나 | 기능 하나가 한 폴더에 모임. 프로세스·스키마·폴더·작업 종류의 이름이 같음 |
| **원본은 추가만, 나머지는 다시 만들 수 있게** | 표와 파일마다 등급을 정함. 되찾을 수 없는 것만 엄격히 보호(수정 금지, 체크섬, 바깥 백업) | 보호 비용을 필요한 곳에만 씀 |
| **결과는 덮어쓰지 않고 쌓음** | 재분석은 새 run을 추가. 현재 run은 "가장 최근에 끝난 run". 착용자 귀속은 조회 때 `wear`로 정함 | 결과를 제자리에서 고치는 UPDATE가 없어 실수로 잃을 길이 없음 |
| **바깥과 닿는 곳만 감쌈** | DB 연결·파일·작업 큐·시계는 `pipeline/` 공용 모듈. 시험에서 바꿔 끼우는 것은 gaitfusion과 시계 둘뿐 | 혼자 유지하는 규모에 맞게 감싸는 코드를 줄임 |

### 단계

| 단계 | 하는 일 | 프로세스 | 작업 종류 | 쓰는 것 |
|---|---|---|---|---|
| **intake** | C1 수신, 검사, 중복 제거, raw 파일 + 색인 | intake | — (HTTP) | `intake.*`, raw 파일 |
| **segment** | 구간 열고 닫기, survey → 앵커 배치 | core (5초 루프) | — | `segment.*` |
| **analysis** | gaitfusion 호출, 결과 저장 | core | `analysis` | `analysis.*`, 궤적 파일 |
| **summary** | 착용자·날짜별 일별 요약 | core | `summary` | `summary.daily` |
| **archive** | 걷기 원본을 잘라 영구 보관, 30일 지난 원본 삭제 | core | `archive` | `archive.*`, archive 파일 |
| **registry** | 사람이 넣는 것: 장치·착용자·착용·계정 | api | — | `registry.*` |
| **pipeline** | 공용: DB·파일·작업 큐·읽기 위치·시계·설정·마이그레이션 | 전부 | — | `pipeline.*` |

규칙 셋입니다.

- **쓰기는 자기 스키마만.** 그 밖에는 `pipeline.job`에 작업을 넣는 것뿐. 예외 하나: archive가 30일 지난 `intake` 행과 raw 파일을 지움. intake·api는 DB 역할로, core 안의 네 단계는 시험(`tests/test_ownership.py`가 각 `store.py`의 쓰기 SQL 대상을 검사)으로 지킵니다.
- **읽기는 SQL로 어느 스키마든.** 단계 사이의 데이터는 DB 표로만 흐릅니다.
- **코드 import는 아래로만**(아래 "폴더"). 흐름 단계 다섯은 서로 import하지 않습니다.

### 데이터 등급

| 등급 | 뜻 | 대상 | 바뀌는 방식 | 백업 | 삭제 |
|---|---|---|---|---|---|
| **영구 원본** | 잃으면 되찾을 수 없음 | archive 파일, `archive.range`·`archive.cut` | 한 번 쓰고 수정 없음 | 10분마다 바깥 복사. 원격은 버전 보존, 삭제 금지 | 없음 |
| | | `registry.*` (사람 입력) | api가 INSERT·UPDATE | DB 덤프 | 없음(토큰 폐기는 UPDATE) |
| | | `intake.device_message` 중 `survey` | INSERT만 | DB 덤프 | 없음 |
| **임시 원본** | 30일 동안 전체 재분석용 | raw 파일, `intake.chunk`, `intake.device_message` 중 `system`·`health` | 이어 쓰기·INSERT만 | raw는 10분마다 바깥 복사(원격 35일), 표는 DB 덤프 | 30일 + 삭제 조건(6절) |
| **결과** | 원본에서 다시 계산 가능. 영구 | `segment.*`\*, `analysis.*`(궤적 제외), `summary.daily` | analysis는 INSERT만, segment는 열림·닫힘 UPDATE, summary는 다시 계산해 덮어씀 | DB 덤프 | 없음. 옛 run도 비교용으로 남김 |
| **캐시** | 지워도 재분석으로 되살림 | 궤적 파일, `analysis.trajectory` | 새 run이 끝나면 옛 run 것 삭제 | 안 함 | 옛 run 것은 즉시. 디스크가 모자라면 오래된 것부터 |
| **작업 기록** | 흐름을 돌리는 데만 씀 | `pipeline.*` | 자유 | DB 덤프에 함께 | `done` 작업은 30일 뒤 |

\* `segment`는 30일이 지나면 다시 만들 수 없습니다(수신 시각과 빈자리 수가 여기에만 남음). 결과이지만 지우지 않고 DB 덤프로 지킵니다.

### 폴더

```
uwb-server/
  pyproject.toml  compose.yaml  compose.dev.yaml  Caddyfile  .env.example  Dockerfile
  migrations/   001_pipeline.sql 002_registry.sql 003_intake.sql 004_segment.sql
                005_analysis.sql 006_summary.sql 007_archive.sql 008_roles.sql   ← server-schema.md
  src/uwbserver/
    pipeline/   db.py files.py jobs.py clock.py settings.py migrate.py
    registry/   rules.py store.py routes.py access.py
    intake/     rules.py store.py routes.py
    segment/    rules.py store.py run.py routes.py
    analysis/   rules.py store.py run.py routes.py gaitfusion.py
    summary/    rules.py store.py run.py routes.py
    archive/    rules.py store.py run.py
    process/    intake.py core.py api.py
  tests/        pipeline/ registry/ intake/ segment/ analysis/ summary/ archive/
                contract/(wire_vectors/, gaitfusion/) e2e/ test_ownership.py
  tools/        fake_head.py(합성 보행 → 묶음 → POST, 끊김·중복·늦은 도착 흉내)  import_usb.py(USB 기록 → 묶음 → POST)
                reindex_raw.py(raw 파일 → intake.chunk 복원)  verify_archive.py(sha256 대조, --reindex로 archive.range 복원)
  ops/          backup.sh crontab restore.md
```

**파일 역할.** 단계 폴더 안 파일은 네 역할뿐이고, 없는 역할은 파일도 없습니다.

| 파일 | 들어가는 것 | 모르는 것 | 시험 |
|---|---|---|---|
| `rules.py` | 순수 계산(데이터클래스와 함수). intake는 C1 해독·검사, segment는 구간 규칙, analysis는 C2 응답 → 행과 걷기 구간 집계, summary는 일별 요약, archive는 보관 범위와 삭제 조건, registry는 비밀번호·토큰 | DB·HTTP·파일 | 단위 시험 |
| `store.py` | 이 단계의 SQL 전부. 함수 하나 = 쿼리 하나 | HTTP | compose.dev의 db |
| `run.py` | 단계 실행: `step(conn)`(5초 루프) 또는 `handle(conn, job)`(작업). rules·store·바깥 호출을 이음 | — | 가짜 gaitfusion + db |
| `routes.py` | FastAPI 라우터. 모든 단계가 `app_router`(C3), intake는 `device_router`(C1)도 | — | TestClient |

그 밖의 파일은 둘입니다. `analysis/gaitfusion.py`(C2 HTTP 클라이언트)와 `registry/access.py`(로그인 확인과 권한 검사, 모든 `routes.py`가 씀).

- `pipeline/`: `db.py`(연결·트랜잭션), `files.py`(원본 파일 형식: 이어 쓰기, 읽기, 한 번 쓰기 + sha256, C1 프레이밍으로 바꾸기), `jobs.py`(작업 넣기·가져가기(`FOR UPDATE SKIP LOCKED`)·끝·실패·정리, 읽기 위치), `clock.py`, `settings.py`, `migrate.py`(6절 "스키마 변경").
- `process/`: 프로세스 셋의 조립 지점. `intake.py` = intake의 `device_router`. `core.py` = 5초 루프(`segment.run.step`) + 작업 스레드(작업 종류 → `analysis`·`summary`·`archive`의 `run.handle`) + 매일 `archive.run.purge`. `api.py` = 모든 단계의 `app_router`.

**import 규칙.** 아래로만 import하고 CI에서 import-linter로 막습니다.

```
process ──▶ intake · segment · analysis · summary · archive ──▶ registry ──▶ pipeline
                      (다섯은 서로 import 금지)
```

```ini
[importlinter]
root_package = uwbserver

[importlinter:contract:layers]
name = 아래로만
type = layers
layers =
    uwbserver.process
    uwbserver.intake | uwbserver.segment | uwbserver.analysis | uwbserver.summary | uwbserver.archive
    uwbserver.registry
    uwbserver.pipeline

[importlinter:contract:rules]
name = rules.py는 바깥을 모름
type = forbidden
source_modules = uwbserver.*.rules
forbidden_modules = psycopg httpx fastapi uwbserver.pipeline
```

`tools/`는 무엇이든 import할 수 있습니다.

## 5. 실행 단위와 배치

| 프로세스 | 외부 포트 | DB 역할 | 볼륨 |
|---|---|---|---|
| proxy | 443 | — | certs |
| migrate | — | DB 소유자 | — |
| intake | 내부 8001 | `intake_rw`: `registry.device` 읽기, `intake` INSERT | raw (쓰기) |
| core | — | `core_rw`: 전부 읽기, `segment`·`analysis`·`summary`·`archive`·`pipeline` 쓰기, `intake` DELETE(30일 삭제) | raw (읽기·삭제), archive (쓰기), trajectory (쓰기) |
| api | 내부 8000 | `api_rw`: `registry` 쓰기, `pipeline.job` INSERT, 나머지 읽기 | trajectory (읽기) |
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
  intake:  {<<: *app, command: uvicorn uwbserver.process.intake:app --host 0.0.0.0 --port 8001,
            depends_on: *ready, volumes: ["${DATA_DIR}/raw:/data/raw"]}
  core:    {<<: *app, command: python -m uwbserver.process.core,
            depends_on: {<<: *ready, gaitfusion: {condition: service_started}},
            volumes: ["${DATA_DIR}/raw:/data/raw", "${DATA_DIR}/archive:/data/archive", "${DATA_DIR}/trajectory:/data/trajectory"]}
  api:     {<<: *app, command: uvicorn uwbserver.process.api:app --host 0.0.0.0 --port 8000,
            depends_on: *ready, volumes: ["${DATA_DIR}/trajectory:/data/trajectory:ro"]}
  gaitfusion: {image: ghcr.io/<org>/gaitfusion:<tag>, command: uvicorn gaitfusion.service:app --host 0.0.0.0 --port 8100}
volumes: {certs: {}, pgdata: {}}
```

- intake·core·api는 이미지 하나(`uwb-server`)이고 명령만 다릅니다. 셋을 프로세스로 나누는 이유는 재시작 단위와 DB 권한입니다.
- `migrate`는 시작할 때 한 번 돌고 끝납니다. 나머지는 그 뒤에 뜹니다. db 연결이 될 때까지 다시 시도합니다.
- 원본과 궤적 파일은 호스트 디렉토리(`DATA_DIR`, 기본 `/srv/uwb`)에 둡니다. 호스트 cron(`ops/crontab`)이 `ops/backup.sh`로 바로 백업합니다.
- `compose.dev.yaml`은 proxy를 빼고 intake 8001·api 8000·db 5432를 호스트에 노출합니다. 버전 태그는 구현 때 고정합니다.

## 6. 규칙

각 단계의 `rules.py`에 들어가는 규칙입니다.

### 원본 파일 형식 (`pipeline/files.py`)

raw와 archive는 같은 형식입니다. 묶음마다 앞에 `u32 길이, i64 서버 수신 UTC µs` 12 B. 그래서 파일만 있어도 색인(`intake.chunk`, `archive.range`)을 다시 만들 수 있습니다. gaitfusion에 보낼 때는 12 B 머리를 C1의 `u32 길이`로 바꿔 이어 붙입니다.

- **raw**: `/data/raw/<device_id>/<head_boot_id>/<YYYY-MM-DDTHH>.bin`. 장치 × 부팅 × 수신 시간(hour) 단위로 이어 쓰기만. `intake.chunk` 행이 `(path, offset, len)`으로 가리킵니다.
- **archive**: `/data/archive/<device_id>/<YYYY-MM>/<start_at>_<head_boot_id>_<first_seq>-<last_seq>.bin`. `start_at`은 UTC `2026-10-26T03-12-05Z` 꼴. 임시 이름으로 쓰고 fsync 뒤 이름을 바꿉니다. `archive.range` 행에 `sha256`.

### 보존

- 전체 원본(raw 파일과 `intake.chunk`)과 `system`·`health` 메시지: **30일**.
- 걷기 원본(archive), 사람 입력(`registry`), `survey` 메시지, 구간·분석 결과·일별 요약: **영구**. 궤적은 캐시.
- 원본 삭제는 `archive.run.purge`(매일 1회) 한 곳에서만 합니다. 다른 코드는 원본을 지우지 않습니다.
- 30일이 지나면 걷기로 판정되지 않았던 시간은 다시 볼 수 없습니다.

### 걷기 원본 보관 (`archive/rules.py`)

- **언제**: run이 끝나면(`done`·`skipped` 모두) analysis가 `archive {run_id}` 작업을 넣습니다. 현재 run이든 아니든 모든 run의 bout가 대상입니다. 보관 범위는 늘어나기만 합니다.
- **범위**: bout마다 `[start_at − 15초, end_at + 15초]`를 묶음(1초) 단위로 잡고, 구간 안으로 자르고, 겹치거나 맞닿는 것은 합칩니다. 15초인 이유: 자세 필터 예열과 UWB 시계 맞춤에 앞 문맥이 필요하고, 나중에 걷기 기준(멈춤 3초 미만 등)을 느슨하게 바꿔도 걷기 사이가 메워짐. bout 하나에 약 144 kB가 더해집니다. gaitfusion의 앞뒤 2초 포함과는 별개입니다.
- **겹침 없음**: 이미 `archive.range`가 덮는 seq와 raw에서 이미 지워진 seq는 빼고, 남은 연속 구간마다 파일 하나를 만듭니다. DB 제약(`EXCLUDE`)이 겹침을 막습니다. 원래 없던 seq(빈자리)는 파일에도 없습니다.
- **끝 표시**: run마다 `archive.cut` 행을 남깁니다(걷기가 없어도 `n_ranges = 0`). 30일 삭제 조건의 근거입니다.

### 30일 삭제 조건 (`archive/rules.py`)

raw 파일 하나(수신 hour가 30일 전보다 이전)는 그 안의 모든 묶음이 다음을 만족할 때만 지웁니다.

1. 묶음이 속한 구간이 `closed`.
2. 그 구간에 `analysis.run`이 하나 이상 있음.
3. 그 구간의 모든 run에 `archive.cut` 행이 있음.
4. 그 구간의 `analysis`·`archive` 작업이 `queued`·`running`이 아님.

하나라도 아니면 파일을 남기고 로그에 적습니다. 지우는 순서는 `intake.chunk` 행 → 파일입니다(중간에 멈추면 남은 파일은 다음 날 정리). 어느 구간에도 속하지 않은 묶음이 있는 파일은 지우지 않습니다(버그 신호). 분석에 실패한 구간(작업 `failed`)의 원본도 남습니다. 관리자가 재분석하거나 직접 지웁니다.

### 구간 (`segment/rules.py`)

한 장치에서 끊김 없이 이어진 원본. 분석 단위.
- 시작: 열린 구간이 없는 장치의 묶음.
- 닫힘(먼저 오는 것): 마지막 수신 뒤 60초 · `head_boot_id` 바뀜 · 첫 IMU 프레임의 `tag_mac`/`tag_boot_id` 바뀜 · `geometry_hash` 바뀜 · 1시간 도달.
- 다시 열기: 같은 부팅에서 `chunk_seq`가 이어지고 Head 시각 간격 60초 미만이면 닫힌 구간을 다시 열고, 다시 닫히면 재분석 등록.
- 빈자리: `chunk_seq` 건너뜀 → `n_missing_chunks`. 머리 `dropped_*` 합 → `n_dropped_frames`.
- 시각: flags bit0 켜진 묶음이 있으면 `time_source = server`, 아니면 `arrival`(수신 − 1초).

### 귀속 (`analysis.bout_subject` 뷰)

bout 시작 시각에 `device_id`가 같고 (`tag_mac`이 NULL이거나 같고) 기간이 덮는 `wear`의 `subject_id`. 둘이면 `tag_mac` 명시 쪽, 그래도 둘이면 NULL + `ambiguous`.

bout에는 주인 열이 없고 조회 때마다 이 뷰가 정합니다. 그래서 착용 기록을 고치면 바로 반영되고 결과 표를 고칠 일이 없습니다. `PATCH /v1/wear`는 `started_at`·`ended_at`·`paretic`만 바꿉니다. 착용자나 장치를 바꾸려면 끝내고 새로 만듭니다.

### 걷기 구간 집계 (`analysis/rules.py`)

케이던스 = 2 × 분당 보폭. 거리 = 보폭 길이 합. 변동성(CV, SD, 표본 엔트로피)은 **30초 이상 구간에서만**.

### 일별 요약 (`summary/rules.py`)

- `Asia/Seoul` 날짜에 시작한 current bout. 자정을 넘는 bout는 시작한 날. 속도·보폭 길이는 구간 길이별(10–30초, 30–60초, 60초 이상)로 따로. `wear_hours < 12`면 `data_sufficient = false`. 열은 [server-schema.md](server-schema.md) `summary.daily`.
- `summary {device_id, from, to}` 작업은 그 기간의 날짜마다, 그 장치에 겹치는 착용 기록이 있는 착용자 전원을 다시 계산합니다(착용 기록이 바뀌어 bout를 잃은 착용자도 갱신되도록).
- 작업을 넣는 쪽: analysis는 run이 끝나면 구간 기간으로, api는 착용 기록을 만들거나 고치면 고치기 전·후 기간을 합쳐서.

### 재분석과 버전

- 다시 돌리면 새 `analysis.run`과 그 아래 표가 전부 새로 생깁니다. 옛 run의 bout·stride·turn·window·event는 비교용으로 남고 궤적만 지웁니다.
- 현재 run은 `analysis.current_run` 뷰(구간마다 가장 최근에 끝난 run)입니다. 앱과 summary는 이 뷰를 거칩니다.
- **30일 안** (`input = raw`): 구간의 묶음 전부를 raw에서 읽어 보냅니다. `origin_at = segment.start_at`.
- **30일 뒤** (`input = archive`): 구간의 archive 파일을 seq 순으로 이어 한 번에 보냅니다. `origin_at` = 첫 범위의 `start_at`. 범위 사이 빈 시간은 seq 빈자리로 보입니다. C2 형식은 같습니다.
- 30일 안의 재분석이 옛 run이 못 본 걷기를 찾으면 archive가 그만큼 늘어납니다.

### 작업 (`pipeline/jobs.py`)

`queued → running → done | failed`. 실패하면 `attempts` +1, `run_after = now + 2^attempts 분`, 3회 뒤 `failed`. 종류는 셋: `analysis {segment_id}`, `summary {device_id, from, to}`, `archive {run_id}`. gaitfusion 422(분석 불가)는 `done` + run `skipped`. 실패한 `analysis` 작업은 run 행을 만들지 않습니다.

### 백업과 복구 (`ops/`)

| 대상 | 방법 | 주기 | 원격 보관 |
|---|---|---|---|
| raw 파일 | `rclone copy` | 10분 | 35일 |
| archive 파일 | `rclone copy` (삭제를 옮기는 `sync` 금지) | 10분 | 영구. 원격 버전 보존 켬 |
| DB | `pg_dump -Fc` → 원격 | 매일 | 14일. 매월 1일 덤프는 지우지 않음 |
| 궤적 파일 | 안 함 | — | — |

- 백업이 실패하면 알림을 보냅니다. 백업이 멈춘 채 30일이 지나면 원격에 없는 원본이 삭제될 수 있기 때문입니다.
- 매주 `verify_archive.py`가 archive 파일의 sha256을 `archive.range`와 대조합니다.
- 디스크가 죽으면 잃는 것: 최근 10분 이하의 raw(Head는 확인 뒤 지웠으므로), 마지막 덤프 뒤 24시간 이하의 DB 행. 이 중 `intake.chunk`는 raw 파일에서(`reindex_raw.py`), `archive.range`는 archive 파일에서(`verify_archive.py --reindex`) 다시 만듭니다. 구간과 분석 결과는 재분석으로 되살립니다. `registry`의 하루치 입력은 사람이 다시 넣습니다.
- 복구 연습: 분기마다 빈 compose에 덤프를 넣고, archive 일부를 받아 `verify_archive.py`를 돌리고, 구간 하나를 재분석합니다. 절차는 `ops/restore.md`.

### 스키마 변경 (`pipeline/migrate.py`)

- `migrations/`에 `NNN_<단계>_<내용>.sql`을 추가만 합니다. 이미 적용한 파일은 고치지 않습니다. `migrate`가 안 적용된 파일을 번호 순으로 적용하고 `pipeline.migration`에 기록합니다.
- 표를 만드는 파일은 그 표의 GRANT도 함께 적습니다.
- 열이나 표를 지우는 변경 전에는 DB 덤프를 따로 받아 둡니다.

## 7. 계약 C1: Head → 서버

[wire.md](../uwb-firmware/docs/wire.md)가 전부입니다. 서버가 기대는 요지: `POST /d/v1/chunks`(`u32 len + 묶음` 반복, 최대 16개·128 kB, 응답 `acked`/`rejected`/`server_utc_us`, 중복은 acked), `POST /d/v1/status`(`system`/`health`/`survey` JSON), 중복 제거 키 `(device_id, head_boot_id, chunk_seq)`, 보통 4.8 kB/s.

## 8. 계약 C2: 서버 → gaitfusion

gaitfusion 저장소가 소유합니다. 서버가 기대하는 모양입니다.

### `POST /v1/analyze` 요청 (`multipart/form-data`)

| 파트 | 형식 | 내용 |
|---|---|---|
| `chunks` | `application/octet-stream` | C1 요청 본문과 **같은 프레이밍**, `chunk_seq` 오름차순. 30일 안의 구간은 묶음 전부(1시간 약 17 MB). 30일이 지난 구간은 보관된 걷기 범위의 묶음만이고 범위 사이는 seq 빈자리로 보임 |
| `context` | `application/json` | 아래 |

```json
{
  "segment_id": 1234,
  "anchors": [{"id": 0, "xyz_m": [0, 0, 1.2], "bias_m": 0.0}, "…"],
  "subject": {"height_m": 1.72, "leg_length_m": 0.90, "side": "left", "paretic": null},
  "params": {"stance_threshold": null, "stance_window": null, "bout_min_strides": 4, "bout_max_pause_s": 3.0, "bout_pad_s": 2.0}
}
```

`anchors`가 null이면 IMU 단독 모드. `subject.side`(착용 발)와 `paretic`(마비측 여부)은 보폭 지표의 뜻을 바꾸므로 알면 넣습니다. `params`의 null은 서비스 기본값. 걷기 구간 기준은 **정지와 swing 교대 4보폭 이상, 멈춤 3초 미만, 앞뒤 2초 포함**입니다.

### 응답 (200, JSON)

```json
{
  "gaitfusion_version": "1.3.0+gabc1234",
  "summary": {"n_imu": 359800, "n_range": 358100, "seq_gaps": 3, "clock_fit": {}, "windows": 2,
              "side": "left", "sensor_site": "lateral_ankle", "method_versions": {"fog": "fi-1.0"}},
  "bouts": [{
    "start_s": 812.40, "end_s": 871.15, "mode": "fused", "quality": {"uwb_accept_ratio": 0.93},
    "strides": [{"idx": 0, "start_s": 814.1, "end_s": 815.2, "duration_s": 1.1, "length_m": 1.31, "speed_mps": 1.19,
                 "heading_deg": 87.2, "clearance_m": 0.052, "min_clearance_m": 0.018, "min_clearance_t_s": 0.61,
                 "stance_s": 0.66, "swing_s": 0.44, "stance_ratio": 0.60, "lateral_dev_m": 0.03,
                 "pitch_at_strike_deg": -12.1, "pitch_at_toeoff_deg": 38.4,
                 "swing_peak_gyro_dps": 310, "stance_min_gyro_dps": 4, "strike_peak_g": 6.2, "swing_peak_speed_mps": 3.9,
                 "uwb_support": 0.95, "turn_idx": null}],
    "turns":   [{"idx": 0, "start_s": 830.1, "end_s": 832.0, "angle_deg": 178, "peak_velocity_dps": 61, "mean_velocity_dps": 40, "n_strides": 3, "jerk": 0.8}],
    "windows": [{"start_s": 812.4, "end_s": 822.4, "freeze_index": 0.9, "power_locomotor": 1.0, "power_freeze": 0.9,
                 "dominant_freq_hz": 1.8, "dominant_amp": 0.4, "stride_regularity": 0.81,
                 "harmonic_ratio_ap": 2.1, "harmonic_ratio_v": 2.4, "harmonic_ratio_ml": 1.6,
                 "local_dynamic_stability": 0.9, "sample_entropy": 0.3, "jerk_rms": 1.3,
                 "acc_rms_v": 0.4, "acc_rms_ap": 0.3, "acc_rms_ml": 0.2, "gyro_rms": 90,
                 "mean_speed_mps": 1.15, "n_strides": 9, "uwb_support": 0.95}],
    "events":  [{"start_s": 840.2, "end_s": 843.9, "type": "fog", "score": 0.82, "method_version": "fi-1.0"}],
    "trajectory": {"rate_hz": 100, "t0_s": 810.40, "n": 6275,
                   "px": [], "py": [], "pz": [], "vx": [], "vy": [], "vz": [],
                   "roll_deg": [], "pitch_deg": [], "yaw_deg": [], "stance": [], "sig_px": [], "sig_py": [], "sig_pz": []}
  }]
}
```

- 시각은 **보낸 첫 묶음의 `head_us_start`를 0으로 하는 초**. 서비스는 UTC를 모르고 core가 `analysis.run.origin_at`을 더합니다.
- seq 빈자리는 표본 수가 아니라 묶음 머리의 `head_us_start`로 시간을 이어야 합니다. 30일 뒤 재분석에서는 빈자리가 수 분일 수 있습니다(gaitfusion 쪽 확인 필요).
- 열의 정의와 근거는 [gait-features.md](../gaitfusion/gait-features.md) 3–6절. 수치가 계산 불가면 `null`(0이 아님).
- 궤적은 100 Hz, 걷기 구간 앞뒤 2초 포함. 앱용 25 Hz는 api가 줄입니다. 응답은 JSON 하나(1시간 구간에 보행 절반이면 약 15 MB).
- 보폭·회전·창·삽화는 전부 서비스가 계산합니다. core는 집계만 합니다.

### 오류

| 코드 | 뜻 | core |
|---|---|---|
| 400 | 묶음 해독 실패(C1 위반). 본문에 어느 묶음·이유 | 작업 `failed` |
| 422 | 분석 불가(표본 부족, IMU 없음). 본문에 이유 | 작업 `done`, run `skipped` + 이유 |
| 5xx, 시간 초과 10분 | 서비스 문제 | 재시도(6절 작업 규칙) |

`GET /healthz`, `GET /v1/version`.

## 9. 계약 C3: 앱 API

REST + JSON, 경로 `/v1/`. FastAPI가 OpenAPI를 만들고 앱 팀은 그것으로 클라이언트를 생성합니다. 시각은 UTC ISO 8601, `date`는 `Asia/Seoul`. 인증은 이메일 + 비밀번호 → 불투명 토큰(`Authorization: Bearer`, DB에는 해시). `member`는 자기 `subject`와 그 착용 기간의 장치만, `admin`은 전부.

폴더는 그 데이터를 만든 단계이고, 라우트는 그 폴더의 `routes.py`에 있습니다.

| 메서드와 경로 | 내용 | 권한 | 폴더 |
|---|---|---|---|
| `POST /v1/auth/token` · `DELETE /v1/auth/token` | 로그인 · 로그아웃 | 누구나 · 로그인 | registry |
| `GET /v1/me` | 내 계정과 착용자 | 로그인 | registry |
| `GET /v1/subjects/{id}/daily?from=&to=` | 일별 요약 | 본인, admin | summary |
| `GET /v1/subjects/{id}/bouts?from=&to=` | 걷기 구간 목록 | 본인, admin | analysis |
| `GET /v1/bouts/{id}` | 걷기 구간 상세: 보폭, 회전, 삽화 | 본인, admin | analysis |
| `GET /v1/bouts/{id}/trajectory?rate=25` | 궤적. `rate` 25(기본)·50·100. 캐시가 지워졌으면 404 | 본인, admin | analysis |
| `GET /v1/devices` · `GET /v1/devices/{id}/health?from=&to=` | 장치 목록과 최신 상태 · 1분 기록(최근 30일) | 로그인 · 본인, admin | registry · intake |
| `POST /v1/wear` · `PATCH /v1/wear/{id}` | 착용 시작·끝, 마비측 여부 | 본인, admin | registry |
| `POST /v1/devices` · `DELETE /v1/devices/{id}/token` | 장치 등록(토큰 한 번 표시) · 토큰 폐기·재발급 | admin | registry |
| `GET /v1/segments?device=&from=&to=` · `POST /v1/segments/{id}/reanalyze` | 원본 구간과 빈자리 수 · 재분석 등록(30일 뒤에는 걷기 부분만) | admin | segment |

## 10. 데이터 모델

DDL 전부는 [server-schema.md](server-schema.md)입니다. 관계만 적습니다.

```
registry.device 1─n segment.segment 1─n analysis.run 1─n analysis.bout ─┬─ n stride (n─1 turn)
                                                                        ├─ n turn
                                                                        ├─ n bout_window
                                                                        ├─ n gait_event
                                                                        └─ 1 trajectory (파일 위치)
registry.subject 1─n registry.wear      bout의 주인 = analysis.bout_subject 뷰(조회 때 wear와 맞춤)
registry.subject 1─n summary.daily
segment.segment 1─n archive.range       걷기 원본 조각.  analysis.run 1─1 archive.cut
intake.chunk ↔ segment.segment는 (device_id, head_boot_id, seq 범위)로 이어짐. FK 없음(색인은 30일 뒤 사라짐)
```

## 11. 보안과 용량

- 외부 포트 443 하나. intake·api·gaitfusion·Postgres는 내부망.
- 장치 토큰은 발급 때 한 번 보이고 해시만 저장. 할 수 있는 일은 `/d/v1/`에 올리기뿐.
- Head는 서버 인증서의 루트 CA를 펌웨어에 넣고 검증. 도메인이 없으면 자체 서명으로 시작하고 Head 쪽은 같음.
- 비밀값은 `.env`(git 제외). DB 역할 셋으로 쓰기 범위 제한. `subject`에는 실명 대신 코드.
- 백업과 복구: 6절.

| 항목 | 값 |
|---|---|
| Head 1대 | 4.8 kB/s, 초당 요청 1개. 버퍼 PSRAM 4 MB ≈ 14분 |
| 전체 원본 (30일) | 시간당 17 MB, 24시간 착용 시 하루 0.41 GB. 3명 × 30일 = 37 GB (회전) |
| 걷기 원본 (영구) | 걷기 하루 1.5시간, 앞뒤 여유를 더해 그 1.5배로 가정: 3명 연 43 GB. 걷기 하루 1시간이면 29 GB, 2시간이면 57 GB |
| 궤적 캐시 | 보행 1시간에 약 15 MB(압축 전) 이하: 3명 연 25 GB 이하 |
| 색인·결과 표 | `intake.chunk` 30일 1.4 GB, 분석 결과 연 1.5 GB |
| 서버 디스크 | 1년 차 약 110 GB, 이후 연 70 GB 증가. 250 GB면 약 3년 |
| 원격 저장 | raw 37 GB(고정) + 걷기 원본 연 43 GB + DB 덤프(14일 + 매월 1개) |
| gaitfusion 메모리 | 10분 창 하나에 약 0.3 GB |
| 권장 VM | 2 vCPU, 4 GB RAM, 디스크 250 GB |

실제 착용 시간은 Tag 전원에 달려 있어 이보다 훨씬 짧을 가능성이 큽니다.

## 12. 각 저장소가 할 일과 순서

가장 불확실한 것부터입니다.

| 순서 | 저장소 | 일 |
|---|---|---|
| 1 | 펌웨어 | **Head TLS 메모리 확인**: `WiFiClientSecure` keep-alive로 1초마다 5 kB POST하면서 남은 힙과 수집이 멈추지 않는지. 막히면 전송 방식을 다시 정함 |
| 2 | 펌웨어 | C1 확정: C++ 인코더, `tests/wire_vectors/` |
| 3 | 서버 | `pipeline`·`registry`·`intake`. fake_head로 raw와 `intake.chunk`, 중복·재전송까지 |
| 4 | 서버 | `segment`·`analysis`·`summary`. 가짜 gaitfusion으로 작업 흐름까지 |
| 5 | 서버 | `archive`(걷기 원본 보관·30일 삭제)와 `ops/backup.sh`. 복구 연습 1회 |
| 6 | gaitfusion | 저장소 분리(`analysis/gaitfusion`, `scripts/fuse.sh`, `scripts/setup-analysis.sh`, `tests/fusion/` 이동), `wire.py`, `service.py`, 보폭 추가 열·회전·창·삽화([gait-features.md](../gaitfusion/gait-features.md)), 10분 창 초기 상태 입력, 긴 seq 빈자리 처리, 이미지 |
| 7 | 서버 | api(각 단계의 `routes.py`) |
| 8 | 펌웨어 | Head 전송부: 묶음 인코더, PSRAM 링 버퍼, HTTPS, 재전송, UTC 추정, `status` 1분. 빌드 옵션 `PSRAM=opi,FlashSize=16M`. 런타임 설정에 서버 URL·토큰. `health`에 PSRAM과 버퍼 점유율 |
| 9 | 서버 | proxy와 원격 VM 배포 |

측량용 3D 뷰어는 지금처럼 현장 노트북 broker(`../uwb-firmware/server/mosquitto.conf`)로 쓰고, 설치가 끝나면 Head 설정을 서버 URL로 바꿉니다.

## 13. 실제 대상자로 넘어갈 때

건강정보는 개인정보보호법상 민감정보입니다. 팀원 시험에서 실제 대상자로 가기 전에 국내 리전, 별도 동의서, 접근 기록(누가 어떤 착용자의 데이터를 봤는지), 저장·백업 암호화, 보존·파기 기간, 연구라면 IRB 요건을 갖춥니다.

## 14. 사용자가 정할 것

1. 호스팅 업체와 리전.
2. 도메인 유무(있으면 Caddy 자동 인증서, 없으면 자체 서명).
3. 앱 기술 스택(API는 OpenAPI로 무관).
4. 원격 백업 저장소: S3 호환 오브젝트 스토리지(버전 보존 기능) 또는 두 번째 머신.
