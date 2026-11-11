"""설정 (SPEC 4.3). 환경 변수에서 한 번 읽어 얼린 값으로 씁니다."""

import json
import os
from dataclasses import dataclass, field
from datetime import time
from functools import cache
from pathlib import Path
from typing import Literal

Role = Literal["owner", "intake", "core", "api"]

_ROLE_USER = {"intake": "intake_rw", "core": "core_rw", "api": "api_rw"}


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "uwb"
    db_owner: str = "uwb"
    db_password: str | None = None
    db_intake_password: str | None = None
    db_core_password: str | None = None
    db_api_password: str | None = None
    # 프로세스 안의 파일 뿌리. DB에는 '/data/...' 꼴의 논리 경로를 적고 여기로 풂(pipeline/files.py).
    data_root: Path = Path("/data")
    gaitfusion_url: str = "http://gaitfusion:8100"
    gaitfusion_timeout_s: float = 600.0
    analysis_workers: int = 1
    analysis_params: dict = field(default_factory=dict)
    segment_silence_s: int = 60
    segment_gap_s: int = 60
    segment_max_s: int = 3600
    archive_pad_s: int = 15
    raw_retention_days: int = 30
    purge_enabled: bool = False
    purge_at_utc: time = time(18, 0)
    app_token_ttl_days: int = 30
    core_version: str = "dev"
    alert_webhook_url: str | None = None  # OP-2. 없으면 로그만
    # RP-8 글 쓰는 모델(report/writer.py). API 키는 SDK가 환경 변수 ANTHROPIC_API_KEY에서 읽음
    report_model: str = "claude-opus-5-5"
    report_effort: str = "high"  # output_config.effort: low · medium · high · xhigh · max
    report_max_searches: int = 12  # RP-6: 질문 하나에서 search_papers를 부를 수 있는 횟수

    def db_user(self, role: Role) -> str:
        return self.db_owner if role == "owner" else _ROLE_USER[role]

    def db_role_password(self, role: Role) -> str | None:
        return {
            "owner": self.db_password,
            "intake": self.db_intake_password,
            "core": self.db_core_password,
            "api": self.db_api_password,
        }[role]

    def dsn(self, role: Role, *, dbname: str | None = None) -> str:
        parts = {
            "host": self.db_host,
            "port": str(self.db_port),
            "dbname": dbname or self.db_name,
            "user": self.db_user(role),
            "password": self.db_role_password(role) or "",
            "application_name": f"uwb-{role}",
        }
        return " ".join(f"{k}='{v}'" for k, v in parts.items())

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        d = cls()

        def get(name: str, default):
            return e.get(name, default)

        hh, mm = get("PURGE_AT_UTC", "18:00").split(":")
        return cls(
            db_host=get("DB_HOST", d.db_host),
            db_port=int(get("DB_PORT", d.db_port)),
            db_name=get("DB_NAME", d.db_name),
            db_owner=get("DB_OWNER", d.db_owner),
            db_password=get("DB_PASSWORD", None),
            db_intake_password=get("DB_INTAKE_PASSWORD", None),
            db_core_password=get("DB_CORE_PASSWORD", None),
            db_api_password=get("DB_API_PASSWORD", None),
            data_root=Path(get("DATA_ROOT", str(d.data_root))),
            gaitfusion_url=get("GAITFUSION_URL", d.gaitfusion_url).rstrip("/"),
            gaitfusion_timeout_s=float(get("GAITFUSION_TIMEOUT_S", d.gaitfusion_timeout_s)),
            analysis_workers=int(get("ANALYSIS_WORKERS", d.analysis_workers)),
            analysis_params=json.loads(get("ANALYSIS_PARAMS", "{}") or "{}"),
            segment_silence_s=int(get("SEGMENT_SILENCE_S", d.segment_silence_s)),
            segment_gap_s=int(get("SEGMENT_GAP_S", d.segment_gap_s)),
            segment_max_s=int(get("SEGMENT_MAX_S", d.segment_max_s)),
            archive_pad_s=int(get("ARCHIVE_PAD_S", d.archive_pad_s)),
            raw_retention_days=int(get("RAW_RETENTION_DAYS", d.raw_retention_days)),
            purge_enabled=_bool(get("PURGE_ENABLED", "false")),
            purge_at_utc=time(int(hh), int(mm)),
            app_token_ttl_days=int(get("APP_TOKEN_TTL_DAYS", d.app_token_ttl_days)),
            core_version=get("CORE_VERSION", d.core_version),
            alert_webhook_url=get("ALERT_WEBHOOK_URL", None) or None,
            report_model=get("REPORT_MODEL", d.report_model),
            report_effort=get("REPORT_EFFORT", d.report_effort),
            report_max_searches=int(get("REPORT_MAX_SEARCHES", d.report_max_searches)),
        )


_override: Settings | None = None


@cache
def _from_env() -> Settings:
    return Settings.from_env()


def get() -> Settings:
    """현재 설정. 시험은 use()로 바꿔 끼웁니다."""
    return _override if _override is not None else _from_env()


def use(settings: Settings | None) -> None:
    global _override
    _override = settings
