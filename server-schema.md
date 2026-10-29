# 서버 DB 스키마 (SQL)

[server-architecture.md](server-architecture.md) 10절의 관계를 PostgreSQL DDL로 적은 것입니다. 서버 저장소의 `migrations/001–008`로 그대로 옮기는 것을 전제로 썼습니다. 파일 하나 = 단계 하나 = 스키마 하나입니다. 열의 뜻과 근거는 [gait-features.md](../gaitfusion/gait-features.md), 묶음 필드의 뜻은 [wire.md](../uwb-firmware/docs/wire.md)에 있습니다.

아직 Postgres에 올려 보지 않았습니다.

## 공통 규칙

- **단위**: SI. 열 이름 끝에 단위를 붙입니다. `_m`(m), `_mps`(m/s), `_s`(초), `_deg`, `_dps`(deg/s), `_g`, `_hz`, `_spm`(분당 보폭). 접미가 없는 비율은 0–1.
- **NULL은 "계산 못 함" 하나의 뜻**입니다. 0으로 대신 쓰지 않습니다.
- **시각**: DB는 전부 `timestamptz`(UTC). 묶음의 µs 정수(`head_us_start`, `utc_us_start`)만 `bigint`로 그대로 둡니다. 날짜(`date`)는 `Asia/Seoul`.
- **부호 없는 정수**: Postgres에 없으므로 wire의 `u32`는 `bigint`, `u16`은 `integer`에 담습니다.
- **실수**: 보행 지표는 `real`(4 B).
- **열거형**: `text` + `CHECK`. `ENUM` 형은 값 추가가 번거로워 쓰지 않습니다.
- **기본 키**: `bigserial`. 외부에 노출되는 id(앱 API)는 그대로 정수를 씁니다. 추측 가능성이 문제가 되면 그때 `uuid`를 추가합니다.
- **등급**: 표마다 주석 첫 줄에 적습니다(영구 원본 · 임시 원본 · 결과 · 캐시 · 작업 기록). 뜻은 architecture 4절 "데이터 등급".
- **쓰기**: 스키마 = 단계. 단계는 자기 스키마만 씁니다. `008_roles.sql`이 프로세스 단위로 강제합니다.
- **보존**: 전체 원본 30일(raw 파일과 `intake.chunk`를 함께 삭제). 걷기 원본(`/data/archive`, `archive.range`) 영구. 분석 결과 영구, 옛 run의 궤적만 삭제. 삭제 조건은 architecture 6절.
- **결과 표는 추가만**: `analysis` 표에는 UPDATE·DELETE 권한이 아무에게도 없습니다(궤적 위치 행 삭제만 예외). 그래서 FK에 `ON DELETE CASCADE`를 두지 않습니다. 실수로 지우려 하면 FK가 막습니다.
- **궤적**: 파일 `/data/trajectory/<run_id>/<bout_id>.json.gz`(C2 응답의 trajectory 객체를 gzip). 표에는 위치만. api가 통째로 읽어 `rate`에 맞게 줄입니다.
- **일별 요약 확장**: 열 추가 = 마이그레이션. `jsonb` 확장 열을 두지 않습니다.
- **변경**: 적용한 파일은 고치지 않고 `NNN_<단계>_<내용>.sql`을 추가합니다. 표를 만드는 파일은 GRANT도 함께 적습니다. 적용 기록 표 `pipeline.migration(name, applied_at)`은 `migrate`가 처음에 만듭니다.

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

-- 등급: 작업 기록. core가 intake 표를 어디까지 읽었나.
CREATE TABLE pipeline.cursor (
  name    text   PRIMARY KEY CHECK (name IN ('chunk','device_message')),
  last_id bigint NOT NULL DEFAULT 0
);
INSERT INTO pipeline.cursor VALUES ('chunk', 0), ('device_message', 0);
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

-- 등급: 영구 원본(사람 입력).
CREATE TABLE registry.subject (
  id           bigserial PRIMARY KEY,
  code         text      NOT NULL UNIQUE,       -- 표시명. 실명 아님
  height_m     real,
  leg_length_m real,
  side         text      CHECK (side IN ('left','right'))   -- 착용 발
);

-- 등급: 영구 원본(사람 입력). 착용 기간. bout의 주인은 이 표로 정함(analysis.bout_subject).
CREATE TABLE registry.wear (
  id         bigserial   PRIMARY KEY,
  subject_id bigint      NOT NULL REFERENCES registry.subject(id),
  device_id  text        NOT NULL REFERENCES registry.device(id),
  tag_mac    macaddr,                           -- NULL = 그 Head의 아무 Tag
  started_at timestamptz NOT NULL,
  ended_at   timestamptz,                       -- NULL = 착용 중
  paretic    boolean,                           -- 뇌졸중 마비측에 찼는가. NULL = 해당 없음/모름
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
  tag_mac            macaddr,                   -- 첫 IMU 프레임. IMU 없으면 NULL
  tag_boot_id        bigint,
  raw_path           text        NOT NULL,      -- '/data/raw/<device>/<boot>/<YYYY-MM-DDTHH>.bin'
  raw_offset         bigint      NOT NULL,      -- 12 B 파일 머리 뒤, 묶음 첫 바이트
  raw_len            integer     NOT NULL,
  UNIQUE (device_id, head_boot_id, chunk_seq)   -- 중복 제거 키
);
CREATE INDEX chunk_device_received ON intake.chunk (device_id, received_at);
CREATE INDEX chunk_path ON intake.chunk (raw_path);              -- 30일 삭제

