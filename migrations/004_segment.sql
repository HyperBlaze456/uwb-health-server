-- SPEC.md 10절 004_segment.sql 그대로. 적용한 뒤에는 고치지 않음(PL-1).

CREATE SCHEMA segment;
CREATE EXTENSION IF NOT EXISTS btree_gist;       -- EXCLUDE에서 text·bigint 같음 비교용

-- 등급: 결과. 한 방의 앵커 배치. system 블록에서 채움(SG-8). 같은 hash는 같은 좌표.
CREATE TABLE segment.installation (
  id            bigserial   PRIMARY KEY,
  device_id     text        NOT NULL REFERENCES registry.device(id),
  geometry_hash bigint      NOT NULL,           -- crc32(geometry_id)
  geometry_id   text        NOT NULL,           -- 'default' | 'sv-<boot>-<survey>-<rev>' | 'manual-<boot>-<n>'
  source        text        NOT NULL CHECK (source IN ('default','survey','manual')),
  surveyed      boolean     NOT NULL,           -- system flags bit0. false면 C2에 anchors = null
  anchors       jsonb       NOT NULL,           -- [{"id":0,"xyz_m":[x,y,z],"bias_m":0.0}, …] A0–A3. C2 문맥용
  first_seen_at timestamptz NOT NULL,
  last_seen_at  timestamptz NOT NULL,
  UNIQUE (device_id, geometry_hash)
);

-- 등급: 결과. 단, 30일 뒤에는 다시 만들 수 없으므로 지우지 않음(수신 시각·빈자리 수가 여기에만 남음).
-- 한 부팅 안에서 끊김 없이 이어진 묶음들(SG-3). 묶음과는 (device_id, head_boot_id, seq 범위)로 이어짐.
CREATE TABLE segment.segment (
  id                bigserial   PRIMARY KEY,
  device_id         text        NOT NULL REFERENCES registry.device(id),
  head_boot_id      bigint      NOT NULL,
  geometry_hash     bigint      NOT NULL,
  installation_id   bigint      REFERENCES segment.installation(id),   -- NULL = 이 hash의 system 블록을 아직 못 봄
  sides             text        NOT NULL CHECK (sides IN ('left','right','both')),   -- segment_tag의 요약
  first_seq         bigint      NOT NULL,
  last_seq          bigint      NOT NULL,
  n_chunks          integer     NOT NULL,
  n_missing_chunks  integer     NOT NULL DEFAULT 0,   -- seq 빈자리
  n_dropped_frames  integer     NOT NULL DEFAULT 0,   -- 머리 dropped_* 합
  first_head_us     bigint      NOT NULL,             -- 첫 묶음의 head_us_start
  last_head_us      bigint      NOT NULL,             -- seq가 가장 큰 묶음의 head_us_start
  utc_offset_us     bigint      NOT NULL,             -- UTC µs = head_us + 이 값(SG-7)
  time_source       text        NOT NULL CHECK (time_source IN ('server','arrival')),
  start_at          timestamptz NOT NULL,
  end_at            timestamptz NOT NULL,
  state             text        NOT NULL CHECK (state IN ('open','closed')),   -- 분석 여부는 analysis.run으로 앎
  closed_reason     text        CHECK (closed_reason IN
                      ('silence_60s','gap_60s','boot_changed','tag_changed','geometry_changed','max_length')),
  revision          integer     NOT NULL DEFAULT 0,   -- 묶음이 더해질 때마다 +1. run이 본 것과 비교
  last_chunk_at     timestamptz NOT NULL,             -- 마지막으로 더한 묶음의 received_at(침묵 60초 기준)
  CHECK (last_seq >= first_seq),
  CHECK ((state = 'closed') = (closed_reason IS NOT NULL)),
  EXCLUDE USING gist (device_id WITH =, head_boot_id WITH =,
                      int8range(first_seq, last_seq, '[]') WITH &&)   -- 같은 부팅 안 seq 겹침 금지
);
CREATE INDEX segment_device_time ON segment.segment (device_id, start_at);
CREATE INDEX segment_seq ON segment.segment (device_id, head_boot_id, first_seq);
CREATE INDEX segment_open ON segment.segment (device_id) WHERE state = 'open';
CREATE INDEX segment_no_installation ON segment.segment (device_id, geometry_hash) WHERE installation_id IS NULL;

-- 등급: 결과. 구간에 있던 발과 Tag. 발마다 한 행(SG-6). 1-Tag 세트는 한 행.
CREATE TABLE segment.segment_tag (
  segment_id    bigint      NOT NULL REFERENCES segment.segment(id),
  side          text        NOT NULL CHECK (side IN ('left','right')),   -- tag_id 0 = right, 1 = left
  tag_mac       macaddr,                          -- NULL = 이 발은 아직 거리 프레임만(IMU 프레임 없음)
  tag_boot_id   bigint,
  first_seen_at timestamptz NOT NULL,
  PRIMARY KEY (segment_id, side),
  CHECK ((tag_mac IS NULL) = (tag_boot_id IS NULL))
);
CREATE INDEX segment_tag_mac ON segment.segment_tag (tag_mac);   -- wear.tag_mac 귀속
