-- SPEC.md 10절 008_analysis_result_status.sql 그대로. 적용한 뒤에는 고치지 않음(PL-1).

-- 응답 result.status(7.2절). 'partial' = gaitfusion이 자기 마감 시각에 끊겨 궤적 일부가 빠짐(걷기 구간 목록은 온전).
-- NULL = 응답에 없음(옛 gaitfusion), skipped run, 이 파일 전에 만든 run.
-- 표에 열만 더하므로 새 GRANT는 없음(007의 표 권한이 새 열에도 미침).
ALTER TABLE analysis.run
  ADD COLUMN result_status text CHECK (result_status IN ('complete','partial')),
  ADD CHECK (status = 'done' OR result_status IS NULL);