-- 등급: survey는 영구 원본, system·health는 임시 원본(30일).
-- POST /d/v1/status 로 온 JSON 그대로. segment가 survey를 해석, api가 health를 바로 읽음.
CREATE TABLE intake.device_message (
  id          bigserial   PRIMARY KEY,
  device_id   text        NOT NULL REFERENCES registry.device(id),
  received_at timestamptz NOT NULL DEFAULT now(),
  kind        text        NOT NULL CHECK (kind IN ('system','health','survey')),
  body        jsonb       NOT NULL
);
CREATE INDEX device_message_lookup ON intake.device_message (device_id, kind, received_at DESC);
```

## 004_segment.sql — 구간과 앵커 배치. core가 씀

```sql
CREATE SCHEMA segment;

-- 등급: 결과. 한 방의 앵커 배치. survey 메시지에서 채움. 같은 hash는 같은 좌표.
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
  tag_mac           macaddr,
  tag_boot_id       bigint,
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
  last_chunk_at     timestamptz NOT NULL        -- 60초 타이머의 기준
);
CREATE INDEX segment_device_time ON segment.segment (device_id, start_at);
CREATE INDEX segment_seq ON segment.segment (device_id, head_boot_id, first_seq);
CREATE INDEX segment_open ON segment.segment (device_id) WHERE state = 'open';
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

-- 등급: 결과. 걷기 구간. 주인 열 없음(analysis.bout_subject).
CREATE TABLE analysis.bout (
  id              bigserial   PRIMARY KEY,
  run_id          bigint      NOT NULL REFERENCES analysis.run(id),
  start_at        timestamptz NOT NULL,
  end_at          timestamptz NOT NULL,
  mode            text        NOT NULL CHECK (mode IN ('fused','imu_only')),
  n_strides       integer     NOT NULL,
  distance_m      real,
  duration_s      real        NOT NULL,
  mean_speed_mps  real,
  cadence_spm     real,                         -- 2 × 분당 보폭
  quality         jsonb       NOT NULL          -- C2 응답 quality 그대로
);
CREATE INDEX bout_run ON analysis.bout (run_id);
CREATE INDEX bout_time ON analysis.bout (start_at);

