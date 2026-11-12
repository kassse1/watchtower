import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Protocol

import httpx

from app.config import Settings

log = logging.getLogger("watchtower.notifier")


@dataclass(frozen=True)
class AlertEvent:
    kind: str  # down | recovered
    monitor_id: int
    monitor_name: str
    url: str
    at: datetime
    error: str | None = None
    downtime_seconds: float | None = None

    def text(self) -> str:
        if self.kind == "down":
            return f"🔴 {self.monitor_name} недоступен\n{self.url}\nПричина: {self.error}"
        mins = (self.downtime_seconds or 0) / 60
        return f"🟢 {self.monitor_name} снова работает\n{self.url}\nПростой: {mins:.1f} мин"


class Notifier(Protocol):
    async def send(self, event: AlertEvent) -> None: ...


class TelegramNotifier:
    def __init__(self, client: httpx.AsyncClient, token: str, chat_id: str) -> None:
        self._client, self._token, self._chat_id = client, token, chat_id

    async def send(self, event: AlertEvent) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        r = await self._client.post(url, json={"chat_id": self._chat_id, "text": event.text()})
        r.raise_for_status()


class WebhookNotifier:
    def __init__(self, client: httpx.AsyncClient, url: str) -> None:
        self._client, self._url = client, url

    async def send(self, event: AlertEvent) -> None:
        payload = asdict(event) | {"at": event.at.isoformat(), "text": event.text()}
        r = await self._client.post(self._url, json=payload)
        r.raise_for_status()


def build_notifiers(settings: Settings, client: httpx.AsyncClient) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifiers.append(
            TelegramNotifier(client, settings.telegram_bot_token, settings.telegram_chat_id)
        )
    if settings.webhook_url:
        notifiers.append(WebhookNotifier(client, settings.webhook_url))
    return notifiers


async def dispatch(notifiers: list[Notifier], event: AlertEvent) -> None:
    """Сбой одного канала не должен ломать остальные и сам мониторинг."""
    for n in notifiers:
        try:
            await n.send(event)
        except Exception:
            # В лог не пишем само исключение целиком: URL Telegram содержит токен.
            log.warning("notifier %s failed for monitor %s", type(n).__name__, event.monitor_id)
