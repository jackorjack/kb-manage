"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _path(value: str, default: Path) -> Path:
    candidate = Path(value) if value else default
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_host: str
    app_port: int
    app_data_dir: Path
    secret_key: str
    admin_username: str
    admin_initial_password: str
    openclaw_bin: str
    openclaw_config_path: Optional[str]
    openclaw_timeout_seconds: int
    max_upload_bytes: int
    max_conversion_output_bytes: int
    session_ttl_seconds: int
    app_start_worker: bool
    frontend_dist: Path
    frontend_origin: str

    @property
    def database_path(self) -> Path:
        return self.app_data_dir / "app.db"

    @property
    def staging_dir(self) -> Path:
        return self.app_data_dir / "staging"

    @property
    def lock_path(self) -> Path:
        return self.app_data_dir / "index.lock"

    @classmethod
    def from_env(cls, values: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if values is None else values
        return cls(
            app_env=env.get("APP_ENV", "development"),
            app_host=env.get("APP_HOST", "127.0.0.1"),
            app_port=_int(env.get("APP_PORT"), 8000),
            app_data_dir=_path(env.get("APP_DATA_DIR"), PROJECT_ROOT / "runtime"),
            secret_key=env.get("APP_SECRET_KEY", "development-only-secret-change-me"),
            admin_username=env.get("ADMIN_USERNAME", "admin"),
            admin_initial_password=env.get("ADMIN_INITIAL_PASSWORD", "change-me-before-use"),
            openclaw_bin=env.get("OPENCLAW_BIN", "openclaw"),
            openclaw_config_path=env.get("OPENCLAW_CONFIG_PATH") or None,
            openclaw_timeout_seconds=_int(env.get("OPENCLAW_TIMEOUT_SECONDS"), 3600),
            max_upload_bytes=_int(env.get("MAX_UPLOAD_BYTES"), 50 * 1024 * 1024),
            max_conversion_output_bytes=_int(
                env.get("MAX_CONVERSION_OUTPUT_BYTES"), 100 * 1024 * 1024
            ),
            session_ttl_seconds=_int(env.get("SESSION_TTL_SECONDS"), 24 * 3600),
            app_start_worker=_bool(env.get("APP_START_WORKER"), True),
            frontend_dist=_path(env.get("FRONTEND_DIST"), PROJECT_ROOT / "frontend" / "dist"),
            frontend_origin=env.get("FRONTEND_ORIGIN", "http://localhost:5173"),
        )
