"""Экспорт в формате Prometheus — пишется руками, чтобы не тянуть зависимость ради 4 метрик."""

from collections.abc import Sequence
from datetime import datetime

from app.models import Monitor


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_metrics(monitors: Sequence[Monitor], now: datetime) -> str:
    families: list[tuple[str, str, list[tuple[Monitor, float]]]] = [
        (
            "watchtower_monitor_up",
            "1 if the monitor is up, 0 if down",
            [(m, 1.0 if m.status == "up" else 0.0) for m in monitors if m.status != "unknown"],
        ),
        (
            "watchtower_monitor_consecutive_failures",
            "Failed checks in a row",
            [(m, float(m.consecutive_failures)) for m in monitors],
        ),
        (
            "watchtower_monitor_latency_ms",
            "Latency of the last check in milliseconds",
            [(m, m.last_latency_ms) for m in monitors if m.last_latency_ms is not None],
        ),
        (
            "watchtower_monitor_tls_days_left",
            "Days until the TLS certificate expires",
            [
                (m, round((m.tls_expires_at - now).total_seconds() / 86400, 2))
                for m in monitors
                if m.tls_expires_at is not None
            ],
        ),
    ]
    lines: list[str] = []
    for name, help_text, samples in families:
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
        lines += [
            f'{name}{{monitor_id="{m.id}",name="{_label(m.name)}"}} {value:g}'
            for m, value in samples
        ]
    return "\n".join(lines) + "\n"
