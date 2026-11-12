import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.config import Settings
from app.notifier import AlertEvent, TelegramNotifier, WebhookNotifier, build_notifiers

AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
DOWN = AlertEvent("down", 7, "Shop", "https://shop.example", AT, error="expected 200, got 502")


def test_payload_roundtrip_preserves_event():
    tls = AlertEvent("tls_expiring", 1, "A", "https://a", AT, tls_expires_at=AT + timedelta(days=3))
    for event in (DOWN, tls):
        restored = AlertEvent.from_payload(json.loads(json.dumps(event.to_payload())))
        assert restored == event


def test_messages():
    assert "недоступен" in DOWN.text() and "502" in DOWN.text()
    recovered = AlertEvent("recovered", 7, "Shop", "https://shop.example", AT, downtime_seconds=90)
    assert "1.5 мин" in recovered.text()
    tls = AlertEvent("tls_expiring", 1, "A", "https://a", AT, tls_expires_at=AT + timedelta(days=3))
    assert "через 3 дн." in tls.text() and "2026-03-04" in tls.text()


@respx.mock
async def test_telegram_request():
    route = respx.post("https://api.telegram.org/botTOKEN/sendMessage").respond(200, json={})
    async with httpx.AsyncClient() as client:
        await TelegramNotifier(client, "TOKEN", "42").send(DOWN)
    body = json.loads(route.calls.last.request.content)
    assert body["chat_id"] == "42" and "Shop" in body["text"]


@respx.mock
async def test_webhook_request_and_error_propagation():
    route = respx.post("https://hooks.example/x").respond(200)
    async with httpx.AsyncClient() as client:
        hook = WebhookNotifier(client, "https://hooks.example/x")
        await hook.send(DOWN)
        body = json.loads(route.calls.last.request.content)
        assert body["kind"] == "down" and body["monitor_id"] == 7 and "text" in body

        route.respond(500)  # ошибка должна дойти до outbox, чтобы он повторил доставку
        with pytest.raises(httpx.HTTPStatusError):
            await hook.send(DOWN)


async def test_channels_are_built_from_settings():
    async with httpx.AsyncClient() as client:
        none = Settings(_env_file=None)
        assert build_notifiers(none, client) == {}
        partial = Settings(_env_file=None, telegram_bot_token="t")  # без chat_id канал не создаём
        assert build_notifiers(partial, client) == {}
        full = Settings(
            _env_file=None, telegram_bot_token="t", telegram_chat_id="1", webhook_url="https://h"
        )
        assert set(build_notifiers(full, client)) == {"telegram", "webhook"}
