# 서버 DB 스키마 (SQL)

[server-architecture.md](server-architecture.md) 10절의 관계를 PostgreSQL DDL로 적은 것입니다. 서버 저장소의 `migrations/001–008`로 그대로 옮기는 것을 전제로 썼습니다. 파일 하나 = 단계 하나 = 스키마 하나입니다. 열의 뜻과 근거는 [gait-features.md](../gaitfusion/gait-features.md), 묶음 필드의 뜻은 [wire.md](../uwb-firmware/docs/wire.md)에 있습니다.

발목 Tag는 세트당 1개(기본) 또는 2개(양 발목, 펌웨어 빌드 모드)입니다. 두 경우를 같은 표로 받고, 둘째 발과 양발 지표는 있을 때만 행이 생깁니다(아래 "선택적인 것은 행으로"). 표를 고칠 때 지켜야 할 속도는 "속도 요구"에 있습니다.

아직 Postgres에 올려 보지 않았습니다.

## 공통 규칙

- **단위**: SI. 열 이름 끝에 단위를 붙입니다. `_m`(m), `_mps`(m/s), `_s`(초), `_deg`, `_dps`(deg/s), `_g`, `_hz`, `_spm`(분당 보폭). 접미가 없는 비율은 0–1.
- **NULL은 "계산 못 함" 하나의 뜻**입니다. 0으로 대신 쓰지 않습니다. 데이터가 아예 없는 것은 NULL이 아니라 행이 없는 것으로 나타냅니다.
- **발(`side`)**: `text`, `'left'`·`'right'`. Tag 번호가 정하고 사람이 입력하지 않습니다. 묶음 프레임의 `tag_id` 0 = 왼쪽(T0), 1 = 오른쪽(T1)([wire.md](../uwb-firmware/docs/wire.md)). 1-Tag 세트는 T0를 왼쪽 발목에 찹니다.
- **선택적인 것은 행으로**:
  - 발마다 생기는 것(보폭, 신호 창, 궤적, 일별 발 지표)은 `side` 열이 있는 행이고, 찬 발의 행만 생깁니다.
  - 양발이 함께 있어야 나오는 것(걸음 `analysis.step`, `summary.daily_bilateral`)은 따로 된 표이고, 양발일 때만 행이 생깁니다.
  - 그래서 **행이 없음 = 그 발(또는 양발) 데이터가 없음**, **행 안의 NULL = 데이터는 있었지만 계산 못 함**입니다.
  - 한 표에 `left_*`·`right_*` 같은 열을 늘려 양발을 담지 않습니다. 예외는 `intake.chunk`의 Tag 자리 두 칸뿐입니다(받기를 행 하나로 끝내려고, "속도 요구").
- **`sides`**: 구간·걷기 구간·하루에 어느 발의 데이터가 있었는지. `'left'`·`'right'`·`'both'`. 행에서 다시 셀 수 있지만 조회 조건에 자주 쓰므로 열로 둡니다.
- **시각**: DB는 전부 `timestamptz`(UTC). 묶음의 µs 정수(`head_us_start`, `utc_us_start`)만 `bigint`로 그대로 둡니다. 날짜(`date`)는 `Asia/Seoul`.
- **부호 없는 정수**: Postgres에 없으므로 wire의 `u32`는 `bigint`, `u16`은 `integer`에 담습니다.
- **실수**: 보행 지표는 `real`(4 B).
- **열거형**: `text` + `CHECK`. `ENUM` 형은 값 추가가 번거로워 쓰지 않습니다.
- **기본 키**: `bigserial`. 외부에 노출되는 id(앱 API)는 그대로 정수를 씁니다. 추측 가능성이 문제가 되면 그때 `uuid`를 추가합니다.
- **등급**: 표마다 주석 첫 줄에 적습니다(영구 원본 · 임시 원본 · 결과 · 캐시 · 작업 기록). 뜻은 architecture 4절 "데이터 등급".
- **쓰기**: 스키마 = 단계. 단계는 자기 스키마만 씁니다. `008_roles.sql`이 프로세스 단위로 강제합니다.
- **보존**: 전체 원본 30일(raw 파일과 `intake.chunk`·`intake.health`·`intake.system`을 함께 삭제. `intake.survey`는 영구). 걷기 원본(`/data/archive`, `archive.range`) 영구. 분석 결과 영구, 옛 run의 궤적만 삭제. 삭제 조건은 architecture 6절.
- **결과 표는 추가만**: `analysis` 표에는 UPDATE·DELETE 권한이 아무에게도 없습니다(궤적 위치 행 삭제만 예외). 그래서 FK에 `ON DELETE CASCADE`를 두지 않습니다. 실수로 지우려 하면 FK가 막습니다.
- **궤적**: 발마다 파일 하나 `/data/trajectory/<run_id>/<bout_id>-<side>.trj`. C2 응답의 궤적 바이너리 파트(TRJ1, [architecture](server-architecture.md) 8절)를 core가 해석하지 않고 그대로 씁니다. 표에는 위치만. api는 머리를 읽고 필요한 채널만 `rate`에 맞게 건너뛰며 읽습니다.
- **일별 요약 확장**: 열 추가 = 마이그레이션. `jsonb` 확장 열을 두지 않습니다.
- **변경**: 적용한 파일은 고치지 않고 `NNN_<단계>_<내용>.sql`을 추가합니다. 표를 만드는 파일은 GRANT도 함께 적습니다. 적용 기록 표 `pipeline.migration(name, applied_at)`은 `migrate`가 처음에 만듭니다.

