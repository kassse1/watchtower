"""Чистая логика принятия решений (без I/O — легко тестировать)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Transition(StrEnum):
    NONE = "none"
    WENT_DOWN = "down"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class State:
    status: str  # unknown | up | down
    consecutive_failures: int


def next_state(state: State, ok: bool, threshold: int) -> tuple[State, Transition]:
    """Алерт только при смене состояния, а не на каждую неудачную проверку.

    Монитор считается упавшим после `threshold` неудач подряд — это отсекает
    единичные сетевые сбои и спам в уведомлениях.
    """
    if ok:
        transition = Transition.RECOVERED if state.status == "down" else Transition.NONE
        return State("up", 0), transition

    failures = state.consecutive_failures + 1
    if failures >= threshold and state.status != "down":
        return State("down", failures), Transition.WENT_DOWN
    return State(state.status, failures), Transition.NONE


def should_warn_tls(
    expires_at: datetime | None, warned_for: datetime | None, now: datetime, warn_days: int
) -> bool:
    """Предупреждаем один раз на сертификат: после перевыпуска срок меняется,
    и для нового сертификата предупреждение сработает снова."""
    if expires_at is None or expires_at == warned_for:
        return False
    return expires_at - now <= timedelta(days=warn_days)


def is_stale_alert(kind: str, monitor_status: str) -> bool:
    """Алерт устарел, если к моменту доставки он противоречит текущему состоянию.

    Доставка может задержаться (канал лежал, шли повторы). Сообщение «сайт упал»,
    пришедшее после восстановления, вводит в заблуждение сильнее, чем его отсутствие:
    о сбое человек всё равно узнает из сообщения о восстановлении с длительностью простоя.
    """
    if kind == "down":
        return monitor_status != "down"
    if kind == "recovered":
        return monitor_status == "down"
    return False


def retry_delay(attempts: int, base: float = 30.0, cap: float = 3600.0) -> float:
    """Экспоненциальная задержка повторной доставки алерта: 30с, 1м, 2м, … до часа."""
    return float(min(cap, base * 2 ** max(0, attempts - 1)))
