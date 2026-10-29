"""SG-9 귀속 뷰가 처음 SPEC의 정의(DISTINCT ON + 창 함수)와 같은 답을 내는지 경계 사례로 확인.

뷰는 속도 때문에(9절) LATERAL 꼴로 다시 썼고, 규칙은 그대로여야 합니다.
"""

import pytest

pytestmark = pytest.mark.db

ORIGINAL_BOUT_SUBJECT = """
WITH m AS (
  SELECT b.id AS bout_id, w.subject_id,
         (w.tag_mac IS NOT NULL) AS exact,
         count(w.id) OVER (PARTITION BY b.id, (w.tag_mac IS NOT NULL)) AS n_same
  FROM analysis.bout b
  JOIN analysis.run r       ON r.id = b.run_id
  JOIN segment.segment s    ON s.id = r.segment_id
  LEFT JOIN registry.wear w ON w.device_id = s.device_id
       AND (w.tag_mac IS NULL OR EXISTS (SELECT 1 FROM segment.segment_tag st
                                         WHERE st.segment_id = s.id AND st.tag_mac = w.tag_mac))
       AND w.started_at <= b.start_at
       AND (w.ended_at IS NULL OR b.start_at < w.ended_at)
)
SELECT DISTINCT ON (bout_id) bout_id,
       CASE WHEN n_same = 1 THEN subject_id END AS subject_id,
       (n_same > 1) AS ambiguous
FROM m
ORDER BY bout_id, exact DESC
"""

ORIGINAL_SEGMENT_SUBJECT = ORIGINAL_BOUT_SUBJECT.replace("b.id AS bout_id", "s.id AS segment_id").replace(
    "PARTITION BY b.id", "PARTITION BY s.id"
).replace("b.start_at", "s.start_at").replace("DISTINCT ON (bout_id) bout_id", "DISTINCT ON (segment_id) segment_id").replace(
    "ORDER BY bout_id", "ORDER BY segment_id"
).replace(
    "FROM analysis.bout b\n  JOIN analysis.run r       ON r.id = b.run_id\n  JOIN segment.segment s    ON s.id = r.segment_id",
    "FROM segment.segment s",
)

# (tag_mac, 시작 시, 끝 시 또는 None, 착용자)
CASES = {
    "none": [],
    "one_general": [(None, 0, None, 1)],
    "ended_before": [(None, 0, 5, 1)],
    "starts_after": [(None, 20, None, 1)],
    "two_general_ambiguous": [(None, 0, None, 1), (None, 0, None, 2)],
    "exact_beats_general": [(None, 0, None, 1), ("aa:00:00:00:00:01", 0, None, 2)],
    "two_exact_ambiguous": [("aa:00:00:00:00:01", 0, None, 1), ("aa:00:00:00:00:01", 0, None, 2)],
    "exact_other_tag_ignored": [("bb:00:00:00:00:09", 0, None, 2), (None, 0, None, 1)],
    "end_equals_start_excluded": [(None, 0, 10, 1), (None, 10, None, 2)],
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_same_as_original(env, case):
    q = env.q
    q("INSERT INTO registry.device (id, mac, registered_at) VALUES ('head-aa0000000000', 'aa:00:00:00:00:00', now())")
    q("INSERT INTO registry.subject (code) VALUES ('s1'), ('s2')")
    q(
        """
        INSERT INTO segment.segment (device_id, head_boot_id, geometry_hash, sides, first_seq, last_seq, n_chunks,
          first_head_us, last_head_us, utc_offset_us, time_source, start_at, end_at, state, closed_reason, last_chunk_at)
        VALUES ('head-aa0000000000', 1, 1, 'left', 0, 99, 100, 0, 99000000, 0, 'server',
                '2026-10-26 10:00Z', '2026-10-26 11:00Z', 'closed', 'silence_60s', '2026-10-26 11:00Z')
        """
    )
    q("INSERT INTO segment.segment_tag VALUES (1, 'left', 'aa:00:00:00:00:01', 1, '2026-10-26 10:00Z')")
    q(
        """
        INSERT INTO analysis.run (segment_id, segment_revision, input, n_chunks_sent, origin_head_us, origin_at,
          core_version, context, status, skip_reason, started_at, finished_at)
        VALUES (1, 0, 'raw', 100, 0, '2026-10-26 10:00Z', 'x', '{}', 'skipped', 'r', now(), now()),
               (1, 0, 'raw', 100, 0, '2026-10-26 10:00Z', 'x', '{}', 'skipped', 'r', now(), now())
        """
    )
    q(
        """
        INSERT INTO analysis.bout (run_id, idx, start_at, end_at, mode, sides, quality)
        VALUES (1, 0, '2026-10-26 10:05Z', '2026-10-26 10:06Z', 'fused', 'left', '{}'),
               (2, 0, '2026-10-26 10:10Z', '2026-10-26 10:11Z', 'fused', 'left', '{}')
        """
    )
    for mac, start_h, end_h, subj in CASES[case]:
        q(
            "INSERT INTO registry.wear (subject_id, device_id, tag_mac, started_at, ended_at) VALUES "
            "(%s, 'head-aa0000000000', %s, '2026-10-26 00:00Z'::timestamptz + %s * interval '1 hour', "
            "'2026-10-26 00:00Z'::timestamptz + %s * interval '1 hour')",
            (subj, mac, start_h, end_h),
        )
    assert q("SELECT * FROM analysis.bout_subject ORDER BY bout_id") == q(ORIGINAL_BOUT_SUBJECT)
    assert q("SELECT * FROM analysis.segment_subject ORDER BY segment_id") == q(ORIGINAL_SEGMENT_SUBJECT)
    assert q("SELECT * FROM analysis.current_run") == [{"segment_id": 1, "run_id": 2}]
