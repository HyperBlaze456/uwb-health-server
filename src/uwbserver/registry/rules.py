"""사람 입력의 규칙 (RG). 순수 계산: 장치 이름, 토큰, 비밀번호."""

import base64
import hashlib
import re
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_MAC = re.compile(r"^[0-9a-f]{2}([:-]?)[0-9a-f]{2}(\1[0-9a-f]{2}){4}$")
_hasher = PasswordHasher()  # argon2id (RG-5)

ROLES = ("member", "admin")
SIDES = ("left", "right")


def normalize_mac(mac: str) -> str:
    """'E0-72-A1-AC-A8-58', 'e072a1aca858' → 'e0:72:a1:ac:a8:58'. 형식이 틀리면 ValueError."""
    m = mac.strip().lower()
    if not _MAC.match(m):
        raise ValueError(f"MAC 형식이 아님: {mac!r}")
    hexes = m.replace(":", "").replace("-", "")
    if int(hexes[:2], 16) & 1:
        raise ValueError("멀티캐스트 MAC은 장치가 될 수 없음")
    return ":".join(hexes[i : i + 2] for i in range(0, 12, 2))


def device_id(mac: str) -> str:
    """RG-1: 'head-' + MAC 12자리 소문자."""
    return "head-" + normalize_mac(mac).replace(":", "")


def new_token() -> str:
    """RG-1·RG-6: 32 바이트 무작위값의 base64url(패딩 없음, 43자)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def token_hash(token: str) -> bytes:
    """DB에는 보낸 그대로의 ASCII 문자열의 sha256만 둡니다."""
    return hashlib.sha256(token.encode("ascii", errors="strict")).digest()


def bearer_token(authorization: str | None) -> str | None:
    """'Bearer <토큰>'에서 토큰. 없거나 형식이 틀리면 None."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value or not value.isascii():
        return None
    return value


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def token_expiry(now: datetime, ttl_days: int) -> datetime:
    return now + timedelta(days=ttl_days)
