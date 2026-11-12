from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from app.config import Settings


@dataclass(frozen=True)
class AlertEvent:
    kind: str  # down | recovered | tls_expiring
    monitor_id: int
    monitor_name: str
    url: str
    at: datetime
    error: str | None = None
    downtime_seconds: float | None = None
    tls_expires_at: datetime | None = None

    def text(self) -> str:
        if self.kind == "down":
            return f"🔴 {self.monitor_name} недоступен\n{self.url}\nПричина: {self.error}"
        if self.kind == "tls_expiring" and self.tls_expires_at:
            days = (self.tls_expires_at - self.at).days
            return (
                f"🟡 {self.monitor_name}: SSL-сертификат истекает через {days} дн.\n"
                f"{self.url}\nДействителен до {self.tls_expires_at:%Y-%m-%d}"
            )
        mins = (self.downtime_seconds or 0) / 60
        return f"🟢 {self.monitor_name} снова работает\n{self.url}\nПростой: {mins:.1f} мин"

    def to_payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["at"] = self.at.isoformat()
        data["tls_expires_at"] = self.tls_expires_at.isoformat() if self.tls_expires_at else None
        return data

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "AlertEvent":
        fields = dict(data)
        fields["at"] = datetime.fromisoformat(fields["at"])
        if fields.get("tls_expires_at"):
            fields["tls_expires_at"] = datetime.fromisoformat(fields["tls_expires_at"])
        return cls(**fields)


class Notifier(Protocol):
    async def send(self, event: AlertEvent) -> None: ...


class TelegramNotifier:
    def __init__(self, client: httpx.AsyncClient, token: str, chat_id: str) -> None:
        self._client, self._token, self._chat_id = client, token, chat_id

    async def send(self, event: AlertEvent) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        r = await self._client.post(
            url, json={"chat_id": self._chat_id, "text": event.text()}, timeout=10
        )
        r.raise_for_status()


class WebhookNotifier:
    def __init__(self, client: httpx.AsyncClient, url: str) -> None:
        self._client, self._url = client, url

    async def send(self, event: AlertEvent) -> None:
        r = await self._client.post(
            self._url, json=event.to_payload() | {"text": event.text()}, timeout=10
        )
        r.raise_for_status()


def build_notifiers(settings: Settings, client: httpx.AsyncClient) -> dict[str, Notifier]:
    """Каналы по именам: имя пишется в outbox, чтобы повторять доставку только туда,
    где она не удалась."""
    notifiers: dict[str, Notifier] = {}
    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifiers["telegram"] = TelegramNotifier(
            client, settings.telegram_bot_token, settings.telegram_chat_id
        )
    if settings.webhook_url:
        notifiers["webhook"] = WebhookNotifier(client, settings.webhook_url)
    return notifiers
