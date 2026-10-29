-- SPEC.md 10절 006_archive.sql 그대로. 적용한 뒤에는 고치지 않음(PL-1).

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
