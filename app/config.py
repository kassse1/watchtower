from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WT_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./watchtower.db"
    auto_migrate: bool = True
    api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_url: str | None = None
    allow_private_targets: bool = False
    public_status_page: bool = True
    retention_days: int = 30
    tls_warn_days: int = 14

    scheduler_enabled: bool = True
    scheduler_tick_seconds: float = 1.0
    max_concurrent_checks: int = 50
    # На сколько секунд инстанс «арендует» монитор на время проверки. Должно быть
    # больше максимального таймаута (60 с): если воркер умер, проверку подберёт другой.
    claim_lease_seconds: int = 120
    alert_max_attempts: int = 8
    # Сколько ждать незавершённые проверки при остановке (docker stop даёт 10 с).
    shutdown_grace_seconds: float = 8.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
