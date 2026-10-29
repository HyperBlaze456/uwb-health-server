"""USB 기록 넣기 (SPEC TL-7). 아직 만들지 않음: M3 뒤에 만듭니다(SPEC 12절 순서 9).

펌웨어의 Head USB 기록(JSONL, ../uwb-firmware/docs/protocol.md)을 wire v1 1초 묶음으로 바꿔 intake에 보냅니다.
Head 전송부가 생기기 전에 실제 기록을 서버에 넣는 길입니다. DB에는 접속하지 않고 HTTP만 씁니다(TL 끝 문단).

계획한 명령:

    uv run python tools/import_usb.py <기록.jsonl> --device MAC=TOKEN --utc-start 2026-10-20T05:12:00Z
        [--url http://localhost:8001] [--dry-run]

계획한 변환 (protocol.md → wire.md):

1. 읽기. 한 줄 = JSON 객체 하나. 공통 머리 schema, type, head_id, boot_id(8자리 16진), packet_seq, head_rx_us.
   type이 range, imu_batch, system인 줄만 씁니다. imu(UWB DATA 경로, 기본 꺼짐, tag_mac·tag_boot_id 없음),
   health, survey, identity와 모르는 type은 건너뜁니다(protocol.md: 모르는 type은 오류가 아님).
   health 블록은 만들지 않습니다(TL-7). survey 블록도 TL-7 범위 밖입니다.
   packet_seq가 1보다 크게 뛰면 USB 출력에서 빠진 이벤트(health.usb_dropped_packets)이므로 경고만 남깁니다.

2. 부팅과 묶음 나누기. boot_id마다 따로 다룹니다: head_boot_id = int(boot_id, 16).
   그 부팅의 첫 이벤트 head_rx_us를 t0로 두고, 이벤트를 k = (head_rx_us − t0) // 1초 창에 넣습니다.
   chunk_seq = k, head_us_start = t0 + k초. 이벤트가 없는 1초도 프레임 없는 묶음으로 만들어(실제 Head와 같음)
   seq와 Head 시각이 어긋나지 않게 합니다(서버는 프레임 없는 묶음을 구간에 넣지 않음, SG-2).
   같은 파일을 다시 넣으면 같은 묶음이 나오므로 서버가 duplicate(1)로 걸러 냅니다(멱등).
   range의 geometry_id가 창 중간에 바뀌면 그 이벤트에서 묶음을 닫고 새 묶음을 시작합니다
   (wire.md: 한 묶음의 프레임은 모두 같은 배치). 그 뒤의 창은 새 시작점에서 1초씩 다시 셉니다.

3. range 이벤트 → 거리 프레임 24 B.
   rx_offset_us = head_rx_us − head_us_start, round_seq, slot_mask, tag_id 그대로.
   byte 9 = valid_mask | rssi_mask << 4.
   report_flags = reported_mask | explicit_anchor_ids << 4 | configuration_verified << 5.
   raw_cm[i] = ranges[i].raw_cm(null이면 0, ±32767로 포화), rssi_dbm[i] = ranges[i].rssi_dbm(null이면 0).
   position, imu_ref, corrected_m, weak_rssi, timing은 보내지 않습니다(서버·gaitfusion이 다시 계산).
   머리 geometry_hash = geometry_id ASCII의 CRC-32.

4. imu_batch 이벤트 → IMU 프레임 24 + 21 × count B.
   rx_offset_us = head_rx_us − head_us_start, tag_mac, tag_boot_id, link_seq, tx_tag_ms, link_rssi, tag_id, count,
   표본마다 (sample_seq, tag_ms, flags, raw[7]) 그대로.
   fs = AFS_SEL | FS_SEL << 4. accel_lsb_per_g 16384/8192/4096/2048 → 0/1/2/3,
   gyro_lsb_per_dps 131/65.5/32.8/16.4 → 0/1/2/3. 스케일 필드가 없는 예전 기록은 4096 LSB/g(±8 g)입니다.

5. system 이벤트 → system 블록 152 B(kind 1). at_offset_us = head_rx_us − head_us_start.
   head_mac(device_id의 12자리, --device의 MAC과 같아야 함), tag_count, slot_ms, position_dimensions,
   flags(coordinates_surveyed, module_filter, imu_transport: none 0 / espnow 1 / uwb_data 2), geometry_source
   (default 0 / survey 1 / manual 2), fs, imu_acquisition_hz, imu_transport_hz, set_members(A1–A3, T0–T1 MAC,
   null → 0), anchors(A0–A3의 x, y, z, range_bias_m), firmware_id(16진 문자열 앞 8 바이트, unversioned → 0),
   geometry_id. USB는 system을 10초마다 내지만 Head 규칙(wire.md "상태 블록을 넣는 때")대로 부팅 뒤 첫 묶음,
   좌표나 세트 구성이 바뀐 직후, 그리고 10분마다만 넣습니다. 한 묶음에 둘이면 나중 것만 넣습니다.
   protocol.md의 system JSON 필드 이름은 실제 기록 파일로 맞춰 보고 정합니다.

6. 묶음 머리. magic 0xC1, version 1, header_len 48, dropped_range = dropped_imu_frames = 0, flags bit1 = 0,
   tag_mask = 프레임들의 tag_id 집합, status_len = 블록 길이 합, reserved 0, 끝에 CRC-32.
   한 묶음의 거리 프레임이 128개, IMU 프레임이 32개를 넘으면 넘는 것을 버리고 dropped_*에 셉니다(Head와 같음).

7. 시각. USB 기록에는 벽시계가 없습니다. --utc-start(첫 이벤트의 UTC)를 꼭 받아
   utc_us_start = head_us_start + (utc_start − t0)로 채우고 flags bit0 = 1로 둡니다. 이 값이 없으면 서버가
   수신 시각 − 1초를 묶음 시각으로 쓰므로(SG-7) 모든 기록이 넣은 시각에 일어난 것처럼 보입니다. 그래서 실행을
   거부합니다. wire 검사 때문에 utc_us_start는 2025-01-01 이후이고 서버 시각 + 1일 이전이어야 합니다.

8. 보내기 전 검사. 묶음마다 서버의 검사기(uwbserver.pipeline.wire.check_chunk)를 돌려 거절될 묶음을 미리
   코드별로 셉니다. 인코더는 tools/fake_head.py의 것(서버 해독기와 따로 쓴 구현)을 씁니다.
   --dry-run은 여기까지만 하고 보내지 않습니다.

9. 보내기. POST /d/v1/chunks, Bearer = --device의 토큰. 요청 하나에 묶음 16개까지, 본문 131,072 B까지,
   부팅별 seq 순서로. 5xx·시간 초과는 1, 2, 4, 8, 16, 30초 뒤 다시 보냄(wire.md 다시 보내기 규칙),
   401이면 멈춤. 끝에 응답 코드별 수, 부팅별 묶음 수, 첫·끝 UTC를 출력합니다.

주의: 같은 부팅을 실제 Head가 직접 올린 적이 있으면 묶음 경계가 달라도 키(부팅, seq)가 겹쳐 duplicate로
버려집니다. 지금은 Head 전송부가 없어 생기지 않지만, 그 뒤에는 둘 중 한 길만 씁니다.

종료 코드(계획): 0 = 모두 0·1, 1 = 거절된 묶음이 있음, 2 = 인자 오류·보내기 실패. 지금은 늘 2입니다.
"""

import argparse
import sys
from collections.abc import Sequence

NOT_IMPLEMENTED = "import_usb.py는 아직 만들지 않았습니다 (SPEC TL-7: M3 뒤에 만듦). 계획은 이 파일의 docstring에 있습니다."


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="import_usb.py",
        description="Head USB 기록(JSONL)을 wire v1 묶음으로 바꿔 intake에 보냅니다(TL-7). 아직 만들지 않음.",
        epilog=NOT_IMPLEMENTED,
    )
    p.add_argument("file", help="Head USB 기록 JSONL 파일")
    p.add_argument("--device", required=True, metavar="MAC=TOKEN", help="Head MAC과 장치 토큰")
    p.add_argument("--utc-start", required=True, metavar="ISO", help="기록 첫 이벤트의 UTC 시각")
    p.add_argument("--url", default="http://localhost:8001", help="intake 주소(기본 %(default)s)")
    p.add_argument("--dry-run", action="store_true", help="묶음을 만들어 검사만 하고 보내지 않음")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    print(NOT_IMPLEMENTED, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
