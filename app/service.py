import asyncio
import logging
from datetime import timedelta

import httpx
from sqlalchemy import delete, select

from app.checker import Probe, run_probe
from app.config import Settings
from app.db import Database, utcnow
from app.incidents import State, Transition, next_state
from app.models import CheckResult, Incident, Monitor
from app.notifier import AlertEvent, Notifier, dispatch
from app.security import UnsafeTargetError, assert_public_url

log = logging.getLogger("watchtower.service")


async def check_monitor(
    monitor_id: int,
    db: Database,
    client: httpx.AsyncClient,
    notifiers: list[Notifier],
    settings: Settings,
) -> None:
    async with db.session_factory() as s:
        m = await s.get(Monitor, monitor_id)
        if m is None or not m.is_active:
            return
        spec = dict(
            url=m.url,
            method=m.method,
            timeout=m.timeout_seconds,
            expected_status=m.expected_status,
            keyword=m.keyword,
        )

    try:
        if not settings.allow_private_targets:
            await assert_public_url(spec["url"])
        probe = await run_probe(client, **spec)
    except UnsafeTargetError as exc:
        probe = Probe(False, error=f"blocked: {exc}")

    event: AlertEvent | None = None
    async with db.session_factory() as s:
        m = await s.get(Monitor, monitor_id)
        if m is None:  # удалён, пока шла проверка
            return
        now = utcnow()
        s.add(
            CheckResult(
                monitor_id=m.id,
                checked_at=now,
                ok=probe.ok,
                status_code=probe.status_code,
                latency_ms=probe.latency_ms,
                error=probe.error,
            )
        )
        new, transition = next_state(
            State(m.status, m.consecutive_failures), probe.ok, m.failure_threshold
        )
        m.status, m.consecutive_failures = new.status, new.consecutive_failures
        m.last_checked_at = now
        m.next_check_at = now + timedelta(seconds=m.interval_seconds)

        if transition is Transition.WENT_DOWN:
            s.add(Incident(monitor_id=m.id, started_at=now, last_error=probe.error))
            event = AlertEvent("down", m.id, m.name, m.url, now, error=probe.error)
        elif transition is Transition.RECOVERED:
            inc = await s.scalar(
                select(Incident)
                .where(Incident.monitor_id == m.id, Incident.resolved_at.is_(None))
                .order_by(Incident.started_at.desc())
            )
            downtime = None
            if inc:
                inc.resolved_at = now
                downtime = (now - inc.started_at).total_seconds()
            event = AlertEvent("recovered", m.id, m.name, m.url, now, downtime_seconds=downtime)
        elif not probe.ok:
            inc = await s.scalar(
                select(Incident).where(Incident.monitor_id == m.id, Incident.resolved_at.is_(None))
            )
            if inc:
                inc.last_error = probe.error
        await s.commit()

    if event:  # уведомляем после коммита: сбой канала не откатывает данные
        await dispatch(notifiers, event)


async def run_due_checks(
    db: Database, client: httpx.AsyncClient, notifiers: list[Notifier], settings: Settings
) -> int:
    async with db.session_factory() as s:
        ids = (
            await s.scalars(
                select(Monitor.id).where(Monitor.is_active, Monitor.next_check_at <= utcnow())
            )
        ).all()
    sem = asyncio.Semaphore(settings.max_concurrent_checks)

    async def guarded(mid: int) -> None:
        async with sem:
            try:
                await check_monitor(mid, db, client, notifiers, settings)
            except Exception:
                log.exception("check failed for monitor %s", mid)

    await asyncio.gather(*(guarded(i) for i in ids))
    return len(ids)


async def purge_old_results(db: Database, retention_days: int) -> int:
    cutoff = utcnow() - timedelta(days=retention_days)
    async with db.session_factory() as s:
        res = await s.execute(delete(CheckResult).where(CheckResult.checked_at < cutoff))
        await s.commit()
        return res.rowcount or 0


async def scheduler_loop(
    db: Database, client: httpx.AsyncClient, notifiers: list[Notifier], settings: Settings
) -> None:
    last_purge = utcnow() - timedelta(days=1)
    while True:
        try:
            await run_due_checks(db, client, notifiers, settings)
            if utcnow() - last_purge > timedelta(hours=1):
                await purge_old_results(db, settings.retention_days)
                last_purge = utcnow()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler iteration failed")
        await asyncio.sleep(settings.scheduler_tick_seconds)