## 속도 요구

이 스키마가 지켜야 하는 속도입니다. 값은 권장 VM(2 vCPU, 4 GB, [architecture](server-architecture.md) 11절) 기준 목표이고 아직 재지 않았습니다. 네트워크와 TLS 시간은 빼고 셉니다. 표나 조회를 바꿀 때 오른쪽 열의 방법을 깨지 않습니다.

| 경로 | 요구 (p95) | 스키마가 지키는 방법 |
|---|---|---|
| 받기: `POST /d/v1/chunks` | 요청 하나(묶음 1–16개) 서버 처리 2 ms 이하, 그중 DB는 묶음당 0.5 ms 이하. Head마다 초당 1묶음이 밀리지 않음 | 요청 하나 = 트랜잭션 하나. 묶음 행과 묶음 안 상태 블록 행을 표마다 다중 행 INSERT 한 번. 장치 토큰 조회(기본 키 하나) 말고는 다른 표를 읽지 않음. Tag 자리는 고정 열 두 칸이라 Tag가 둘이어도 행 수가 같음. 색인은 중복 키·장치 시각·파일 경로 셋뿐. 커밋 동기화는 아래 "받기의 디스크 동기화" |
| 구간 잇기 (core segment) | 묶음당 0.5 ms 이하 | 열린 구간을 부분 색인 `segment_open`으로 찾아 한 행 UPDATE. `segment_tag`는 Tag가 처음 보이거나 바뀔 때만 INSERT. 읽기 위치(`pipeline.cursor`)는 묶음 여러 개를 처리한 뒤 한 번 고침 |
| 결과 쓰기 (core analysis) | run 하나(1시간 구간) 200 ms 이하 | run 하나 = 트랜잭션 하나, 표마다 COPY 한 번. 결과 표에는 트리거가 없고 고유 제약은 순번 하나씩. 궤적(응답의 대부분)은 해석하지 않고 파일로 씀. 머리와 길이·CRC만 확인 |
| 일별 요약 다시 계산 (core summary) | 착용자 하루 10 ms 이하 | 그날 bout의 stride·step을 `(bout_id, …)` 색인으로만 읽음. 일별 세 표를 한 트랜잭션에서 지우고 COPY로 넣음 |
| 앱 요청 (api) | 요청 하나 20 ms 이하, 착용자 1년치 기준 | 조회 키가 기본 키나 색인의 앞부분. 일별 세 표는 `(subject_id, date[, side])` 기본 키 범위 읽기. bout 아래 표는 모두 `bout_id`가 앞에 오는 색인. `bout_subject`는 bout id나 기간으로 좁힌 뒤에만 씀. 궤적은 TRJ1 파일에서 필요한 채널만 건너뛰며 읽음(25 Hz = 4개마다 1개, 해석 없음) |

**받기의 디스크 동기화.** 확인 응답(`acked`)을 보낸 묶음은 Head가 지우므로, 응답 전에 디스크에 있어야 하는 것은 raw 파일입니다. intake는 요청마다 raw 파일에 묶음들을 이어 쓰고 `fdatasync`를 한 번 한 뒤 DB에 커밋하고 응답합니다. DB 행은 raw 파일에서 다시 만들 수 있으므로(`tools/reindex_raw.py`) intake 역할은 `synchronous_commit = off`로 커밋이 디스크를 기다리지 않습니다(`008_roles.sql`). 그 대가로 서버가 비정상 종료되면 마지막 1초 안쪽의 `intake.*` 행이 빠질 수 있으므로, intake는 시작할 때 최근 raw 파일을 `intake.chunk`와 대조해 빠진 행을 채운 뒤 받기를 엽니다(`reindex_raw.py --since <마지막 정상 종료>`).

