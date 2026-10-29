-- SPEC.md 10절 002_registry.sql 그대로. 적용한 뒤에는 고치지 않음(PL-1).

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
