-- SPEC.md 10절 001_pipeline.sql 그대로. 적용한 뒤에는 고치지 않음(PL-1).

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
