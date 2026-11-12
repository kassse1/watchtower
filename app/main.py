import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.api import ops_router, public_router, router
from app.config import get_settings
from app.db import Database
from app.migrate import upgrade
from app.notifier import build_notifiers
from app.service import Scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # иначе строка в лог на каждую проверку
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.auto_migrate:
        await asyncio.to_thread(upgrade, settings.database_url)
    db = Database(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
    app.state.db = db
    async with httpx.AsyncClient(headers={"User-Agent": "Watchtower/0.2"}) as client:
        scheduler = Scheduler(db, client, build_notifiers(settings, client), settings)
        task = asyncio.create_task(scheduler.run()) if settings.scheduler_enabled else None
        try:
            yield
        finally:
            if task:
                scheduler.stop()
                await task
    await db.dispose()


app = FastAPI(title="Watchtower", version="0.2.0", lifespan=lifespan)
app.include_router(router)
app.include_router(public_router)
app.include_router(ops_router)


@app.get("/health", include_in_schema=False)
async def health(request: Request) -> dict[str, str]:
    """Проверяет и базу: «процесс жив, но БД недоступна» — это не healthy."""
    try:
        async with request.app.state.db.session_factory() as s:
            await s.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(503, "database unavailable") from exc
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/status", include_in_schema=False)
async def status_page() -> FileResponse:
    if not get_settings().public_status_page:
        raise HTTPException(404)
    return FileResponse(STATIC / "status.html")
