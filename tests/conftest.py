import os

import httpx
import pytest
import pytest_asyncio

from app.config import Settings, get_settings
from app.db import Database
from app.main import app
from app.ratelimit import FailureLimiter

# Набор гоняется и на SQLite (по умолчанию), и на Postgres: WT_TEST_DATABASE_URL=postgresql+asyncpg://…
TEST_DATABASE_URL = os.environ.get("WT_TEST_DATABASE_URL")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL or f"sqlite+aiosqlite:///{tmp_path}/t.db",
        allow_private_targets=True,
        scheduler_enabled=False,
        api_key=None,
        telegram_bot_token=None,
        webhook_url=None,
    )


@pytest_asyncio.fixture
async def db(settings):
    database = Database(settings.database_url)
    await database.drop_all()
    await database.create_all()
    yield database
    await database.dispose()


@pytest_asyncio.fixture
async def api(db, settings):
    app.state.db = db
    app.state.auth_limiter = FailureLimiter(
        settings.auth_max_failures, settings.auth_window_seconds
    )
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class FakeNotifier:
    def __init__(self, fail_times: int = 0):
        self.events = []
        self.fail_times = fail_times

    async def send(self, event):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("channel is down")
        self.events.append(event)
