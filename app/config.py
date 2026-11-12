from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WT_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./watchtower.db"
    auto_migrate: bool = True
    # Размер пула соединений (не для SQLite). У облачных Postgres лимит соединений
    # небольшой, поэтому значения по умолчанию скромные.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    api_key: str | None = None
    # Защита ключа от перебора: после стольких неверных ключей с одного адреса — пауза.
    auth_max_failures: int = 10
    auth_window_seconds: int = 300
    # Включать только за обратным прокси (Render, nginx): тогда адрес клиента берётся
    # из X-Forwarded-For. Без прокси этот заголовок может подделать сам клиент.
    trust_proxy_headers: bool = False
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

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Хостинги выдают строку вида postgres://…?sslmode=require. Приводим её к виду,
        который понимает асинхронный драйвер, чтобы её можно было вставить как есть."""
        url = make_url(value)
        if url.drivername not in ("postgres", "postgresql"):
            return value
        query = dict(url.query)
        sslmode = query.pop("sslmode", None)
        if sslmode and "ssl" not in query:
            query["ssl"] = sslmode  # asyncpg называет этот параметр иначе
        url = url.set(drivername="postgresql+asyncpg", query=query)
        return url.render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
