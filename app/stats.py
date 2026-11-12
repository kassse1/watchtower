from datetime import timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import utcnow
from app.models import CheckResult


def percentile(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    idx = max(0, min(len(sorted_values) - 1, round(p / 100 * len(sorted_values) + 0.5) - 1))
    return sorted_values[idx]


async def monitor_stats(session: AsyncSession, monitor_id: int) -> dict:
    now = utcnow()
    uptime: dict[str, float | None] = {}
    for label, delta in (
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("30d", timedelta(days=30)),
    ):
        total, ok = (
            await session.execute(
                select(
                    func.count(), func.coalesce(func.sum(case((CheckResult.ok, 1), else_=0)), 0)
                ).where(CheckResult.monitor_id == monitor_id, CheckResult.checked_at >= now - delta)
            )
        ).one()
        uptime[label] = round(ok / total * 100, 3) if total else None

    latencies = sorted(
        (
            await session.scalars(
                select(CheckResult.latency_ms).where(
                    CheckResult.monitor_id == monitor_id,
                    CheckResult.checked_at >= now - timedelta(hours=24),
                    CheckResult.latency_ms.is_not(None),
                )
            )
        ).all()
    )
    return {
        "uptime_percent": uptime,
        "latency_ms_24h": {
            "avg": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "p95": round(p, 1) if (p := percentile(latencies, 95)) is not None else None,
        },
    }
