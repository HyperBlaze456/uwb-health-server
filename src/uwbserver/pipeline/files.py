"""파일 형식과 쓰기 순서 (SPEC 5.2, IN-6).

DB에는 '/data/...' 꼴의 논리 경로를 적습니다. 실제 위치는 settings.data_root 아래입니다.
그래서 덤프를 다른 호스트에 복구해도 경로 열이 그대로 맞습니다.

- raw·archive 레코드: u32 len · i64 received_utc_us · len 바이트 묶음 (머리 12 B)
- rejected 레코드:    u32 len · i64 received_utc_us · u8 code · u8 check · u16 0 · len 바이트 (머리 16 B)
"""

import os
import re
import struct
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from uwbserver.pipeline import clock, settings

LOGICAL_ROOT = "/data"

RAW_HEAD = struct.Struct("<Iq")  # 12 B
REJECTED_HEAD = struct.Struct("<IqBBH")  # 16 B
C1_LEN = struct.Struct("<I")


# ---------------------------------------------------------------- 경로


def resolve(logical: str) -> Path:
    if not logical.startswith(LOGICAL_ROOT + "/"):
        raise ValueError(f"논리 경로가 아님: {logical}")
    return settings.get().data_root / logical[len(LOGICAL_ROOT) + 1 :]


def logical(path: Path) -> str:
    rel = path.relative_to(settings.get().data_root)
    return f"{LOGICAL_ROOT}/{rel.as_posix()}"


def _utc(us: int) -> datetime:
    return clock.from_us(us)


def raw_path(device_id: str, head_boot_id: int, received_us: int) -> str:
    t = _utc(received_us)
    return f"{LOGICAL_ROOT}/raw/{device_id}/{head_boot_id:08x}/{t:%Y-%m-%dT%H}.bin"


def rejected_path(device_id: str, received_us: int) -> str:
    return f"{LOGICAL_ROOT}/rejected/{device_id}/{_utc(received_us):%Y-%m-%d}.bin"


def archive_path(device_id: str, head_boot_id: int, first_seq: int, last_seq: int, start_us: int) -> str:
    t = _utc(start_us)
    return (
        f"{LOGICAL_ROOT}/archive/{device_id}/{t:%Y-%m}/"
        f"{t:%Y-%m-%dT%H-%M-%S}Z_{head_boot_id:08x}_{first_seq}-{last_seq}.bin"
    )


def result_path(run_id: int) -> str:
    return f"{LOGICAL_ROOT}/result/{run_id}.json.gz"


def trajectory_dir(run_id: int) -> str:
    return f"{LOGICAL_ROOT}/trajectory/{run_id}"


def trajectory_path(run_id: int, bout_idx: int, side: str) -> str:
    return f"{trajectory_dir(run_id)}/{bout_idx}-{side}.trj"


_RAW_RE = re.compile(r"^/data/raw/(?P<dev>[^/]+)/(?P<boot>[0-9a-f]{8})/(?P<hour>\d{4}-\d{2}-\d{2}T\d{2})\.bin$")
_ARCHIVE_RE = re.compile(
    r"^/data/archive/(?P<dev>[^/]+)/\d{4}-\d{2}/"
    r"(?P<start>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})Z_(?P<boot>[0-9a-f]{8})_(?P<first>\d+)-(?P<last>\d+)\.bin$"
)
_REJECTED_RE = re.compile(r"^/data/rejected/(?P<dev>[^/]+)/(?P<day>\d{4}-\d{2}-\d{2})\.bin$")


@dataclass(frozen=True, slots=True)
class RawName:
    device_id: str
    head_boot_id: int
    hour: datetime  # 수신 hour의 시작(UTC)


@dataclass(frozen=True, slots=True)
class ArchiveName:
    device_id: str
    head_boot_id: int
    first_seq: int
    last_seq: int
    start_at: datetime


def parse_raw_path(path: str) -> RawName:
    m = _RAW_RE.match(path)
    if not m:
        raise ValueError(f"raw 경로 형식이 아님: {path}")
    hour = datetime.strptime(m["hour"], "%Y-%m-%dT%H").replace(tzinfo=UTC)
    return RawName(m["dev"], int(m["boot"], 16), hour)


def parse_archive_path(path: str) -> ArchiveName:
    m = _ARCHIVE_RE.match(path)
    if not m:
        raise ValueError(f"archive 경로 형식이 아님: {path}")
    start = datetime.strptime(m["start"], "%Y-%m-%dT%H-%M-%S").replace(tzinfo=UTC)
    return ArchiveName(m["dev"], int(m["boot"], 16), int(m["first"]), int(m["last"]), start)


def parse_rejected_path(path: str) -> tuple[str, datetime]:
    m = _REJECTED_RE.match(path)
    if not m:
        raise ValueError(f"rejected 경로 형식이 아님: {path}")
    return m["dev"], datetime.strptime(m["day"], "%Y-%m-%d").replace(tzinfo=UTC)


