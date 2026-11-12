import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import router
from app.config import get_settings
from app.db import Database
from app.notifier import build_notifiers
from app.service import scheduler_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db = Database(settings.database_url)
    await db.create_all()
    app.state.db = db
    async with httpx.AsyncClient(headers={"User-Agent": "Watchtower/0.1"}) as client:
        task = None
        if settings.scheduler_enabled:
            notifiers = build_notifiers(settings, client)
            task = asyncio.create_task(scheduler_loop(db, client, notifiers, settings))
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    await db.dispose()


app = FastAPI(title="Watchtower", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(STATIC / "index.html")