규칙:
- 조회 조건과 정렬에 쓰는 값은 열로 둡니다. `jsonb`(`quality`, `summary`, `params`, `anchors`, `target`)는 원문 보존용이고 `WHERE`·`ORDER BY`에 쓰지 않습니다. 받는 경로(intake)에는 JSON이 없습니다. Head가 보낸 바이너리 블록을 해독해 검사한 뒤 바이트 그대로(`bytea`) 둡니다.
- 받기는 관용하지 않습니다. 길이·CRC·버전뿐 아니라 프레임과 상태 블록의 모든 필드 범위를 검사하고, 하나라도 틀리면 그 묶음 전체를 거절하고 이유를 셉니다([wire.md](../uwb-firmware/docs/wire.md) "서버의 검사"). Head는 우리 펌웨어이므로 틀린 값은 버그이고, 고쳐서 받지 않습니다.
- 선택적인 데이터는 행으로 둡니다(공통 규칙). 표가 좁아지고, 1-Tag 착용자의 조회가 쓰지 않는 반대 발 열 때문에 느려지지 않습니다.
- 수치는 `real`, 개수는 `integer`, 발은 짧은 `text`. 큰 배열(궤적)은 파일에 둡니다.
- 새 조회를 넣을 때는 착용자 1년치 크기의 시험 데이터에서 `EXPLAIN ANALYZE`로 색인을 타는지 확인합니다. 순차 스캔이면 색인을 먼저 추가하는 마이그레이션을 넣습니다.

## 001_pipeline.sql — 작업 큐와 읽기 위치

```sql
CREATE SCHEMA IF NOT EXISTS pipeline;   -- migrate가 기록 표를 위해 먼저 만들었을 수 있음

-- 등급: 작업 기록. kind = 단계 이름. core가 가져가 끝냄. core·api가 넣음.
CREATE TABLE pipeline.job (
  id          bigserial   PRIMARY KEY,
  kind        text        NOT NULL CHECK (kind IN ('analysis','summary','archive')),
  target      jsonb       NOT NULL,             -- {"segment_id"} | {"device_id","from","to"} | {"run_id"}
  state       text        NOT NULL DEFAULT 'queued'
                          CHECK (state IN ('queued','running','done','failed')),
  attempts    integer     NOT NULL DEFAULT 0,
  last_error  text,
  run_after   timestamptz NOT NULL DEFAULT now(),
  locked_by   text,
  locked_at   timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX job_claim ON pipeline.job (run_after) WHERE state = 'queued';

-- 등급: 작업 기록. core가 intake.chunk를 어디까지 읽었나. 상태 블록은 묶음과 함께 오므로 읽기 위치가 하나.
CREATE TABLE pipeline.cursor (
  name    text   PRIMARY KEY CHECK (name IN ('chunk')),
  last_id bigint NOT NULL DEFAULT 0
);
INSERT INTO pipeline.cursor VALUES ('chunk', 0);
```

## 002_registry.sql — 사람이 넣는 것. api가 씀

```sql
CREATE SCHEMA registry;

-- 등급: 영구 원본(사람 입력). 장치 하나 = 행 하나. 등록과 토큰을 함께.
CREATE TABLE registry.device (
  id              text        PRIMARY KEY,      -- 'head-e072a1aca858'
  mac             macaddr     NOT NULL UNIQUE,
  token_hash      bytea       UNIQUE,           -- sha256(토큰). NULL = 폐기됨, 재발급 전
  token_issued_at timestamptz,
  registered_at   timestamptz NOT NULL DEFAULT now(),
  note            text
);

-- 등급: 영구 원본(사람 입력). 착용 발은 Tag 번호가 정하므로 여기 없음(공통 규칙 "발").
CREATE TABLE registry.subject (
  id           bigserial PRIMARY KEY,
  code         text      NOT NULL UNIQUE,       -- 표시명. 실명 아님
  height_m     real,
  leg_length_m real,
  paretic_side text      CHECK (paretic_side IN ('left','right'))   -- 뇌졸중 마비측. NULL = 해당 없음/모름
);

-- 등급: 영구 원본(사람 입력). 착용 기간. bout의 주인은 이 표로 정함(analysis.bout_subject).
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
  subject_id    bigint      REFERENCES registry.subject(id),   -- 본인 착용자
  created_at    timestamptz NOT NULL DEFAULT now()
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

## 003_intake.sql — 전송 기록. intake가 씀

```sql
CREATE SCHEMA intake;

