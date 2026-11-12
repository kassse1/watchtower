"""Ограничение числа неудачных попыток входа — защита API-ключа от перебора."""

import time
from collections import deque
from collections.abc import Callable


class FailureLimiter:
    """Скользящее окно неудач на клиента.

    Хранится в памяти процесса: при нескольких инстансах лимит действует на каждый
    отдельно. Для перебора ключа это всё равно замедление на порядки, а общий счётчик
    потребовал бы Redis ради одной функции.
    """

    def __init__(
        self,
        max_failures: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = 10_000,
    ) -> None:
        self.max_failures, self.window = max_failures, window_seconds
        self._clock, self._max_clients = clock, max_clients
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, client: str) -> deque[float]:
        events = self._failures.get(client)
        if events is None:
            return deque()
        cutoff = self._clock() - self.window
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            del self._failures[client]
        return events

    def retry_after(self, client: str) -> int:
        """Сколько секунд клиент ещё заблокирован; 0 — можно пробовать."""
        events = self._recent(client)
        if len(events) < self.max_failures:
            return 0
        return max(1, int(events[0] + self.window - self._clock()) + 1)

    def record_failure(self, client: str) -> None:
        if client not in self._failures and len(self._failures) >= self._max_clients:
            # Не даём атакующему с множеством адресов раздуть память: забываем самого старого.
            self._failures.pop(next(iter(self._failures)))
        self._failures.setdefault(client, deque()).append(self._clock())
