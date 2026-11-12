"""Сквозные тесты: настоящий сервер + настоящий браузер.

Запуск:  pip install -e ".[dev,e2e]" && playwright install chromium && pytest tests/e2e
Без установленного Playwright тесты пропускаются, обычный `pytest` от них не зависит.
"""

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
import pytest_asyncio

pytest.importorskip("playwright", reason="e2e-тесты требуют Playwright")
from playwright.async_api import async_playwright  # noqa: E402

API_KEY = "e2e-secret"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Server:
    def __init__(self, url: str, api_key: str | None) -> None:
        self.url, self.api_key = url, api_key

    def api(self) -> httpx.Client:
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        return httpx.Client(base_url=self.url, headers=headers, timeout=10)


def _start(tmp_path, api_key: str | None):
    port = _free_port()
    env = os.environ | {
        "WT_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/e2e.db",
        "WT_SCHEDULER_ENABLED": "false",  # состояние задаём сами, без фоновых проверок
        "WT_ALLOW_PRIVATE_TARGETS": "true",
        "WT_API_KEY": api_key or "",
        "WT_AUTH_MAX_FAILURES": "3",
    }
    for name in ("WT_TELEGRAM_BOT_TOKEN", "WT_WEBHOOK_URL"):
        env.pop(name, None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(url + "/health", timeout=1).status_code == 200:
                return proc, Server(url, api_key)
        except httpx.HTTPError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("сервер не запустился")


@pytest.fixture
def server(tmp_path):
    proc, srv = _start(tmp_path, None)
    yield srv
    proc.terminate()
    proc.wait(timeout=15)


@pytest.fixture
def locked_server(tmp_path):
    proc, srv = _start(tmp_path, API_KEY)
    yield srv
    proc.terminate()
    proc.wait(timeout=15)


@pytest_asyncio.fixture
async def page():
    """Страница, которая запоминает ошибки: исключения JS, ошибки консоли и,
    главное, нарушения Content-Security-Policy."""
    async with async_playwright() as pw:
        # WT_E2E_CHROMIUM — путь к уже установленному браузеру (иначе берётся свой)
        browser = await pw.chromium.launch(executable_path=os.environ.get("WT_E2E_CHROMIUM"))
        page = await browser.new_page(viewport={"width": 1200, "height": 900})
        page.problems = []
        page.on("pageerror", lambda e: page.problems.append(f"pageerror: {e}"))
        page.on(
            "console",
            lambda m: page.problems.append(m.text) if m.type == "error" else None,
        )
        yield page
        await browser.close()