-- 등급: 임시 원본(30일). 받은 묶음 하나 = 행 하나. 바이트는 raw 파일에, 여기는 머리 복사 + 파일 위치.
-- 30일 뒤 raw 파일과 함께 archive가 지움. 파일에서 다시 만들 수 있음(tools/reindex_raw.py).
CREATE TABLE intake.chunk (
  id                 bigserial   PRIMARY KEY,   -- core의 읽기 순서(cursor)
  device_id          text        NOT NULL REFERENCES registry.device(id),
  head_boot_id       bigint      NOT NULL,      -- wire u32
  chunk_seq          bigint      NOT NULL,      -- wire u32
  received_at        timestamptz NOT NULL DEFAULT now(),
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
  tag0_mac           macaddr,                   -- 묶음 안 T0의 첫 IMU 프레임. 없으면 NULL
  tag0_boot_id       bigint,
  tag1_mac           macaddr,                   -- T1의 첫 IMU 프레임. 1-Tag 세트는 늘 NULL
  tag1_boot_id       bigint,
  raw_path           text        NOT NULL,      -- '/data/raw/<device>/<boot>/<YYYY-MM-DDTHH>.bin'
  raw_offset         bigint      NOT NULL,      -- 12 B 파일 머리 뒤, 묶음 첫 바이트
  raw_len            integer     NOT NULL,
  UNIQUE (device_id, head_boot_id, chunk_seq)   -- 중복 제거 키
);
CREATE INDEX chunk_device_received ON intake.chunk (device_id, received_at);
CREATE INDEX chunk_path ON intake.chunk (raw_path);              -- 30일 삭제

-- 묶음 안 상태 블록(wire.md "상태 블록"). intake가 끝까지 해독해 검사한 뒤 블록 바이트를 그대로 두고,
-- 조회에 쓰는 값만 열로 뺌. 나머지 필드는 읽는 쪽(api, segment)이 같은 wire 해독기로 풂.
-- 키는 묶음 키. 30일 뒤 intake.chunk가 지워져도 survey는 남아야 하므로 FK 대신 같은 키를 둠.
-- at = 묶음 시각(구간의 time_source 규칙) + 블록 at_offset_us.

-- 등급: 임시 원본(30일). health 블록(1분마다). 30일 삭제 때 같은 묶음의 intake.chunk 행과 함께 지움.
CREATE TABLE intake.health (
  device_id    text        NOT NULL REFERENCES registry.device(id),
  head_boot_id bigint      NOT NULL,
  chunk_seq    bigint      NOT NULL,
  at           timestamptz NOT NULL,
  flags        integer     NOT NULL,            -- wire health flags(UWB 설정, 링크, 버퍼 등)
  buffer_used  integer     NOT NULL,            -- Head 버퍼에 쌓인 묶음 수
  range_age_ms integer,                         -- NULL = 부팅 뒤 거리 없음(wire 0xFFFF)
  imu_age_ms   integer,                         -- NULL = 부팅 뒤 IMU 없음
  block        bytea       NOT NULL,            -- health 블록 payload 그대로(고정 길이)
  PRIMARY KEY (device_id, head_boot_id, chunk_seq)
);
CREATE INDEX health_device_time ON intake.health (device_id, at DESC);

-- 등급: 임시 원본(30일). system 블록(부팅, 설정이 바뀔 때, 10분마다). segment가 풀어 installation을 채움.
CREATE TABLE intake.system (
  device_id     text        NOT NULL REFERENCES registry.device(id),
  head_boot_id  bigint      NOT NULL,
  chunk_seq     bigint      NOT NULL,
  at            timestamptz NOT NULL,
  tag_count     smallint    NOT NULL CHECK (tag_count IN (1,2)),
  geometry_hash bigint      NOT NULL,           -- 블록 geometry_id의 CRC-32. 같은 묶음 머리의 값과 다르면 묶음을 거절
  firmware_id   bytea       NOT NULL CHECK (length(firmware_id) = 8),
  block         bytea       NOT NULL,
  PRIMARY KEY (device_id, head_boot_id, chunk_seq)
);
CREATE INDEX system_device_time ON intake.system (device_id, at DESC);

-- 등급: 영구 원본. survey 블록(배치 측량이 끝날 때). raw 파일이 지워져도 남음.
CREATE TABLE intake.survey (
  device_id    text        NOT NULL REFERENCES registry.device(id),
  head_boot_id bigint      NOT NULL,
  chunk_seq    bigint      NOT NULL,
  at           timestamptz NOT NULL,
  solved       boolean     NOT NULL,
  survey_id    integer     NOT NULL,
  revision     integer     NOT NULL,
  block        bytea       NOT NULL,
  PRIMARY KEY (device_id, head_boot_id, chunk_seq)
);
```

## 004_segment.sql — 구간과 앵커 배치. core가 씀

```sql
CREATE SCHEMA segment;

