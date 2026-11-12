"""Оркестрация: захват мониторов, проверки, запись результатов, доставка алертов."""

import asyncio
import contextlib
import logging
from datetime import timedelta

import httpx
from sqlalchemy import delete, select, update

from app.checker import Probe, Target, run_probe
from app.config import Settings
from app.db import Database, utcnow
from app.incidents import (
    State,
    Transition,
    is_stale_alert,
    next_state,
    retry_delay,
    should_warn_tls,
)
from app.models import AlertDelivery, CheckResult, Incident, Monitor
from app.notifier import AlertEvent, Notifier
from app.security import UnsafeTargetError, resolve_public_ip

log = logging.getLogger("watchtower.service")


async def claim_due_monitors(db: Database, limit: int, lease_seconds: int) -> list[int]:
    """Атомарно забирает мониторы, которым пора на проверку.

    Один UPDATE … RETURNING сдвигает next_check_at на время аренды, поэтому монитор
    не достанется двум воркерам — ни в соседних тиках, ни в разных инстансах сервиса.
    Если воркер умрёт посреди проверки, аренда истечёт и монитор подберёт другой.
    """
    if limit <= 0:
        return []
    now = utcnow()
    due = (
        select(Monitor.id)
        .where(Monitor.is_active, Monitor.next_check_at <= now)
        .order_by(Monitor.next_check_at)
        .limit(limit)
        .scalar_subquery()
    )
    stmt = (
        update(Monitor)
        # Условие продублировано снаружи: в Postgres конкурент, дождавшись блокировки
        # строки, перепроверит его и увидит, что монитор уже занят.
        .where(Monitor.id.in_(due), Monitor.next_check_at <= now)
        .values(next_check_at=now + timedelta(seconds=lease_seconds))
        .returning(Monitor.id)
    )
    async with db.session_factory() as s:
        ids = list((await s.scalars(stmt)).all())
        await s.commit()
    return ids


async def check_monitor(
    monitor_id: int,
    db: Database,
    client: httpx.AsyncClient,
    channels: list[str],
    settings: Settings,
) -> None:
    async with db.session_factory() as s:
        m = await s.get(Monitor, monitor_id)
        if m is None or not m.is_active:
            return
        url, method, timeout = m.url, m.method, m.timeout_seconds
        expected, keyword = m.expected_status, m.keyword

    started = utcnow()
    try:
        pinned = None if settings.allow_private_targets else await resolve_public_ip(url)
        probe = await run_probe(client, Target(url, method, timeout, expected, keyword, pinned))
    except UnsafeTargetError as exc:
        probe = Probe(False, error=f"blocked: {exc}")

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
        m.last_checked_at, m.last_latency_ms = now, probe.latency_ms
        # Считаем от начала проверки, чтобы медленные ответы не сдвигали расписание.
        m.next_check_at = started + timedelta(seconds=m.interval_seconds)

        events: list[AlertEvent] = []
        if transition is Transition.WENT_DOWN:
            s.add(Incident(monitor_id=m.id, started_at=now, last_error=probe.error))
            events.append(AlertEvent("down", m.id, m.name, m.url, now, error=probe.error))
        elif transition is Transition.RECOVERED or not probe.ok:
            inc = await s.scalar(
                select(Incident)
                .where(Incident.monitor_id == m.id, Incident.resolved_at.is_(None))
                .order_by(Incident.started_at.desc())
                .limit(1)
            )
            if transition is Transition.RECOVERED:
                downtime = None
                if inc:
                    inc.resolved_at = now
                    downtime = (now - inc.started_at).total_seconds()
                events.append(
                    AlertEvent("recovered", m.id, m.name, m.url, now, downtime_seconds=downtime)
                )
            elif inc:
                inc.last_error = probe.error

        if probe.tls_expires_at:
            m.tls_expires_at = probe.tls_expires_at
            if should_warn_tls(m.tls_expires_at, m.tls_warned_for, now, settings.tls_warn_days):
                m.tls_warned_for = m.tls_expires_at
                events.append(
                    AlertEvent(
                        "tls_expiring", m.id, m.name, m.url, now, tls_expires_at=m.tls_expires_at
                    )
                )

        # Outbox: алерты коммитятся вместе со сменой статуса. Либо записано и то и другое,
        # либо ничего — алерт не потеряется, даже если процесс упадёт сразу после коммита.
        for ev in events:
            for channel in channels:
                s.add(
                    AlertDelivery(
                        monitor_id=m.id,
                        channel=channel,
                        kind=ev.kind,
                        payload=ev.to_payload(),
                        next_attempt_at=now,
                    )
                )
        await s.commit()


