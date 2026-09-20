"""Application settings, read from the environment or the repository-root `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- runtime mode -----------------------------------------------------
    app_mode: Literal["demo", "production"] = "demo"
    """`demo` enables the seeded accounts, the demo clock and the demo control panel."""

    # --- database ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg://torob:torob@127.0.0.1:5437/torob_repair"
    test_database_url: str = "postgresql+asyncpg://torob:torob@127.0.0.1:5438/torob_repair_test"
    sql_echo: bool = False

    # --- http -------------------------------------------------------------
    web_origin: str = "http://127.0.0.1:3000"
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # --- session ----------------------------------------------------------
    session_ttl_hours: int = 8
    session_cookie_name: str = "torob_repair_session"
    session_cookie_secure: bool = False

    # --- private files ----------------------------------------------------
    attachment_dir: Path = REPO_ROOT / "backend" / "var" / "attachments"
    attachment_max_bytes: int = 2 * 1024 * 1024
    attachment_max_per_expense: int = 3

    # --- AI ---------------------------------------------------------------
    ai_mode: Literal["mock", "live"] = "mock"
    ai_model: str = "deepseek-flash"
    ai_api_key: str | None = None
    ai_base_url: str | None = None
    ai_request_timeout_seconds: float = 45.0
    ai_retry_delay_seconds: float = 2.0
    ai_input_price_per_million: int | None = None
    """Recorded tariff in Toman per million input tokens; required in live mode."""
    ai_output_price_per_million: int | None = None
    ai_tariff_recorded_at: str | None = None
    ai_daily_cost_cap_toman: int = 0
    """Whole-installation daily live spend cap. Defaults to the ~1 USD equivalent below."""

    # --- worker -----------------------------------------------------------
    worker_poll_seconds: float = 10.0
    worker_lease_seconds: int = 300

    # --- demo -------------------------------------------------------------
    demo_registration_fee_toman: int = 2_000

    @field_validator("attachment_dir", mode="after")
    @classmethod
    def _absolute_attachment_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    @property
    def is_demo(self) -> bool:
        return self.app_mode == "demo"

    def sqlalchemy_url(self, *, testing: bool = False) -> str:
        return self.test_database_url if testing else self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
