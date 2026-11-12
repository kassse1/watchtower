"""Чистая логика переходов состояния монитора (без I/O — легко тестировать)."""

from dataclasses import dataclass
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