async def deliver_pending_alerts(
    db: Database, notifiers: dict[str, Notifier], settings: Settings, batch: int = 50
) -> int:
    """Доставляет алерты из outbox. Гарантия at-least-once: при сбое — повтор с backoff.
    Алерты, которые за время ожидания перестали соответствовать действительности,
    не отправляются (см. is_stale_alert)."""
    now = utcnow()
    due = (
        select(AlertDelivery.id)
        .where(AlertDelivery.status == "pending", AlertDelivery.next_attempt_at <= now)
        .order_by(AlertDelivery.id)
        .limit(batch)
        .scalar_subquery()
    )
    claim = (
        update(AlertDelivery)
        .where(
            AlertDelivery.id.in_(due),
            AlertDelivery.status == "pending",
            AlertDelivery.next_attempt_at <= now,
        )
        .values(next_attempt_at=now + timedelta(seconds=60))  # аренда на время отправки
        .returning(AlertDelivery.id)
    )
    async with db.session_factory() as s:
        ids = sorted((await s.scalars(claim)).all())
        await s.commit()

    delivered = 0
    for alert_id in ids:
        async with db.session_factory() as s:
            alert = await s.get(AlertDelivery, alert_id)
            if alert is None:
                continue
            monitor = await s.get(Monitor, alert.monitor_id)
            if monitor is None or is_stale_alert(alert.kind, monitor.status):
                alert.status, alert.last_error = "skipped", "superseded"
                await s.commit()
                continue
            alert.attempts += 1
            notifier = notifiers.get(alert.channel)
            try:
                if notifier is None:
                    raise LookupError("channel is not configured")
                await notifier.send(AlertEvent.from_payload(alert.payload))
            except Exception as exc:
                # Текст исключения не сохраняем и не логируем: URL Telegram содержит токен.
                alert.last_error = type(exc).__name__
                if alert.attempts >= settings.alert_max_attempts:
                    alert.status = "failed"
                    log.error("alert %s to %s dropped after retries", alert.id, alert.channel)
                else:
                    delay = retry_delay(alert.attempts)
                    alert.next_attempt_at = utcnow() + timedelta(seconds=delay)
                    log.warning(
                        "alert %s to %s failed, retry in %ss", alert.id, alert.channel, delay
                    )
            else:
                alert.status, alert.delivered_at = "delivered", utcnow()
                delivered += 1
            await s.commit()
    return delivered


async def purge_old_data(db: Database, retention_days: int) -> int:
    cutoff = utcnow() - timedelta(days=retention_days)
    async with db.session_factory() as s:
        res = await s.execute(delete(CheckResult).where(CheckResult.checked_at < cutoff))
        await s.execute(
            delete(AlertDelivery).where(
                AlertDelivery.status != "pending", AlertDelivery.created_at < cutoff
            )
        )
        await s.commit()
        return int(getattr(res, "rowcount", 0) or 0)


class Scheduler:
    """Фоновый цикл. Проверки идут отдельными задачами: один медленный сайт
    не задерживает остальные и не блокирует доставку алертов."""

    def __init__(
        self,
        db: Database,
        client: httpx.AsyncClient,
        notifiers: dict[str, Notifier],
        settings: Settings,
    ) -> None:
        self.db, self.client, self.notifiers, self.settings = db, client, notifiers, settings
        self._checks: set[asyncio.Task[None]] = set()
        self._delivery: asyncio.Task[int] | None = None
        self._stop = asyncio.Event()

    async def _safe_check(self, monitor_id: int) -> None:
        try:
            await check_monitor(
                monitor_id, self.db, self.client, list(self.notifiers), self.settings
            )
        except Exception:
            log.exception("check failed for monitor %s", monitor_id)

    async def _safe_deliver(self) -> int:
        try:
            return await deliver_pending_alerts(self.db, self.notifiers, self.settings)
        except Exception:
            log.exception("alert delivery failed")
            return 0

    async def tick(self) -> int:
        free = self.settings.max_concurrent_checks - len(self._checks)
        ids = await claim_due_monitors(self.db, free, self.settings.claim_lease_seconds)
        for mid in ids:
            task = asyncio.create_task(self._safe_check(mid))
            self._checks.add(task)
            task.add_done_callback(self._checks.discard)
        if self.notifiers and (self._delivery is None or self._delivery.done()):
            self._delivery = asyncio.create_task(self._safe_deliver())
        return len(ids)

    async def wait_idle(self) -> None:
        while self._checks:
            await asyncio.gather(*list(self._checks))
        if self._delivery:
            await self._delivery

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Работает до stop(). Останавливается флагом, а не отменой задачи: отмена
        посреди операции с базой оставляет соединение незакрытым."""
        last_purge = utcnow() - timedelta(days=1)
        while not self._stop.is_set():
            try:
                await self.tick()
                if utcnow() - last_purge > timedelta(hours=1):
                    await purge_old_data(self.db, self.settings.retention_days)
                    last_purge = utcnow()
            except Exception:
                log.exception("scheduler iteration failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.settings.scheduler_tick_seconds)
        await self._drain()

    async def _drain(self) -> None:
        """Плавная остановка: даём начатым проверкам дописать результат. Те, что не
        уложились, отменяем — их аренда истечёт, и монитор подберёт следующий запуск."""
        pending = [*self._checks, *([self._delivery] if self._delivery else [])]
        if not pending:
            return
        _, late = await asyncio.wait(pending, timeout=self.settings.shutdown_grace_seconds)
        for task in late:
            task.cancel()
        await asyncio.gather(*late, return_exceptions=True)
