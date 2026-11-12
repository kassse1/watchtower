import asyncio

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db import Base, Database
from app.migrate import upgrade
from tests.conftest import TEST_DATABASE_URL


@pytest.fixture
def url(tmp_path):
    return TEST_DATABASE_URL or f"sqlite+aiosqlite:///{tmp_path}/m.db"


async def test_migrations_match_models(url):
    """Забыли сгенерировать миграцию после правки моделей — тест упадёт.
    Без него расхождение схемы всплывает только на проде."""
    db = Database(url)
    await db.drop_all()
    async with db.engine.begin() as conn:
        await conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    await asyncio.to_thread(upgrade, url)

    def diff(conn):
        tables = set(inspect(conn).get_table_names())
        return tables, compare_metadata(MigrationContext.configure(conn), Base.metadata)

    async with db.engine.connect() as conn:
        tables, drift = await conn.run_sync(diff)
    await asyncio.to_thread(upgrade, url)  # повторный запуск безопасен
    async with db.engine.begin() as conn:
        await conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    await db.dispose()

    assert {"monitors", "check_results", "incidents", "alert_deliveries"} <= tables
    assert drift == []
