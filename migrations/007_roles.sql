-- SPEC.md 10절 007_roles.sql 그대로. 적용한 뒤에는 고치지 않음(PL-1).

-- 비밀번호는 migrate가 .env 값으로 ALTER ROLE … PASSWORD(PL-1).
-- 역할은 클러스터 전체에 하나. 같은 클러스터의 다른 DB가 이미 만들었으면 건너뜀.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'intake_rw') THEN CREATE ROLE intake_rw LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'core_rw')   THEN CREATE ROLE core_rw   LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'api_rw')    THEN CREATE ROLE api_rw    LOGIN; END IF;
END $$;
-- 받기 커밋은 디스크 동기화를 기다리지 않음. 행은 raw 파일에서 다시 만듦(IN-9).
ALTER ROLE intake_rw SET synchronous_commit = off;

-- intake: 장치 토큰 조회, 자기 표 INSERT(중복 확인용 SELECT 포함).
GRANT USAGE ON SCHEMA registry, intake TO intake_rw;
GRANT SELECT ON registry.device TO intake_rw;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA intake TO intake_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA intake TO intake_rw;
GRANT USAGE ON SCHEMA archive TO intake_rw;
GRANT SELECT ON archive.purged TO intake_rw;                       -- 재색인이 삭제가 처리한 파일을 건너뜀

-- core: 전부 읽기. segment·analysis·archive·pipeline 쓰기. 결과와 영구 원본은 추가만.
GRANT USAGE ON SCHEMA pipeline, registry, intake, segment, analysis, archive TO core_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline, registry, intake, segment, analysis, archive TO core_rw;
GRANT INSERT, UPDATE, DELETE ON pipeline.job TO core_rw;          -- DELETE: done 30일 뒤 정리
GRANT UPDATE ON pipeline.cursor TO core_rw;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA segment TO core_rw;  -- 구간 열림·닫힘, 배치 last_seen_at, Tag MAC 채움
GRANT INSERT ON ALL TABLES IN SCHEMA analysis TO core_rw;         -- 결과는 추가만
GRANT DELETE ON analysis.trajectory TO core_rw;                   -- 옛 run 궤적 캐시 정리
GRANT INSERT ON ALL TABLES IN SCHEMA archive TO core_rw;          -- 걷기 원본: 추가만
GRANT DELETE ON intake.chunk, intake.health, intake.system, intake.rejected TO core_rw;   -- 30일 삭제만. survey는 영구
GRANT USAGE ON ALL SEQUENCES IN SCHEMA pipeline, segment, analysis, archive TO core_rw;

-- api: registry 쓰기, 재분석 작업 넣기, 나머지 읽기.
GRANT USAGE ON SCHEMA pipeline, registry, intake, segment, analysis, archive TO api_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline, registry, intake, segment, analysis, archive TO api_rw;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA registry TO api_rw;
GRANT DELETE ON registry.app_token TO api_rw;                     -- 만료 토큰 정리
GRANT INSERT ON pipeline.job TO api_rw;                           -- 재분석
GRANT USAGE ON ALL SEQUENCES IN SCHEMA registry, pipeline TO api_rw;

-- 아무에게도 없는 권한: archive.* UPDATE·DELETE, intake.* UPDATE, intake.survey DELETE,
-- analysis.*(trajectory 제외) UPDATE·DELETE, registry.*(app_token 제외) DELETE, segment.* DELETE.
