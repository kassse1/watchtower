from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def upgrade(database_url: str) -> None:
    """Синхронная функция: внутри env.py свой event loop, поэтому из async-кода
    вызывать через asyncio.to_thread."""
    command.upgrade(alembic_config(database_url), "head")
