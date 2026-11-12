from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WT_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./watchtower.db"
    api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_url: str | None = None
    allow_private_targets: bool = False
    retention_days: int = 30
    scheduler_tick_seconds: float = 1.0
    max_concurrent_checks: int = 50
    scheduler_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