-- 등급: 결과. 한 방의 앵커 배치. intake.system 블록(좌표·geometry_id)에서 채움. 같은 hash는 같은 좌표.
CREATE TABLE segment.installation (
  id            bigserial   PRIMARY KEY,
  device_id     text        NOT NULL REFERENCES registry.device(id),
  geometry_id   text        NOT NULL,           -- 'sv-<boot>-<survey>-<rev>' | 'manual-…' | 'default'
  geometry_hash bigint      NOT NULL,           -- crc32(geometry_id)
  anchors       jsonb       NOT NULL,           -- [{"id":0,"xyz_m":[x,y,z],"bias_m":0.0}, …] A0–A3
  first_seen_at timestamptz NOT NULL,
  last_seen_at  timestamptz NOT NULL,
  UNIQUE (device_id, geometry_hash)
);

-- 등급: 결과. 단, 30일 뒤에는 다시 만들 수 없으므로 지우지 않음(수신 시각·빈자리 수가 여기에만 남음).
-- 끊김 없이 이어진 묶음들. 묶음과는 (device_id, head_boot_id, seq 범위)로 이어짐. 색인이 30일 뒤 사라지므로 FK 없음.
CREATE TABLE segment.segment (
  id                bigserial   PRIMARY KEY,
  device_id         text        NOT NULL REFERENCES registry.device(id),
  head_boot_id      bigint      NOT NULL,
  sides             text        CHECK (sides IN ('left','right','both')),   -- segment_tag의 요약. NULL = 아직 IMU 없음
  first_seq         bigint      NOT NULL,
  last_seq          bigint      NOT NULL,
  n_chunks          integer     NOT NULL,
  n_missing_chunks  integer     NOT NULL DEFAULT 0,   -- seq 빈자리
  n_dropped_frames  integer     NOT NULL DEFAULT 0,   -- 머리 dropped_* 합
  start_at          timestamptz NOT NULL,
  end_at            timestamptz NOT NULL,
  time_source       text        NOT NULL CHECK (time_source IN ('server','arrival')),
  installation_id   bigint      REFERENCES segment.installation(id),
  state             text        NOT NULL CHECK (state IN ('open','closed')),   -- 분석 여부는 analysis.run으로 앎
  closed_reason     text        CHECK (closed_reason IN
                      ('silence_60s','boot_changed','tag_changed','geometry_changed','max_length')),
                      -- tag_changed: 같은 발 자리의 tag_mac 또는 tag_boot_id가 바뀜(교체·재부팅).
                      -- 새 발 자리가 나타나는 것은 닫지 않음: segment_tag에 행을 더하고 sides를 고침
  last_chunk_at     timestamptz NOT NULL        -- 60초 타이머의 기준
);
CREATE INDEX segment_device_time ON segment.segment (device_id, start_at);
CREATE INDEX segment_seq ON segment.segment (device_id, head_boot_id, first_seq);
CREATE INDEX segment_open ON segment.segment (device_id) WHERE state = 'open';

-- 등급: 결과. 구간에 있던 Tag. 발마다 한 행, 그 발의 프레임이 처음 보일 때 INSERT. 1-Tag 세트는 한 행.
CREATE TABLE segment.segment_tag (
  segment_id    bigint      NOT NULL REFERENCES segment.segment(id),
  side          text        NOT NULL CHECK (side IN ('left','right')),   -- tag_id 0 = left, 1 = right
  tag_mac       macaddr     NOT NULL,
  tag_boot_id   bigint      NOT NULL,
  first_seen_at timestamptz NOT NULL,
  PRIMARY KEY (segment_id, side)
);
CREATE INDEX segment_tag_mac ON segment.segment_tag (tag_mac);   -- wear.tag_mac 귀속
```

## 005_analysis.sql — 분석 결과. core가 씀

```sql
CREATE SCHEMA analysis;

-- 등급: 결과. 구간을 한 번 분석한 기록. 끝날 때 한 번 INSERT. 실패하면 행을 만들지 않음(pipeline.job에 남음).
CREATE TABLE analysis.run (
  id                 bigserial   PRIMARY KEY,
  segment_id         bigint      NOT NULL REFERENCES segment.segment(id),
  input              text        NOT NULL CHECK (input IN ('raw','archive')),   -- 30일 안: raw, 뒤: archive
  origin_at          timestamptz NOT NULL,      -- C2 시각 0의 UTC = 보낸 첫 묶음의 시작
  gaitfusion_version text        NOT NULL,      -- C2 응답의 값
  core_version       text        NOT NULL,      -- 서버 이미지 git sha
  params             jsonb       NOT NULL,      -- C2 요청 context.params
  summary            jsonb       NOT NULL,      -- C2 응답 summary
  status             text        NOT NULL CHECK (status IN ('done','skipped')),
  skip_reason        text,
  started_at         timestamptz NOT NULL,
  finished_at        timestamptz NOT NULL
);
CREATE INDEX run_segment ON analysis.run (segment_id, id DESC);

