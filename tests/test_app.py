"""Запуск приложения целиком: миграции → планировщик → проверка → алерт на webhook."""

import asyncio
import json
import threading

import httpx
import pytest
import respx

from app.config import Settings, get_settings
from app.main import app, lifespan
from app.models import Monitor
from tests.conftest import TEST_DATABASE_URL


@respx.mock
async def test_startup_to_alert(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL or f"sqlite+aiosqlite:///{tmp_path}/app.db",
        allow_private_targets=True,
        scheduler_tick_seconds=0.02,
        webhook_url="https://hooks.example/alerts",
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app.dependency_overrides[get_settings] = lambda: settings
    respx.get("https://broken.example/").respond(503)
    hook = respx.post("https://hooks.example/alerts").respond(200)

    if TEST_DATABASE_URL:  # начинаем с чистой базы, как при первом запуске
        from app.db import Database

        db = Database(TEST_DATABASE_URL)
        await db.drop_all()
        async with db.engine.begin() as conn:
            await conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        await db.dispose()

    try:
        async with lifespan(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
                body = {"name": "Broken", "url": "https://broken.example/", "failure_threshold": 1}
                assert (await api.post("/api/monitors", json=body)).status_code == 201
                for _ in range(200):
                    if hook.called:
                        break
                    await asyncio.sleep(0.02)
                assert hook.called, "алерт не дошёл до webhook"
                assert json.loads(hook.calls.last.request.content)["kind"] == "down"

                assert (await api.get("/api/monitors/1")).json()["status"] == "down"
                assert (await api.get("/api/public/status")).json()["overall"] == "outage"
                (incident,) = (await api.get("/api/incidents?open_only=true")).json()
                assert incident["monitor_id"] == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(bool(TEST_DATABASE_URL), reason="проверяет потоки драйвера SQLite")
@respx.mock
async def test_shutdown_with_checks_in_flight_leaks_no_connections(tmp_path, monkeypatch):
    """Регрессия: остановка отменяла незавершённые проверки, но не дожидалась их,
    и база закрывалась, пока они держали соединения."""

    async def slow(request):
        await asyncio.sleep(0.01)
        return httpx.Response(500)

    respx.get("https://x.test/").mock(side_effect=slow)
    for i in range(6):
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path}/s{i}.db",
            allow_private_targets=True,
            scheduler_tick_seconds=0.005,
        )
        monkeypatch.setattr("app.main.get_settings", lambda s=settings: s)
        async with lifespan(app):
            async with app.state.db.session_factory() as session:
                session.add_all(
                    Monitor(name="m", url="https://x.test/", failure_threshold=1) for _ in range(30)
                )
                await session.commit()
            await asyncio.sleep(0.02 + (i % 7) * 0.004)  # останавливаемся посреди проверок

    await asyncio.sleep(0.1)
    leaked = [t.name for t in threading.enumerate() if "_connection_worker_thread" in t.name]
    assert leaked == []
