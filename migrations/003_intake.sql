-- SPEC.md 10절 003_intake.sql 그대로. 적용한 뒤에는 고치지 않음(PL-1).

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