-- 현재 run = 구간마다 가장 최근에 끝난 run. is_current 열은 없음.
CREATE VIEW analysis.current_run AS
  SELECT DISTINCT ON (segment_id) segment_id, id AS run_id
  FROM analysis.run
  ORDER BY segment_id, id DESC;

-- 등급: 결과. 걷기 구간 = 사람이 걸은 한 동안. 발이 둘이어도 bout는 하나이고, 발마다의 값은 아래 표의 side 행.
-- 주인 열 없음(analysis.bout_subject).
CREATE TABLE analysis.bout (
  id              bigserial   PRIMARY KEY,
  run_id          bigint      NOT NULL REFERENCES analysis.run(id),
  start_at        timestamptz NOT NULL,
  end_at          timestamptz NOT NULL,
  mode            text        NOT NULL CHECK (mode IN ('fused','imu_only')),
  sides           text        NOT NULL CHECK (sides IN ('left','right','both')),   -- 보폭 행이 있는 발
  n_strides       integer     NOT NULL,         -- 한 발 기준 보폭 수. 양발이면 많은 쪽
  distance_m      real,
  duration_s      real        NOT NULL,
  mean_speed_mps  real,
  cadence_spm     real,                         -- 2 × 분당 보폭
  quality         jsonb       NOT NULL          -- C2 응답 quality 그대로
);
CREATE INDEX bout_run ON analysis.bout (run_id);
CREATE INDEX bout_time ON analysis.bout (start_at);

-- bout의 주인. 조회 때 wear와 맞춤. architecture 6절 "귀속"을 SQL로 쓴 것.
-- 속도: 언제나 bout_id나 기간 조건과 함께 씀. 조건은 b.id로 내려가 그 bout만 계산함.
CREATE VIEW analysis.bout_subject AS
WITH m AS (
  SELECT b.id AS bout_id, w.subject_id,
         (w.tag_mac IS NOT NULL) AS exact,
         count(w.id) OVER (PARTITION BY b.id, (w.tag_mac IS NOT NULL)) AS n_same
  FROM analysis.bout b
  JOIN analysis.run r       ON r.id = b.run_id
  JOIN segment.segment s    ON s.id = r.segment_id
  LEFT JOIN registry.wear w ON w.device_id = s.device_id
       AND (w.tag_mac IS NULL OR EXISTS (SELECT 1 FROM segment.segment_tag st
                                         WHERE st.segment_id = s.id AND st.tag_mac = w.tag_mac))
       AND w.started_at <= b.start_at
       AND (w.ended_at IS NULL OR b.start_at < w.ended_at)
)
SELECT DISTINCT ON (bout_id) bout_id,
       CASE WHEN n_same = 1 THEN subject_id END AS subject_id,   -- 같은 우선순위에 둘이면 NULL
       (n_same > 1) AS ambiguous
FROM m
ORDER BY bout_id, exact DESC;                   -- tag_mac 명시 쪽 먼저

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

-- 등급: 결과. 한 발의 보폭 하나(그 발 착지부터 다음 착지까지). 수치 열은 전부 real, NULL = 계산 못 함.
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

-- 등급: 결과. 양발 걸음 하나: 반대 발 착지부터 이 발 착지까지. 양발 bout(sides = 'both')에만 행이 있음.
-- 열은 gait-features.md 8절(좌우 비대칭, 양발 지지, 보폭 너비). 수치는 real, NULL = 계산 못 함.
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

-- 등급: 결과. 걷기 구간 안 10초 창(겹침 50 %), 발마다 그 발의 원시 신호에서 gaitfusion이 계산. 전부 real.
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

-- 등급: 캐시. 궤적은 발마다 TRJ1 파일 하나(architecture 8절), 행은 위치만. 옛 run 것은 새 run이 끝날 때 파일과 함께 지움.
CREATE TABLE analysis.trajectory (
  bout_id  bigint      NOT NULL REFERENCES analysis.bout(id),
  side     text        NOT NULL CHECK (side IN ('left','right')),
  path     text        NOT NULL UNIQUE,         -- '/data/trajectory/<run_id>/<bout_id>-<side>.trj'
  rate_hz  integer     NOT NULL,                -- 100
  t0       timestamptz NOT NULL,
  n        integer     NOT NULL,
  bytes    bigint      NOT NULL,
  PRIMARY KEY (bout_id, side)
);
```

## 006_summary.sql — 일별 요약. core가 씀

```sql
CREATE SCHEMA summary;

