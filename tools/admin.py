"""관리 도구 (TL-6). API와 같은 store 함수를 쓰고, DB 역할은 api_rw입니다.

    uv run python tools/admin.py create-admin <email>            # 비밀번호는 묻거나 ADMIN_PASSWORD
    uv run python tools/admin.py register-device <mac> [--note]  # 장치 토큰을 한 번만 출력
    uv run python tools/admin.py rotate-token <device_id>
    uv run python tools/admin.py reanalyze <segment_id>
"""

import argparse
import getpass
import os
import sys

from uwbserver.pipeline import clock, db, jobs, log
from uwbserver.registry import rules, store
from uwbserver.segment import store as segment_store


def create_admin(email: str) -> int:
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("password: ")
    if len(password) < 8:
        print("password must be at least 8 characters", file=sys.stderr)
        return 2
    with db.connect("api") as conn, conn.transaction():
        uid = store.insert_user(
            conn, email=email, password_hash=rules.hash_password(password), role="admin", subject_id=None,
            now=clock.now(),
        )  # fmt: skip
    if uid is None:
        print(f"{email} already exists", file=sys.stderr)
        return 1
    print(f"admin {email} created (id {uid})")
    return 0


def register_device(mac: str, note: str | None) -> int:
    mac = rules.normalize_mac(mac)
    token = rules.new_token()
    did = rules.device_id(mac)
    with db.connect("api") as conn, conn.transaction():
        ok = store.insert_device(conn, device_id=did, mac=mac, token_hash=rules.token_hash(token), now=clock.now(), note=note)
    if not ok:
        print(f"{did} already registered", file=sys.stderr)
        return 1
    print(f"device_id {did}")
    print(f"token     {token}   (shown once; put it in the Head settings app)")
    return 0


def rotate_token(device_id: str) -> int:
    token = rules.new_token()
    with db.connect("api") as conn, conn.transaction():
        ok = store.set_device_token(conn, device_id, rules.token_hash(token), clock.now())
    if not ok:
        print(f"unknown device {device_id}", file=sys.stderr)
        return 1
    print(f"token {token}   (old token stops working now)")
    return 0


def reanalyze(segment_id: int) -> int:
    with db.connect("api") as conn, conn.transaction():
        if segment_store.get(conn, segment_id) is None:
            print(f"unknown segment {segment_id}", file=sys.stderr)
            return 1
        if not segment_store.has_input(conn, segment_id):
            print("no raw or archive input for this segment", file=sys.stderr)
            return 1
        queued = jobs.enqueue_analysis(conn, segment_id, now=clock.now())
    print("queued" if queued else "already queued or running")
    return 0


def main(argv: list[str] | None = None) -> int:
    log.setup("admin")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("email")
    r = sub.add_parser("register-device")
    r.add_argument("mac")
    r.add_argument("--note")
    t = sub.add_parser("rotate-token")
    t.add_argument("device_id")
    z = sub.add_parser("reanalyze")
    z.add_argument("segment_id", type=int)
    args = p.parse_args(argv)
    match args.cmd:
        case "create-admin":
            return create_admin(args.email)
        case "register-device":
            return register_device(args.mac, args.note)
        case "rotate-token":
            return rotate_token(args.device_id)
        case "reanalyze":
            return reanalyze(args.segment_id)
    return 2


if __name__ == "__main__":
    sys.exit(main())