def list_files(kind: str) -> list[str]:
    """kind 폴더('raw', 'archive', 'rejected') 아래 .bin 파일의 논리 경로, 이름 순."""
    base = settings.get().data_root / kind
    if not base.exists():
        return []
    return sorted(logical(p) for p in base.rglob("*.bin"))


# ---------------------------------------------------------------- 레코드


def raw_record_head(data_len: int, received_us: int) -> bytes:
    return RAW_HEAD.pack(data_len, received_us)


def rejected_record_head(data_len: int, received_us: int, code: int, check: int) -> bytes:
    return REJECTED_HEAD.pack(data_len, received_us, code, check, 0)


@dataclass(frozen=True, slots=True)
class RawRecord:
    offset: int  # 머리 뒤, 묶음 첫 바이트
    received_us: int
    data: bytes


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    offset: int
    received_us: int
    code: int
    check: int
    data: bytes


CHUNK_MAGIC = 0xC1  # wire.md 머리 첫 바이트
_CHUNK_MIN, _CHUNK_MAX = 52, 11_284  # wire.md 묶음 길이 한도


def _record_ok(buf: bytes, pos: int) -> int | None:
    """pos에 온전한 raw 레코드가 있으면 묶음 길이, 아니면 None. 묶음의 magic과 CRC로 확인합니다."""
    if pos + RAW_HEAD.size > len(buf):
        return None
    n, _ = RAW_HEAD.unpack_from(buf, pos)
    start = pos + RAW_HEAD.size
    if not (_CHUNK_MIN <= n <= _CHUNK_MAX) or start + n > len(buf) or buf[start] != CHUNK_MAGIC:
        return None
    if zlib.crc32(buf[start : start + n - 4]) != int.from_bytes(buf[start + n - 4 : start + n], "little"):
        return None
    return n


def scan_raw(path: str, garbage: list[tuple[int, int]] | None = None) -> tuple[list[RawRecord], int]:
    """raw·archive 파일을 처음부터 읽습니다. (레코드들, 마지막 온전한 레코드의 끝 위치)를 돌려줍니다.

    레코드마다 묶음의 magic과 CRC를 확인합니다. 쓰다 만 레코드(작업자가 쓰는 중에 죽음)가 파일 중간에 있어도
    그 뒤에서 다음 온전한 레코드를 찾아 계속 읽습니다. 그래서 중간의 깨진 바이트 때문에 뒤의 레코드를 잃지 않습니다.
    깨진 구간은 garbage에 (위치, 길이)로 담습니다. 그 바이트는 확인 응답을 받지 못했으므로 Head가 다시 보냈습니다.
    """
    buf = resolve(path).read_bytes()
    out: list[RawRecord] = []
    pos = 0
    end = 0
    while pos + RAW_HEAD.size <= len(buf):
        n = _record_ok(buf, pos)
        if n is None:
            # 다음 후보: 머리 뒤 첫 바이트가 magic인 곳
            nxt = pos + 1
            while True:
                hit = buf.find(bytes([CHUNK_MAGIC]), nxt + RAW_HEAD.size)
                if hit < 0:
                    nxt = len(buf)
                    break
                nxt = hit - RAW_HEAD.size
                if _record_ok(buf, nxt) is not None:
                    break
                nxt += 1
            if garbage is not None:
                garbage.append((pos, nxt - pos))
            pos = nxt
            continue
        _, us = RAW_HEAD.unpack_from(buf, pos)
        start = pos + RAW_HEAD.size
        out.append(RawRecord(start, us, buf[start : start + n]))
        pos = end = start + n
    if garbage is not None and pos < len(buf):  # 머리 하나도 안 되는 꼬리
        garbage.append((pos, len(buf) - pos))
    return out, end


def scan_rejected(path: str) -> tuple[list[RejectedRecord], int]:
    buf = resolve(path).read_bytes()
    out: list[RejectedRecord] = []
    pos = 0
    while pos + REJECTED_HEAD.size <= len(buf):
        n, us, code, check, _ = REJECTED_HEAD.unpack_from(buf, pos)
        start = pos + REJECTED_HEAD.size
        if start + n > len(buf):
            break
        out.append(RejectedRecord(start, us, code, check, buf[start : start + n]))
        pos = start + n
    return out, pos


def truncate_torn_tail(path: str, valid_end: int) -> int:
    """마지막 온전한 레코드(valid_end) 뒤의 꼬리만 잘라 냅니다. 잘라 낸 바이트 수를 돌려줍니다(IN-9 재색인 때).

    valid_end는 scan_raw가 준 값이어야 합니다. 그 뒤에는 온전한 레코드가 없으므로 확인 받은 묶음을 자르지 않습니다.
    """
    p = resolve(path)
    size = p.stat().st_size
    if size <= valid_end:
        return 0
    with open(p, "r+b") as f:
        f.truncate(valid_end)
        f.flush()
        os.fsync(f.fileno())
    return size - valid_end