-- 일별 요약은 표 셋입니다. 사람 단위(daily, 날마다 한 행), 발 단위(daily_side, 그날 데이터가 있던 발만),
-- 양발 단위(daily_bilateral, 양발 bout가 있던 날만). 열은 gait-features.md 7–8절.
-- 셋 다 current bout들로 계산하고, 다시 계산하면 그 착용자·날짜의 행을 한 트랜잭션에서 지우고 새로 넣음.

-- 등급: 결과. 착용자의 하루, 어느 발을 찼는지와 무관한 값.
CREATE TABLE summary.daily (
  subject_id               bigint  NOT NULL REFERENCES registry.subject(id),
  date                     date    NOT NULL,    -- Asia/Seoul
  sides                    text    CHECK (sides IN ('left','right','both')),   -- 그날 데이터가 있던 발. NULL = 걷기 없음
  -- 양
  wear_hours               real,                -- 앵커 방 안에서 데이터가 있던 시간(segment에서 계산)
  walking_time_s           real,
  n_bouts                  integer,
  n_strides                integer,             -- bout.n_strides 합(한 발 기준)
  est_steps                integer,             -- 2 × n_strides
  distance_m               real,
  -- 구간 구조
  bout_duration_median_s   real,
  bout_duration_p90_s      real,
  longest_bout_s           real,
  share_time_bouts_over_60s real,
  -- 속도(구간 길이별). 양발이면 두 발 보폭을 함께 씀
  speed_median_mps         real,
  speed_p90_mps            real,
  speed_10_30s_median_mps  real,
  speed_30_60s_median_mps  real,
  speed_over_60s_median_mps real,
  stride_length_median_m   real,
  cadence_median_spm       real,
  -- 회전
  n_turns                  integer,
  turn_angle_median_deg    real,
  turn_velocity_median_dps real,
  steps_per_turn_median    real,
  steps_per_turn_cv        real,
  -- 삽화
  n_fog                    integer,
  fog_time_ratio           real,
  -- 품질
  fused_ratio              real,                -- 융합 모드 보폭 비율
  data_sufficient          boolean NOT NULL,    -- wear_hours 기준 통과
  computed_at              timestamptz NOT NULL,
  PRIMARY KEY (subject_id, date)
);

-- 등급: 결과. 한 발의 하루. 그 발의 보폭 행이 있던 날만 행이 있음(1-Tag 착용자는 날마다 한 행).
CREATE TABLE summary.daily_side (
  subject_id               bigint  NOT NULL REFERENCES registry.subject(id),
  date                     date    NOT NULL,
  side                     text    NOT NULL CHECK (side IN ('left','right')),
  n_strides                integer NOT NULL,
  -- 변동성(≥ 30초 구간만)
  stride_time_median_s     real,
  stride_time_cv           real,
  swing_time_cv            real,
  stride_length_median_m   real,
  stride_length_cv         real,
  lateral_dev_sd_m         real,
  -- 보폭 모양
  clearance_median_m       real,
  min_clearance_median_m   real,
  min_clearance_cv         real,
  stance_ratio_median      real,
  -- 신호 질(그 발의 창 평균)
  freeze_index_median      real,
  stride_regularity_median real,
  harmonic_ratio_ap_median real,
  computed_at              timestamptz NOT NULL,
  PRIMARY KEY (subject_id, date, side),
  FOREIGN KEY (subject_id, date) REFERENCES summary.daily(subject_id, date)
);

-- 등급: 결과. 양발 하루. 양발 bout가 있던 날만 행이 있음(1-Tag 착용자는 행이 없음).
-- 비대칭 = |왼 − 오른| ÷ 두 값의 평균, 두 발 중앙값으로 계산. 0 = 대칭. 어느 발이 큰지는 daily_side에서 봄.
CREATE TABLE summary.daily_bilateral (
  subject_id                  bigint  NOT NULL REFERENCES registry.subject(id),
  date                        date    NOT NULL,
  bilateral_walking_time_s    real    NOT NULL,   -- 양발 bout의 걷기 시간
  n_steps                     integer NOT NULL,
  step_length_median_m        real,
  step_length_asymmetry       real,
  step_time_asymmetry         real,
  swing_time_asymmetry        real,
  stance_time_asymmetry       real,
  double_support_ratio_median real,               -- 양발 지지 시간 ÷ 보폭 시간
  double_support_cv           real,               -- ≥ 30초 구간만
  step_width_median_m         real,
  step_width_cv               real,               -- ≥ 30초 구간만
  computed_at                 timestamptz NOT NULL,
  PRIMARY KEY (subject_id, date),
  FOREIGN KEY (subject_id, date) REFERENCES summary.daily(subject_id, date)
);
```

## 007_archive.sql — 걷기 원본. core가 씀

```sql
CREATE SCHEMA archive;
CREATE EXTENSION IF NOT EXISTS btree_gist;       -- 아래 EXCLUDE에서 text 같음 비교용

