"""segment의 SQL. 함수 하나 = 쿼리 하나. 쓰기는 segment.*만."""

from dataclasses import fields
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from uwbserver.segment.rules import ChunkIn, InstallationIn, Seg, SegTag, Side

_SEG_COLS = [f.name for f in fields(Seg)]
_SEG_MUTABLE = [c for c in _SEG_COLS if c not in ("id", "device_id", "head_boot_id", "first_seq", "first_head_us")]


def load_chunks(conn: psycopg.Connection, after_id: int, limit: int) -> list[ChunkIn]:
    """SG-1: id > 읽기 위치인 묶음을 id 순으로. system 블록이 있으면 함께."""
    rows = conn.execute(
        """
        SELECT c.id, c.device_id, c.head_boot_id, c.chunk_seq, c.received_at, c.head_us_start, c.utc_us_start,
               c.geometry_hash, c.flags, c.n_range, c.n_imu_frames,
               c.dropped_range + c.dropped_imu_frames AS dropped, c.tag_mask,
               c.tag0_mac::text AS tag0_mac, c.tag0_boot_id, c.tag1_mac::text AS tag1_mac, c.tag1_boot_id,
               s.block AS system_block
        FROM intake.chunk c
        LEFT JOIN intake.system s USING (device_id, head_boot_id, chunk_seq)
        WHERE c.id > %s
        ORDER BY c.id
        LIMIT %s
        """,
        (after_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        tags: dict[Side, tuple[str, int] | None] = {}
        if r["tag_mask"] & 1:
            tags["left"] = (r["tag0_mac"], r["tag0_boot_id"]) if r["tag0_mac"] else None
        if r["tag_mask"] & 2:
            tags["right"] = (r["tag1_mac"], r["tag1_boot_id"]) if r["tag1_mac"] else None
        out.append(
            ChunkIn(
                id=r["id"], device_id=r["device_id"], head_boot_id=r["head_boot_id"], seq=r["chunk_seq"],
                received_at=r["received_at"], head_us_start=r["head_us_start"], utc_us_start=r["utc_us_start"],
                geometry_hash=r["geometry_hash"], flags=r["flags"], n_range=r["n_range"],
                n_imu_frames=r["n_imu_frames"], dropped=r["dropped"], tag_mask=r["tag_mask"], tags=tags,
                system_block=bytes(r["system_block"]) if r["system_block"] is not None else None,
            )  # fmt: skip
        )
    return out


def _seg(row: dict | None) -> Seg | None:
    return Seg(**{c: row[c] for c in _SEG_COLS}) if row else None


def find_p(conn: psycopg.Connection, device_id: str, head_boot_id: int, seq: int) -> Seg | None:
    """SG-3: first_seq ≤ seq인 구간 중 first_seq가 가장 큰 것(segment_seq 색인)."""
    row = conn.execute(
        f"""
        SELECT {", ".join(_SEG_COLS)} FROM segment.segment
        WHERE device_id = %s AND head_boot_id = %s AND first_seq <= %s
        ORDER BY first_seq DESC LIMIT 1
        """,
        (device_id, head_boot_id, seq),
    ).fetchone()
    return _seg(row)


def latest_of_boot(conn: psycopg.Connection, device_id: str, head_boot_id: int) -> Seg | None:
    row = conn.execute(
        f"""
        SELECT {", ".join(_SEG_COLS)} FROM segment.segment
        WHERE device_id = %s AND head_boot_id = %s
        ORDER BY first_seq DESC LIMIT 1
        """,
        (device_id, head_boot_id),
    ).fetchone()
    return _seg(row)


def get(conn: psycopg.Connection, segment_id: int) -> Seg | None:
    row = conn.execute(f"SELECT {', '.join(_SEG_COLS)} FROM segment.segment WHERE id = %s", (segment_id,)).fetchone()
    return _seg(row)


def insert_segment(conn: psycopg.Connection, seg: Seg) -> int:
    cols = [c for c in _SEG_COLS if c != "id"]
    return conn.execute(
        f"INSERT INTO segment.segment ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
        [getattr(seg, c) for c in cols],
    ).fetchone()["id"]


def update_segment(conn: psycopg.Connection, seg: Seg) -> None:
    sets = ", ".join(f"{c} = %s" for c in _SEG_MUTABLE)
    conn.execute(
        f"UPDATE segment.segment SET {sets} WHERE id = %s",
        [getattr(seg, c) for c in _SEG_MUTABLE] + [seg.id],
    )


def close_other_boots(conn: psycopg.Connection, device_id: str, head_boot_id: int) -> list[int]:
    """SG-5 boot_changed."""
    rows = conn.execute(
        """
        UPDATE segment.segment SET state = 'closed', closed_reason = 'boot_changed'
        WHERE device_id = %s AND head_boot_id <> %s AND state = 'open'
        RETURNING id
        """,
        (device_id, head_boot_id),
    ).fetchall()
    return [r["id"] for r in rows]


def close_silent(conn: psycopg.Connection, cutoff: datetime) -> list[int]:
    """SG-5 silence_60s: last_chunk_at < cutoff인 열린 구간."""
    rows = conn.execute(
        """
        UPDATE segment.segment SET state = 'closed', closed_reason = 'silence_60s'
        WHERE state = 'open' AND last_chunk_at < %s
        RETURNING id
        """,
        (cutoff,),
    ).fetchall()
    return [r["id"] for r in rows]


def tags(conn: psycopg.Connection, segment_id: int) -> dict[Side, SegTag]:
    rows = conn.execute(
        """
        SELECT side, tag_mac::text AS tag_mac, tag_boot_id, first_seen_at
        FROM segment.segment_tag WHERE segment_id = %s
        """,
        (segment_id,),
    ).fetchall()
    return {r["side"]: SegTag(r["side"], r["tag_mac"], r["tag_boot_id"], r["first_seen_at"]) for r in rows}


def insert_tag(conn: psycopg.Connection, segment_id: int, tag: SegTag) -> None:
    conn.execute(
        """
        INSERT INTO segment.segment_tag (segment_id, side, tag_mac, tag_boot_id, first_seen_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (segment_id, tag.side, tag.tag_mac, tag.tag_boot_id, tag.first_seen_at),
    )


def fill_tag(conn: psycopg.Connection, segment_id: int, side: Side, tag_mac: str, tag_boot_id: int) -> None:
    """SG-6: 거리 프레임만 있던 발의 MAC을 처음 IMU 프레임으로 채움. 이미 있는 MAC은 바꾸지 않음."""
    conn.execute(
        """
        UPDATE segment.segment_tag SET tag_mac = %s, tag_boot_id = %s
        WHERE segment_id = %s AND side = %s AND tag_mac IS NULL
        """,
        (tag_mac, tag_boot_id, segment_id, side),
    )


def upsert_installation(conn: psycopg.Connection, inst: InstallationIn) -> int:
    """SG-8: (device_id, geometry_hash)의 배치를 넣거나 last_seen_at을 늘림."""
    return conn.execute(
        """
        INSERT INTO segment.installation
            (device_id, geometry_hash, geometry_id, source, surveyed, anchors, first_seen_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (device_id, geometry_hash)
        DO UPDATE SET last_seen_at = GREATEST(segment.installation.last_seen_at, EXCLUDED.last_seen_at)
        RETURNING id
        """,
        (
            inst.device_id, inst.geometry_hash, inst.geometry_id, inst.source, inst.surveyed,
            Jsonb(inst.anchors), inst.seen_at, inst.seen_at,
        ),  # fmt: skip
    ).fetchone()["id"]


def fill_installation(conn: psycopg.Connection, device_id: str, geometry_hash: int, installation_id: int) -> int:
    """SG-8: 같은 장치·hash로 installation_id가 비어 있는 구간을 채움(segment_no_installation 색인)."""
    return conn.execute(
        """
        UPDATE segment.segment SET installation_id = %s
        WHERE device_id = %s AND geometry_hash = %s AND installation_id IS NULL
        """,
        (installation_id, device_id, geometry_hash),
    ).rowcount


def installation_id_for(conn: psycopg.Connection, device_id: str, geometry_hash: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM segment.installation WHERE device_id = %s AND geometry_hash = %s", (device_id, geometry_hash)
    ).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------- 앱 조회 (C3, 8절)


def list_segments(
    conn: psycopg.Connection, device_id: str | None, start: datetime, end: datetime, limit: int, before_id: int | None
) -> list[dict]:
    return conn.execute(
        """
        SELECT id, device_id, head_boot_id, state, closed_reason, sides, start_at, end_at, n_chunks,
               n_missing_chunks, n_dropped_frames, time_source, installation_id
        FROM segment.segment
        WHERE (%(dev)s::text IS NULL OR device_id = %(dev)s) AND start_at >= %(start)s AND start_at < %(end)s
          AND (%(before)s::bigint IS NULL OR id < %(before)s)
        ORDER BY id DESC LIMIT %(limit)s
        """,
        {"dev": device_id, "start": start, "end": end, "before": before_id, "limit": limit},
    ).fetchall()


def segment_detail(conn: psycopg.Connection, segment_id: int) -> dict | None:
    return conn.execute(
        """
        SELECT s.*, ss.subject_id, ss.ambiguous AS subject_ambiguous,
               i.geometry_id, i.source AS geometry_source, i.surveyed, i.anchors
        FROM segment.segment s
        JOIN analysis.segment_subject ss ON ss.segment_id = s.id
        LEFT JOIN segment.installation i ON i.id = s.installation_id
        WHERE s.id = %s
        """,
        (segment_id,),
    ).fetchone()


def tag_rows(conn: psycopg.Connection, segment_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT side, tag_mac::text AS tag_mac, tag_boot_id, first_seen_at
        FROM segment.segment_tag WHERE segment_id = %s ORDER BY side
        """,
        (segment_id,),
    ).fetchall()


def run_rows(conn: psycopg.Connection, segment_id: int) -> list[dict]:
    return conn.execute(
        """
        SELECT r.id, r.status, r.skip_reason, r.input, r.gaitfusion_version, r.started_at, r.finished_at,
               (SELECT count(*) FROM analysis.bout b WHERE b.run_id = r.id) AS n_bouts
        FROM analysis.run r WHERE r.segment_id = %s ORDER BY r.id DESC
        """,
        (segment_id,),
    ).fetchall()


def has_input(conn: psycopg.Connection, segment_id: int) -> bool:
    """재분석 입력이 있는가(AN-4): 묶음 행이 모두 raw에 있거나, archive 범위가 있음."""
    return conn.execute(
        """
        SELECT (SELECT count(*) FROM intake.chunk c, pipeline.cursor k
                 WHERE c.device_id = s.device_id AND c.head_boot_id = s.head_boot_id
                   AND c.chunk_seq BETWEEN s.first_seq AND s.last_seq
                   AND (c.n_range > 0 OR c.n_imu_frames > 0)
                   AND k.name = 'chunk' AND c.id <= k.last_id) = s.n_chunks
            OR EXISTS (SELECT 1 FROM archive.range a WHERE a.segment_id = s.id) AS ok
        FROM segment.segment s WHERE s.id = %s
        """,
        (segment_id,),
    ).fetchone()["ok"]