-- bout의 주인. 조회 때 wear와 맞춤. architecture 6절 "귀속"을 SQL로 쓴 것.
CREATE VIEW analysis.bout_subject AS
WITH m AS (
  SELECT b.id AS bout_id, w.subject_id,
         (w.tag_mac IS NOT NULL) AS exact,
         count(w.id) OVER (PARTITION BY b.id, (w.tag_mac IS NOT NULL)) AS n_same
  FROM analysis.bout b
  JOIN analysis.run r       ON r.id = b.run_id
  JOIN segment.segment s    ON s.id = r.segment_id
  LEFT JOIN registry.wear w ON w.device_id = s.device_id
       AND (w.tag_mac IS NULL OR w.tag_mac = s.tag_mac)
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

-- 등급: 결과. 보폭 하나. 수치 열은 전부 real, NULL = 계산 못 함.
CREATE TABLE analysis.stride (
  id                   bigserial   PRIMARY KEY,
  bout_id              bigint      NOT NULL REFERENCES analysis.bout(id),
  idx                  integer     NOT NULL,    -- 구간 안 순번 0부터
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
  UNIQUE (bout_id, idx),
  FOREIGN KEY (bout_id, turn_id) REFERENCES analysis.turn(bout_id, id)   -- 같은 bout의 turn만
);

-- 등급: 결과. 걷기 구간 안 10초 창(겹침 50 %). 원시 신호에서 gaitfusion이 계산. 전부 real.
CREATE TABLE analysis.bout_window (
  id                      bigserial   PRIMARY KEY,
  bout_id                 bigint      NOT NULL REFERENCES analysis.bout(id),
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

-- 등급: 결과. 삽화. gaitfusion 안의 원시 창 모델 결과.
CREATE TABLE analysis.gait_event (
  id             bigserial   PRIMARY KEY,
  bout_id        bigint      NOT NULL REFERENCES analysis.bout(id),
  start_at       timestamptz NOT NULL,
  end_at         timestamptz NOT NULL,
  type           text        NOT NULL CHECK (type IN ('fog')),
  score          real        NOT NULL,          -- 0–1
  method_version text        NOT NULL           -- 'fi-1.0', 'cnn-0.3' …
);

-- 등급: 캐시. 궤적은 파일, 행은 위치만. 옛 run 것은 새 run이 끝날 때 파일과 함께 지움.
CREATE TABLE analysis.trajectory (
  bout_id  bigint      PRIMARY KEY REFERENCES analysis.bout(id),
  path     text        NOT NULL UNIQUE,         -- '/data/trajectory/<run_id>/<bout_id>.json.gz'
  rate_hz  integer     NOT NULL,                -- 100
  t0       timestamptz NOT NULL,
  n        integer     NOT NULL,
  bytes    bigint      NOT NULL
);
```

## 006_summary.sql — 일별 요약. core가 씀

```sql
CREATE SCHEMA summary;

-- 등급: 결과. 착용자의 하루. current bout들로 계산하고 다시 계산하면 덮어씀. 열은 gait-features.md 7절.
CREATE TABLE summary.daily (
  subject_id               bigint  NOT NULL REFERENCES registry.subject(id),
  date                     date    NOT NULL,    -- Asia/Seoul
  -- 양
  wear_hours               real,                -- 앵커 방 안에서 데이터가 있던 시간(segment에서 계산)
  walking_time_s           real,
  n_bouts                  integer,
  n_strides                integer,
  est_steps                integer,             -- 2 × n_strides
  distance_m               real,
  -- 구간 구조
  bout_duration_median_s   real,
  bout_duration_p90_s      real,
  longest_bout_s           real,
  share_time_bouts_over_60s real,
  -- 속도(구간 길이별)
  speed_median_mps         real,
  speed_p90_mps            real,
  speed_10_30s_median_mps  real,
  speed_30_60s_median_mps  real,
  speed_over_60s_median_mps real,
  stride_length_median_m   real,
  cadence_median_spm       real,
  -- 변동성(≥ 30초 구간만)
  stride_time_median_s     real,
  stride_time_cv           real,
  swing_time_cv            real,
  stride_length_cv         real,
  lateral_dev_sd_m         real,
  -- 보폭 모양
  clearance_median_m       real,
  min_clearance_median_m   real,
  min_clearance_cv         real,
  stance_ratio_median      real,
  -- 회전
  n_turns                  integer,
  turn_angle_median_deg    real,
  turn_velocity_median_dps real,
  steps_per_turn_median    real,
  steps_per_turn_cv        real,
  -- 신호 질(창 평균)
  freeze_index_median      real,
  stride_regularity_median real,
  harmonic_ratio_ap_median real,
  -- 삽화
  n_fog                    integer,
  fog_time_ratio           real,
  -- 품질
  fused_ratio              real,                -- 융합 모드 보폭 비율
  data_sufficient          boolean NOT NULL,    -- wear_hours 기준 통과
  computed_at              timestamptz NOT NULL,
  PRIMARY KEY (subject_id, date)
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

-- intake: 장치 토큰 조회, 자기 표 INSERT(중복 검사용 SELECT 포함).
GRANT USAGE ON SCHEMA registry, intake TO intake_rw;
GRANT SELECT ON registry.device TO intake_rw;
GRANT SELECT, INSERT ON intake.chunk, intake.device_message TO intake_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA intake TO intake_rw;

-- core: 전부 읽기. segment·analysis·summary·archive·pipeline 쓰기. 결과와 원본은 추가만.
GRANT USAGE ON SCHEMA pipeline, registry, intake, segment, analysis, summary, archive TO core_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline, registry, intake, segment, analysis, summary, archive TO core_rw;
GRANT INSERT, UPDATE, DELETE ON pipeline.job TO core_rw;          -- DELETE: done 30일 뒤 정리
GRANT UPDATE ON pipeline.cursor TO core_rw;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA segment TO core_rw;  -- 구간 열림·닫힘, 배치 last_seen_at
GRANT INSERT ON ALL TABLES IN SCHEMA analysis TO core_rw;         -- 결과는 추가만
GRANT DELETE ON analysis.trajectory TO core_rw;                   -- 옛 run 궤적 캐시 정리
GRANT INSERT, UPDATE ON summary.daily TO core_rw;                 -- 다시 계산은 덮어씀
GRANT INSERT ON ALL TABLES IN SCHEMA archive TO core_rw;          -- 걷기 원본: 추가만
GRANT DELETE ON intake.chunk, intake.device_message TO core_rw;   -- archive의 30일 삭제만
GRANT USAGE ON ALL SEQUENCES IN SCHEMA pipeline, segment, analysis, archive TO core_rw;

-- api: registry 쓰기, 작업 넣기, 나머지 읽기.
GRANT USAGE ON SCHEMA pipeline, registry, intake, segment, analysis, summary, archive TO api_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline, registry, intake, segment, analysis, summary, archive TO api_rw;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA registry TO api_rw;
GRANT DELETE ON registry.app_token TO api_rw;                     -- 만료 토큰 정리
GRANT INSERT ON pipeline.job TO api_rw;                           -- 재분석, 착용 변경 뒤 재요약
GRANT USAGE ON ALL SEQUENCES IN SCHEMA registry, pipeline TO api_rw;

-- 아무에게도 없는 권한: archive.* UPDATE·DELETE, intake.* UPDATE,
-- analysis.*(trajectory 제외) UPDATE·DELETE, registry.*(app_token 제외) DELETE.
```
