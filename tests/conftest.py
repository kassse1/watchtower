import httpx
import pytest
import pytest_asyncio

from app.config import Settings, get_settings
from app.db import Database
from app.main import app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
        allow_private_targets=True,
        scheduler_enabled=False,
    )


@pytest_asyncio.fixture
async def db(settings):
    database = Database(settings.database_url)
    await database.create_all()
    yield database
    await database.dispose()


@pytest_asyncio.fixture
async def api(db, settings):
    app.state.db = db
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class FakeNotifier:
    def __init__(self):
        self.events = []

    async def send(self, event):
        self.events.append(event)
