"""한 줄 JSON 로그 (PL-5). 모든 프로세스가 표준 출력에 씁니다."""

import json
import sys
import threading
from datetime import datetime

from uwbserver.pipeline import clock

_lock = threading.Lock()
_process = "uwb"


def setup(process: str) -> None:
    global _process
    _process = process


def _default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def emit(level: str, event: str, **fields) -> None:
    record = {"at": clock.now().isoformat(), "level": level, "process": _process, "event": event, **fields}
    line = json.dumps(record, ensure_ascii=False, default=_default)
    with _lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def info(event: str, **fields) -> None:
    emit("info", event, **fields)


def warning(event: str, **fields) -> None:
    emit("warning", event, **fields)


def error(event: str, **fields) -> None:
    emit("error", event, **fields)
