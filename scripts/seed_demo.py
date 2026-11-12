"""Наполняет базу правдоподобной историей за 30 дней, чтобы посмотреть интерфейс без ожидания.

WT_DATABASE_URL=sqlite+aiosqlite:///./demo.db python scripts/seed_demo.py
WT_DATABASE_URL=sqlite+aiosqlite:///./demo.db WT_SCHEDULER_ENABLED=false uvicorn app.main:app
"""

import asyncio
import random
from datetime import timedelta

from app.config import get_settings
from app.db import Database, utcnow
from app.migrate import upgrade
from app.models import CheckResult, Incident, Monitor

# название, url, базовая латентность, [(дней назад, длительность сбоя в минутах)], упал ли сейчас
DEMO = [
    ("Интернет-магазин", "https://shop.example.com", 180, [(12, 95)], False),
    ("API оплаты", "https://pay.example.com/health", 60, [], False),
    ("Личный кабинет", "https://account.example.com", 240, [(21, 300), (4, 40)], False),
    ("Блог", "https://blog.example.com", 320, [(0, 25)], True),
]
STEP = timedelta(minutes=15)


async def main() -> None:
    url = get_settings().database_url
    await asyncio.to_thread(upgrade, url)
    db = Database(url)
    rng = random.Random(7)
    now = utcnow()
    async with db.session_factory() as s:
        for name, site, latency, outages, down_now in DEMO:
            m = Monitor(
                name=name,
                url=site,
                status="down" if down_now else "up",
                consecutive_failures=3 if down_now else 0,
                last_checked_at=now,
                last_latency_ms=None if down_now else float(latency),
                next_check_at=now + timedelta(days=365),
                tls_expires_at=now + timedelta(days=rng.choice([9, 48, 71, 83])),
            )
            s.add(m)
            await s.flush()
            windows = []
            for days_ago, minutes in outages:
                end = now if days_ago == 0 else now - timedelta(days=days_ago)
                windows.append((end - timedelta(minutes=minutes), end))
                s.add(
                    Incident(
                        monitor_id=m.id,
                        started_at=windows[-1][0],
                        resolved_at=None if days_ago == 0 else end,
                        last_error="expected 200, got 502",
                    )
                )
            t = now - timedelta(days=30)
            while t <= now:
                failed = any(a <= t <= b for a, b in windows)
                s.add(
                    CheckResult(
                        monitor_id=m.id,
                        checked_at=t,
                        ok=not failed,
                        status_code=502 if failed else 200,
                        latency_ms=None if failed else max(5.0, rng.gauss(latency, latency * 0.15)),
                        error="expected 200, got 502" if failed else None,
                    )
                )
                t += STEP
        await s.commit()
    await db.dispose()
    print(f"Готово: {len(DEMO)} монитора в {url}")


if __name__ == "__main__":
    asyncio.run(main())
