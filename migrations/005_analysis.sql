-- SPEC.md 10절 005_analysis.sql 그대로. 적용한 뒤에는 고치지 않음(PL-1).

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
