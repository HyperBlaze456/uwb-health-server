"""진짜 gaitfusion 전 구간 시험의 입력(walk_two_feet.c1.gz, walk_two_feet.json)을 만듭니다.

gaitfusion 저장소의 합성 보행(발목 궤적에서 IMU와 UWB 거리를 계산)으로 C1 묶음을 만들고, 서버가 받도록
system 블록(시험 장치 MAC, 측정된 앵커, Tag MAC)을 넣습니다. 가짜 Head(tools/fake_head.py)의 보행 신호는
형식 시험용이라 진짜 서비스가 걸음으로 알아보지 못하므로 이 파일을 따로 둡니다.

서버 코드와 시험은 이 스크립트를 import하지 않습니다. 결과 파일만 씁니다. 다시 만들 때는 gaitfusion의 환경으로
(gaitfusion 저장소가 이 저장소 옆에 있어야 함):

    cd ../gaitfusion && .venv/bin/python ../uwb-health-server/tests/e2e/data/make_walk_two_feet.py

같은 gaitfusion 판이면 같은 바이트가 나옵니다(합성기의 난수 씨앗 고정, gzip 시각 0).
"""

import gzip
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parents[2]
GAITFUSION = SERVER.parent / "gaitfusion"
sys.path[:0] = [str(GAITFUSION), str(SERVER)]

import numpy as np  # noqa: E402

from gaitfusion import synth, synth_c1  # noqa: E402
from gaitfusion import wire as gw  # noqa: E402
from tools import fake_head as fh  # noqa: E402

NAME = "walk_two_feet"
HEAD_BOOT_ID = 0xB005A0E5
HEAD_US0 = 81_234_567_890
GEOMETRY_ID = "e2e-room-1"
ANCHOR_MACS = ["e0:72:a1:00:00:01", "e0:72:a1:00:00:02", "e0:72:a1:00:00:03"]
SYSTEM_EVERY = 60  # system 블록을 넣는 묶음 간격(첫 묶음 포함)
# (길이 s, 처음 정지 s): 정지 15 + 걷기 35 | 쉬기 180 | 정지 10 + 걷기 35 | 쉬기 20
PIECES = [(50.0, 15.0), (180.0, 179.9), (45.0, 10.0), (20.0, 19.9)]
TURN_RATE = 0.25  # 방 안을 돌도록(거리가 30 m 한도를 넘지 않게)


def piece(duration_s: float, still_s: float, seed: int, side: str):
    p = synth.GaitParams(duration_s=duration_s, still_s=still_s, seed=seed, turn_rate_rad_s=TURN_RATE)
    if side == "right":  # synth_c1.two_feet와 같게: 반 보폭 늦게, 옆으로 0.25 m
        p = replace(p, still_s=min(p.still_s + p.stride_period_s / 2, duration_s - 0.05),
                    start_xy=(p.start_xy[0], p.start_xy[1] + 0.25), seed=seed + 100)
    return synth.simulate_gait(p)


def concat(a, b):
    """b를 a 뒤에 이어 붙임(gaitfusion tests/test_analysis.py의 concat과 같음)."""
    shift = a.t_imu[-1] + 0.01
    return replace(a, t_imu=np.concatenate([a.t_imu, b.t_imu + shift]), acc=np.vstack([a.acc, b.acc]),
                   gyro=np.vstack([a.gyro, b.gyro]), p=np.vstack([a.p, b.p]), v=np.vstack([a.v, b.v]),
                   q=np.vstack([a.q, b.q]), stance=np.concatenate([a.stance, b.stance]),
                   t_uwb=np.concatenate([a.t_uwb, b.t_uwb + shift]), ranges=np.vstack([a.ranges, b.ranges]))


def foot(side: str):
    out = None
    for i, (duration, still) in enumerate(PIECES):
        p = piece(duration, still, 8 + i, side)
        out = p if out is None else concat(out, p)
    return out


def true_walks() -> list[list[float]]:
    """합성기가 걷게 한 시간(왼발 기준, 첫 묶음 = 0초). 정지보다 길이가 길게 남는 조각이 걷기 조각."""
    walks, t = [], 0.0
    for duration, still in PIECES:
        if duration - still > 1.0:
            walks.append([round(t + still, 3), round(t + duration, 3)])
        t += duration
    return walks


def main() -> None:
    feet = {"left": foot("left"), "right": foot("right")}
    raw = synth_c1.c1_chunks(feet, ranging=("left", "right"), head_boot_id=HEAD_BOOT_ID, head_us0=HEAD_US0)
    anchors = [[float(v) for v in a] + [0.0] for a in synth.DEFAULT_ANCHORS]  # [x, y, z, range_bias_m]
    system = fh.encode_system(head_mac=fh.TEST_DEVICE_MAC, tag_count=2, geometry_id=GEOMETRY_ID, anchors=anchors,
                              set_members=[*ANCHOR_MACS, synth_c1.TAG_MACS["left"], synth_c1.TAG_MACS["right"]],
                              surveyed=True)
    chunks = []
    for i, data in enumerate(raw):
        c = gw.decode_chunk(data)
        h = c.header
        status = (gw.StatusBlock(1, 1, 0, 0, system),) if i % SYSTEM_EVERY == 0 else ()
        # UTC 없음(flags bit0 = 0): 언제 다시 보내도 받기의 시각 검사에 걸리지 않음. 구간 시각은 도착 기준(SG-7)
        chunks.append(gw.build_chunk(head_boot_id=h.head_boot_id, chunk_seq=h.chunk_seq, head_us_start=h.head_us_start,
                                     ranges=c.ranges, imu_frames=c.imu_frames, status=status,
                                     geometry_hash=fh.geometry_hash(GEOMETRY_ID)))
    seqs = [gw.decode_chunk(c).header.chunk_seq for c in chunks]
    assert seqs == list(range(len(chunks))), "합성 기록에 빠진 묶음이 있으면 안 됨"
    batch = b"".join(len(c).to_bytes(4, "little") + c for c in chunks)
    (HERE / f"{NAME}.c1.gz").write_bytes(gzip.compress(batch, compresslevel=9, mtime=0))
    commit = subprocess.run(["git", "-C", str(GAITFUSION), "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(GAITFUSION), "status", "--porcelain", "gaitfusion"],
                                capture_output=True, text=True, check=True).stdout.strip())
    meta = {
        "made_by": "tests/e2e/data/make_walk_two_feet.py",
        "gaitfusion_commit": commit + ("-dirty" if dirty else ""),
        "format": "gzip of C1 record batch (u32 len little-endian + chunk, repeated), chunk_seq ascending",
        "sha256": hashlib.sha256(batch).hexdigest(),
        "n_chunks": len(chunks),
        "max_chunk_bytes": max(map(len, chunks)),
        "device_mac": fh.TEST_DEVICE_MAC,
        "head_boot_id": HEAD_BOOT_ID,
        "geometry_id": GEOMETRY_ID,
        "anchors": anchors,
        "sides": "both",
        "utc": False,
        "walks_s": true_walks(),
    }
    (HERE / f"{NAME}.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps({k: meta[k] for k in ("n_chunks", "max_chunk_bytes", "walks_s", "gaitfusion_commit")}),
          f"gz={len((HERE / f'{NAME}.c1.gz').read_bytes())}B raw={len(batch)}B")


if __name__ == "__main__":
    main()
