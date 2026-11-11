-- SPEC.md 14.9 009_feature.sql 그대로. 적용한 뒤에는 고치지 않음(PL-1).

CREATE SCHEMA feature;

-- 작업 종류 feature를 더함. 대상은 archive처럼 run 하나.
ALTER TABLE pipeline.job DROP CONSTRAINT job_kind_check;
ALTER TABLE pipeline.job ADD CONSTRAINT job_kind_check CHECK (kind IN ('analysis','archive','feature'));
ALTER TABLE pipeline.job DROP CONSTRAINT job_check1;
ALTER TABLE pipeline.job ADD CONSTRAINT job_run_target CHECK ((kind IN ('archive','feature')) = (run_id IS NOT NULL));
CREATE UNIQUE INDEX job_one_feature ON pipeline.job (run_id) WHERE kind = 'feature' AND state IN ('queued','running');

-- 등급: 결과(결과 원문 파일에서 다시 만들 수 있음). run 하나를 푼 기록. 다시 풀면 이 행과 그 run의 record 행을 지우고 새로 넣음.
CREATE TABLE feature.unpack (
  run_id            bigint      PRIMARY KEY REFERENCES analysis.run(id),
  catalog_version   integer     NOT NULL,       -- 풀 때의 지표 목록 판(아래 세 값은 이 판으로 본 것)
  formats           jsonb       NOT NULL,       -- 응답이 밝힌 표 형식. 예 {"strides": "gaitfusion.stride/1"}
  n_records         integer     NOT NULL,       -- 넣은 줄 수
  n_dropped_records integer     NOT NULL,       -- 시작·끝·발이 틀려 버린 줄 수
  n_bad_values      integer     NOT NULL,       -- 지표 목록의 형이나 범위에 맞지 않은 값 수(값은 그대로 저장됨)
  unknown_fields    text[]      NOT NULL,       -- 지표 목록에 없던 필드. '<표>.<필드>' 꼴
  unknown_tables    text[]      NOT NULL,       -- 지표 목록에 없던 표(풀지 않음)
  core_version      text        NOT NULL,
  unpacked_at       timestamptz NOT NULL
);

-- 등급: 결과. 분석 결과의 줄 하나: 보폭 하나, 걸음 하나, 회전 하나, 10초 창 하나, 삽화 하나.
-- 값은 gaitfusion이 낸 그대로 v에 둠. 이름·단위·뜻은 읽을 때 지표 목록이 정함(MC-4).
CREATE TABLE feature.record (
  id        bigserial   PRIMARY KEY,
  bout_id   bigint      NOT NULL REFERENCES analysis.bout(id),
  kind      text        NOT NULL,               -- 'stride'·'step'·'turn'·'window'·'event'와 지표 목록이 더하는 것. CHECK 없음
  idx       integer     NOT NULL,               -- 응답 배열 안의 순번, 0부터
  side      text        CHECK (side IN ('left','right')),   -- NULL = 발 구분 없음
  start_at  timestamptz NOT NULL,
  end_at    timestamptz NOT NULL,
  v         jsonb       NOT NULL,               -- 응답의 그 줄 전체. 조회 조건에 쓰지 않음(9절)
  UNIQUE (bout_id, kind, idx)                   -- 걷기 구간의 줄을 읽는 색인을 겸함
);

GRANT USAGE ON SCHEMA feature TO core_rw, api_rw;
GRANT SELECT, INSERT, DELETE ON ALL TABLES IN SCHEMA feature TO core_rw;   -- 다시 풀기는 지우고 새로 넣음
GRANT SELECT ON ALL TABLES IN SCHEMA feature TO api_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA feature TO core_rw;
