from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import utcnow
from app.models import CheckResult

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


def percentile(sorted_values: list[float], p: float) -> float | None:
    """Перцентиль методом nearest-rank."""
    if not sorted_values:
        return None
    idx = max(0, min(len(sorted_values) - 1, round(p / 100 * len(sorted_values) + 0.5) - 1))
    return sorted_values[idx]


def _pct(ok: int, total: int) -> float | None:
    return round(ok / total * 100, 3) if total else None


async def monitor_stats(session: AsyncSession, monitor_id: int) -> dict[str, Any]:
    now = utcnow()
    columns = []
    for delta in WINDOWS.values():  # все окна одним запросом
        in_window = CheckResult.checked_at >= now - delta
        columns.append(func.coalesce(func.sum(case((in_window, 1), else_=0)), 0))
        columns.append(func.coalesce(func.sum(case((in_window & CheckResult.ok, 1), else_=0)), 0))
    row = (
        await session.execute(
            select(*columns).where(
                CheckResult.monitor_id == monitor_id,
                CheckResult.checked_at >= now - max(WINDOWS.values()),
            )
        )
    ).one()
    uptime = {label: _pct(int(row[i * 2 + 1]), int(row[i * 2])) for i, label in enumerate(WINDOWS)}

    latencies = sorted(
        v
        for v in (
            await session.scalars(
                select(CheckResult.latency_ms).where(
                    CheckResult.monitor_id == monitor_id,
                    CheckResult.checked_at >= now - timedelta(hours=24),
                    CheckResult.latency_ms.is_not(None),
                )
            )
        ).all()
        if v is not None
    )
    p95 = percentile(latencies, 95)
    return {
        "uptime_percent": uptime,
        "latency_ms_24h": {
            "avg": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "p95": round(p95, 1) if p95 is not None else None,
        },
    }


async def daily_uptime(session: AsyncSession, days: int) -> dict[int, list[dict[str, Any]]]:
    """Uptime по дням для всех мониторов одним запросом (для status-страницы)."""
    now = utcnow()
    day = func.date(CheckResult.checked_at)
    rows = (
        await session.execute(
            select(
                CheckResult.monitor_id,
                day,
                func.count(),
                func.sum(case((CheckResult.ok, 1), else_=0)),
            )
            .where(CheckResult.checked_at >= now - timedelta(days=days))
            .group_by(CheckResult.monitor_id, day)
        )
    ).all()
    by_monitor: dict[int, dict[str, float | None]] = defaultdict(dict)
    for monitor_id, d, total, ok in rows:
        by_monitor[monitor_id][str(d)] = _pct(int(ok), int(total))

    calendar = [(now - timedelta(days=i)).date().isoformat() for i in range(days - 1, -1, -1)]
    return defaultdict(
        lambda: [{"date": d, "uptime": None} for d in calendar],
        {
            mid: [{"date": d, "uptime": values.get(d)} for d in calendar]
            for mid, values in by_monitor.items()
        },
    )


async def recent_checks(
    session: AsyncSession, monitor_id: int, limit: int
) -> list[tuple[datetime, bool, float | None]]:
    rows = (
        await session.execute(
            select(CheckResult.checked_at, CheckResult.ok, CheckResult.latency_ms)
            .where(CheckResult.monitor_id == monitor_id)
            .order_by(CheckResult.checked_at.desc())
            .limit(limit)
        )
    ).all()
    return [(r[0], bool(r[1]), r[2]) for r in reversed(rows)]
