import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401  (регистрирует таблицы в metadata)
from app.config import get_settings
from app.db import Base

config = context.config
target_metadata = Base.metadata
url = config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite не умеет ALTER, batch пересоздаёт таблицу
    )
    with context.begin_transaction():
        context.run_migrations()


async def _online() -> None:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_online())
