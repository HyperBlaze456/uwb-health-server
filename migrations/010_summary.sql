-- SPEC.md 14.9 010_summary.sql 그대로. 적용한 뒤에는 고치지 않음(PL-1).

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