def read_at(path: str, offset: int, length: int) -> bytes:
    with open(resolve(path), "rb") as f:
        f.seek(offset)
        data = f.read(length)
    if len(data) != length:
        raise OSError(f"{path}@{offset}: {length} B를 기대했지만 {len(data)} B")
    return data


def read_many(locations: Iterable[tuple[str, int, int]]) -> Iterator[bytes]:
    """(경로, 위치, 길이)들을 차례로 읽습니다. 같은 파일은 한 번만 엽니다."""
    handles: dict[str, object] = {}
    try:
        for path, offset, length in locations:
            f = handles.get(path)
            if f is None:
                f = handles[path] = open(resolve(path), "rb")
            f.seek(offset)
            data = f.read(length)
            if len(data) != length:
                raise OSError(f"{path}@{offset}: {length} B를 기대했지만 {len(data)} B")
            yield data
    finally:
        for f in handles.values():
            f.close()


def c1_record(data: bytes) -> bytes:
    """5.2: gaitfusion에 보낼 때 12 B 머리를 C1의 u32 len으로 바꿉니다."""
    return C1_LEN.pack(len(data)) + data


# ---------------------------------------------------------------- 이어 쓰기 (IN-6)


def _makedirs(directory: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    d = directory
    while not d.exists():
        missing.append(d)
        d = d.parent
    for d in reversed(missing):
        try:
            d.mkdir()
            created.append(d)
        except FileExistsError:
            pass


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Appender:
    """한 요청에서 건드린 파일들에 이어 쓰고, 파일마다 fdatasync를 한 번 합니다.

    한 장치의 파일에는 한 번에 한 작성자만 있습니다(장치 잠금, IN-4). 그래서 열 때의 크기가 곧 쓰는 위치입니다.
    실패하면 rollback()이 이번에 쓴 바이트를 잘라 냅니다.
    """

    def __init__(self) -> None:
        self._fds: dict[str, int] = {}
        self._start: dict[str, int] = {}
        self._pos: dict[str, int] = {}
        self._new_files: list[Path] = []
        self._new_dirs: list[Path] = []

    def append(self, path: str, head: bytes, data: bytes) -> int:
        """레코드 하나를 씁니다. 묶음 첫 바이트의 위치(머리 뒤)를 돌려줍니다."""
        fd = self._fds.get(path)
        if fd is None:
            p = resolve(path)
            _makedirs(p.parent, self._new_dirs)
            existed = p.exists()
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            self._fds[path] = fd
            if not existed:
                self._new_files.append(p)
            size = os.fstat(fd).st_size
            self._start[path] = self._pos[path] = size
        record = head + data
        view = memoryview(record)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        offset = self._pos[path] + len(head)
        self._pos[path] += len(record)
        return offset

    def sync(self) -> None:
        """파일마다 fdatasync 한 번, 새로 만든 파일·폴더의 부모 폴더 fsync(IN-6 3번)."""
        for fd in self._fds.values():
            os.fdatasync(fd) if hasattr(os, "fdatasync") else os.fsync(fd)
        parents = {p.parent for p in self._new_files} | {d.parent for d in self._new_dirs}
        for d in sorted(parents, key=lambda x: len(x.parts), reverse=True):
            _fsync_dir(d)

    def rollback(self) -> None:
        for path, fd in self._fds.items():
            try:
                os.ftruncate(fd, self._start[path])
            except OSError:
                pass

    def close(self) -> None:
        for fd in self._fds.values():
            os.close(fd)
        self._fds.clear()

    def __enter__(self) -> Appender:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.rollback()
        self.close()


# ---------------------------------------------------------------- 한 번에 쓰기 (archive, 결과 원문, 궤적)


def write_atomic(path: str, data: bytes) -> None:
    """임시 이름으로 쓰고 fsync 뒤 이름을 바꿉니다(5.2). 이 함수가 끝난 뒤에야 DB 행을 넣습니다."""
    p = resolve(path)
    created: list[Path] = []
    _makedirs(p.parent, created)
    tmp = p.with_name(f".{p.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    _fsync_dir(p.parent)
    for d in created:
        _fsync_dir(d.parent)


def exists(path: str) -> bool:
    return resolve(path).exists()


def size(path: str) -> int:
    return resolve(path).stat().st_size


def delete(path: str) -> bool:
    """파일 하나를 지웁니다. 지웠으면 True. 빈 폴더는 남겨 둡니다."""
    try:
        resolve(path).unlink()
        return True
    except FileNotFoundError:
        return False


def delete_tree(path: str) -> None:
    """폴더 하나와 그 안의 파일(궤적 run 폴더 정리용)."""
    p = resolve(path)
    if not p.exists():
        return
    for child in p.iterdir():
        child.unlink(missing_ok=True)
    p.rmdir()