-- 등급: 영구 원본. 걷기 원본 조각. 파일 하나 = 행 하나. 한 번 쓰고 수정·삭제 없음.
-- 파일에서 다시 만들 수 있음(tools/verify_archive.py --reindex).
CREATE TABLE archive.range (
  id           bigserial   PRIMARY KEY,
  device_id    text        NOT NULL REFERENCES registry.device(id),
  head_boot_id bigint      NOT NULL,
  segment_id   bigint      NOT NULL REFERENCES segment.segment(id),
  first_seq    bigint      NOT NULL,
  last_seq     bigint      NOT NULL,
  n_chunks     integer     NOT NULL,            -- 실제 담긴 수(빈자리 제외)
  start_at     timestamptz NOT NULL,            -- 첫 묶음 시작 UTC. 구간의 time_source 규칙과 같은 방법
  end_at       timestamptz NOT NULL,
  path         text        NOT NULL UNIQUE,     -- '/data/archive/<device>/<YYYY-MM>/<start_at>_<boot>_<first>-<last>.bin'
  bytes        bigint      NOT NULL,
  sha256       bytea       NOT NULL,
  from_run_id  bigint      NOT NULL REFERENCES analysis.run(id),   -- 이 범위를 만든 run
  created_at   timestamptz NOT NULL DEFAULT now(),
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
```

## 008_roles.sql — 프로세스별 권한

```sql
-- 비밀번호는 migrate가 .env 값으로 ALTER ROLE … PASSWORD.
CREATE ROLE intake_rw LOGIN;
CREATE ROLE core_rw   LOGIN;
CREATE ROLE api_rw    LOGIN;
-- 받기 커밋은 디스크 동기화를 기다리지 않음. 행은 raw 파일에서 다시 만듦("속도 요구"의 받기 디스크 동기화).
ALTER ROLE intake_rw SET synchronous_commit = off;

-- intake: 장치 토큰 조회, 자기 표 INSERT(중복 검사용 SELECT 포함).
GRANT USAGE ON SCHEMA registry, intake TO intake_rw;
GRANT SELECT ON registry.device TO intake_rw;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA intake TO intake_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA intake TO intake_rw;

-- core: 전부 읽기. segment·analysis·summary·archive·pipeline 쓰기. 결과와 원본은 추가만.
GRANT USAGE ON SCHEMA pipeline, registry, intake, segment, analysis, summary, archive TO core_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline, registry, intake, segment, analysis, summary, archive TO core_rw;
GRANT INSERT, UPDATE, DELETE ON pipeline.job TO core_rw;          -- DELETE: done 30일 뒤 정리
GRANT UPDATE ON pipeline.cursor TO core_rw;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA segment TO core_rw;  -- 구간 열림·닫힘, 배치 last_seen_at
GRANT INSERT ON ALL TABLES IN SCHEMA analysis TO core_rw;         -- 결과는 추가만
GRANT DELETE ON analysis.trajectory TO core_rw;                   -- 옛 run 궤적 캐시 정리
GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA summary TO core_rw;   -- 다시 계산은 지우고 새로 넣음(발이 줄면 행도 사라져야 함)
GRANT INSERT ON ALL TABLES IN SCHEMA archive TO core_rw;          -- 걷기 원본: 추가만
GRANT DELETE ON intake.chunk, intake.health, intake.system TO core_rw;   -- archive의 30일 삭제만. survey는 영구
GRANT USAGE ON ALL SEQUENCES IN SCHEMA pipeline, segment, analysis, archive TO core_rw;

-- api: registry 쓰기, 작업 넣기, 나머지 읽기.
GRANT USAGE ON SCHEMA pipeline, registry, intake, segment, analysis, summary, archive TO api_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline, registry, intake, segment, analysis, summary, archive TO api_rw;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA registry TO api_rw;
GRANT DELETE ON registry.app_token TO api_rw;                     -- 만료 토큰 정리
GRANT INSERT ON pipeline.job TO api_rw;                           -- 재분석, 착용 변경 뒤 재요약
GRANT USAGE ON ALL SEQUENCES IN SCHEMA registry, pipeline TO api_rw;

-- 아무에게도 없는 권한: archive.* UPDATE·DELETE, intake.* UPDATE, intake.survey DELETE,
-- analysis.*(trajectory 제외) UPDATE·DELETE, registry.*(app_token 제외) DELETE, segment.* DELETE.
```
