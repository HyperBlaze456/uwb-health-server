"""registry의 SQL. 함수 하나 = 쿼리 하나. 쓰기는 registry.*만."""

from dataclasses import dataclass
from datetime import datetime

import psycopg


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    mac: str  # 'aa:bb:cc:dd:ee:ff'

    @property
    def mac_bytes(self) -> bytes:
        return bytes.fromhex(self.mac.replace(":", ""))


# ---------------------------------------------------------------- 장치 (RG-1, RG-2)


def device_by_token_hash(conn: psycopg.Connection, token_hash: bytes) -> Device | None:
    """IN-2: 토큰의 sha256으로 장치를 찾습니다(고유 색인). 폐기된 장치는 token_hash가 NULL이라 안 나옴."""
    row = conn.execute("SELECT id, mac::text AS mac FROM registry.device WHERE token_hash = %s", (token_hash,)).fetchone()
    return Device(row["id"], row["mac"]) if row else None


def device_by_id(conn: psycopg.Connection, device_id: str) -> Device | None:
    row = conn.execute("SELECT id, mac::text AS mac FROM registry.device WHERE id = %s", (device_id,)).fetchone()
    return Device(row["id"], row["mac"]) if row else None


def insert_device(
    conn: psycopg.Connection, *, device_id: str, mac: str, token_hash: bytes, now: datetime, note: str | None
) -> bool:
    """새 장치. 같은 id나 MAC이 이미 있으면 False."""
    cur = conn.execute(
        """
        INSERT INTO registry.device (id, mac, token_hash, token_issued_at, registered_at, note)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (device_id, mac, token_hash, now, now, note),
    )
    return cur.rowcount == 1


def set_device_token(conn: psycopg.Connection, device_id: str, token_hash: bytes | None, now: datetime) -> bool:
    """RG-2: 재발급(새 해시)이나 폐기(NULL). 장치가 없으면 False."""
    cur = conn.execute(
        "UPDATE registry.device SET token_hash = %s, token_issued_at = %s WHERE id = %s",
        (token_hash, now if token_hash else None, device_id),
    )
    return cur.rowcount == 1


def list_devices(conn: psycopg.Connection, device_ids: list[str] | None = None) -> list[dict]:
    """device_ids가 None이면 전부."""
    return conn.execute(
        """
        SELECT id, mac::text AS mac, registered_at, token_issued_at, (token_hash IS NOT NULL) AS active, note
        FROM registry.device
        WHERE %(ids)s::text[] IS NULL OR id = ANY(%(ids)s)
        ORDER BY id
        """,
        {"ids": device_ids},
    ).fetchall()


def latest_blocks(conn: psycopg.Connection, device_id: str, subject_id: int | None = None) -> dict[str, dict | None]:
    """최신 system·health 블록(장치 상세, 8절). subject_id가 있으면(member) 그 착용 기간 안의 것만."""
    out: dict[str, dict | None] = {}
    for kind in ("system", "health"):
        out[kind] = conn.execute(
            f"""
            SELECT b.head_boot_id, b.chunk_seq, b.at, b.block FROM intake.{kind} b
            WHERE b.device_id = %(dev)s
              AND (%(subj)s::bigint IS NULL OR EXISTS (
                    SELECT 1 FROM registry.wear w
                    WHERE w.subject_id = %(subj)s AND w.device_id = b.device_id
                      AND w.started_at <= b.at AND (w.ended_at IS NULL OR b.at < w.ended_at)))
            ORDER BY b.at DESC LIMIT 1
            """,
            {"dev": device_id, "subj": subject_id},
        ).fetchone()
    return out


def device_ids_worn_by(conn: psycopg.Connection, subject_id: int) -> list[str]:
    rows = conn.execute("SELECT DISTINCT device_id FROM registry.wear WHERE subject_id = %s", (subject_id,)).fetchall()
    return [r["device_id"] for r in rows]


# ---------------------------------------------------------------- 계정과 로그인 토큰 (RG-5, RG-6)


def insert_user(
    conn: psycopg.Connection, *, email: str, password_hash: str, role: str, subject_id: int | None, now: datetime
) -> int | None:
    """같은 이메일이 있으면 None."""
    row = conn.execute(
        """
        INSERT INTO registry.app_user (email, password_hash, role, subject_id, created_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (email) DO NOTHING
        RETURNING id
        """,
        (email, password_hash, role, subject_id, now),
    ).fetchone()
    return row["id"] if row else None


def user_by_email(conn: psycopg.Connection, email: str) -> dict | None:
    return conn.execute(
        "SELECT id, email, password_hash, role, subject_id FROM registry.app_user WHERE email = %s", (email,)
    ).fetchone()


def user_by_token_hash(conn: psycopg.Connection, token_hash: bytes, now: datetime) -> dict | None:
    return conn.execute(
        """
        SELECT u.id, u.email, u.role, u.subject_id
        FROM registry.app_token t JOIN registry.app_user u ON u.id = t.user_id
        WHERE t.token_hash = %s AND t.revoked_at IS NULL AND t.expires_at > %s
        """,
        (token_hash, now),
    ).fetchone()


def insert_app_token(conn: psycopg.Connection, token_hash: bytes, user_id: int, expires_at: datetime, now: datetime) -> None:
    conn.execute(
        "INSERT INTO registry.app_token (token_hash, user_id, expires_at, created_at) VALUES (%s, %s, %s, %s)",
        (token_hash, user_id, expires_at, now),
    )


def revoke_app_token(conn: psycopg.Connection, token_hash: bytes, now: datetime) -> None:
    conn.execute(
        "UPDATE registry.app_token SET revoked_at = %s WHERE token_hash = %s AND revoked_at IS NULL", (now, token_hash)
    )


def delete_expired_app_tokens(conn: psycopg.Connection, now: datetime) -> int:
    """RG-6: 만료된 토큰은 로그인할 때 지움."""
    return conn.execute("DELETE FROM registry.app_token WHERE expires_at <= %s", (now,)).rowcount


# ---------------------------------------------------------------- 착용자 (RG-3)

_SUBJECT_COLS = "id, code, height_m, leg_length_m, paretic_side"


def insert_subject(conn: psycopg.Connection, *, code: str, height_m, leg_length_m, paretic_side) -> int | None:
    row = conn.execute(
        """
        INSERT INTO registry.subject (code, height_m, leg_length_m, paretic_side) VALUES (%s, %s, %s, %s)
        ON CONFLICT (code) DO NOTHING RETURNING id
        """,
        (code, height_m, leg_length_m, paretic_side),
    ).fetchone()
    return row["id"] if row else None


def update_subject(conn: psycopg.Connection, subject_id: int, changes: dict) -> bool:
    """키·다리 길이·마비측만(RG-3)."""
    allowed = {k: v for k, v in changes.items() if k in ("height_m", "leg_length_m", "paretic_side")}
    if not allowed:
        return subject(conn, subject_id) is not None
    sets = ", ".join(f"{k} = %s" for k in allowed)
    return conn.execute(
        f"UPDATE registry.subject SET {sets} WHERE id = %s", [*allowed.values(), subject_id]
    ).rowcount == 1


def subject(conn: psycopg.Connection, subject_id: int) -> dict | None:
    return conn.execute(f"SELECT {_SUBJECT_COLS} FROM registry.subject WHERE id = %s", (subject_id,)).fetchone()


# ---------------------------------------------------------------- 착용 기록 (RG-4)

_WEAR_COLS = "id, subject_id, device_id, tag_mac::text AS tag_mac, started_at, ended_at"


def insert_wear(
    conn: psycopg.Connection, *, subject_id: int, device_id: str, tag_mac: str | None, started_at: datetime,
    ended_at: datetime | None,
) -> int:  # fmt: skip
    return conn.execute(
        """
        INSERT INTO registry.wear (subject_id, device_id, tag_mac, started_at, ended_at)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
        """,
        (subject_id, device_id, tag_mac, started_at, ended_at),
    ).fetchone()["id"]


def wear(conn: psycopg.Connection, wear_id: int) -> dict | None:
    return conn.execute(f"SELECT {_WEAR_COLS} FROM registry.wear WHERE id = %s", (wear_id,)).fetchone()


def update_wear_times(conn: psycopg.Connection, wear_id: int, started_at: datetime, ended_at: datetime | None) -> None:
    """RG-4: 고칠 수 있는 것은 시작과 끝뿐."""
    conn.execute(
        "UPDATE registry.wear SET started_at = %s, ended_at = %s WHERE id = %s", (started_at, ended_at, wear_id)
    )


def wear_of_subject(conn: psycopg.Connection, subject_id: int, limit: int, before_id: int | None) -> list[dict]:
    return conn.execute(
        f"""
        SELECT {_WEAR_COLS} FROM registry.wear
        WHERE subject_id = %s AND (%s::bigint IS NULL OR id < %s)
        ORDER BY id DESC LIMIT %s
        """,
        (subject_id, before_id, before_id, limit),
    ).fetchall()
