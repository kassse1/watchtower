import asyncio
from datetime import timedelta

import httpx
import respx
from sqlalchemy import select

from app.db import Database, utcnow
from app.models import AlertDelivery, CheckResult, Incident, Monitor
from app.service import (
    Scheduler,
    check_monitor,
    claim_due_monitors,
    deliver_pending_alerts,
    purge_old_data,
)
from tests.conftest import FakeNotifier

URL = "https://example.test/"


async def make_monitor(db, url=URL, **kw) -> int:
    async with db.session_factory() as s:
        m = Monitor(name="site", url=url, **({"failure_threshold": 2} | kw))
        s.add(m)
        await s.commit()
        return m.id


async def alerts(db) -> list[AlertDelivery]:
    async with db.session_factory() as s:
        return list((await s.scalars(select(AlertDelivery).order_by(AlertDelivery.id))).all())


async def make_due(db):
    async with db.session_factory() as s:
        for a in (await s.scalars(select(AlertDelivery))).all():
            a.next_attempt_at = utcnow() - timedelta(seconds=1)
        await s.commit()


@respx.mock
async def test_full_incident_lifecycle(db, settings):
    mid = await make_monitor(db)
    notifier = FakeNotifier()
    route = respx.get(URL)

    async def step():
        async with httpx.AsyncClient() as client:
            await check_monitor(mid, db, client, ["fake"], settings)
        await deliver_pending_alerts(db, {"fake": notifier}, settings)

    route.respond(500)
    await step()
    assert notifier.events == []  # 1 сбой < порога

    await step()
    assert [e.kind for e in notifier.events] == ["down"]
    assert "500" in notifier.events[0].error

    await step()
    assert len(notifier.events) == 1  # без спама, пока лежит

    route.respond(200)
    await step()
    assert [e.kind for e in notifier.events] == ["down", "recovered"]
    assert notifier.events[1].downtime_seconds is not None

    async with db.session_factory() as s:
        incidents = (await s.scalars(select(Incident))).all()
        assert len(incidents) == 1 and incidents[0].resolved_at is not None
        m = await s.get(Monitor, mid)
        assert m.status == "up" and m.last_latency_ms is not None
        assert len((await s.scalars(select(CheckResult))).all()) == 4
    assert [a.status for a in await alerts(db)] == ["delivered", "delivered"]


@respx.mock
async def test_failed_alert_is_retried_not_lost(db, settings):
    mid = await make_monitor(db, failure_threshold=1)
    respx.get(URL).respond(500)
    async with httpx.AsyncClient() as client:
        await check_monitor(mid, db, client, ["fake"], settings)

    flaky = FakeNotifier(fail_times=2)
    assert await deliver_pending_alerts(db, {"fake": flaky}, settings) == 0
    (a,) = await alerts(db)
    assert (a.status, a.attempts, a.last_error) == ("pending", 1, "RuntimeError")
    assert a.next_attempt_at > utcnow()  # backoff: сразу не повторяем

    assert await deliver_pending_alerts(db, {"fake": flaky}, settings) == 0  # ещё рано
    assert (await alerts(db))[0].attempts == 1

    for _ in range(2):
        await make_due(db)
        await deliver_pending_alerts(db, {"fake": flaky}, settings)
    (a,) = await alerts(db)
    assert (a.status, a.attempts) == ("delivered", 3)
    assert len(flaky.events) == 1


@respx.mock
async def test_only_the_failed_channel_is_retried(db, settings):
    mid = await make_monitor(db, failure_threshold=1)
    respx.get(URL).respond(500)
    async with httpx.AsyncClient() as client:
        await check_monitor(mid, db, client, ["good", "bad"], settings)

    good, bad = FakeNotifier(), FakeNotifier(fail_times=1)
    notifiers = {"good": good, "bad": bad}
    await deliver_pending_alerts(db, notifiers, settings)
    await make_due(db)
    await deliver_pending_alerts(db, notifiers, settings)
    assert len(good.events) == 1 and len(bad.events) == 1  # без дублей в исправный канал


@respx.mock
async def test_alert_gives_up_after_max_attempts(db, settings):
    settings.alert_max_attempts = 2
    mid = await make_monitor(db, failure_threshold=1)
    respx.get(URL).respond(500)
    async with httpx.AsyncClient() as client:
        await check_monitor(mid, db, client, ["fake"], settings)
    dead = FakeNotifier(fail_times=99)
    for _ in range(3):
        await make_due(db)
        await deliver_pending_alerts(db, {"fake": dead}, settings)
    (a,) = await alerts(db)
    assert (a.status, a.attempts) == ("failed", 2)


@respx.mock
async def test_private_target_blocked_at_check_time(db, settings):
    settings.allow_private_targets = False
    mid = await make_monitor(db, url="http://169.254.169.254/", failure_threshold=1)
    async with httpx.AsyncClient() as client:
        await check_monitor(mid, db, client, [], settings)
    async with db.session_factory() as s:
        r = (await s.scalars(select(CheckResult))).one()
        assert not r.ok and r.error.startswith("blocked")
    assert not respx.calls  # запрос не ушёл вообще


async def test_claim_is_exclusive_between_instances(db, settings):
    """Два инстанса сервиса на одной базе не должны проверять один монитор дважды."""
    ids = {await make_monitor(db) for _ in range(20)}
    other = Database(settings.database_url)
    try:
        a, b = await asyncio.gather(
            claim_due_monitors(db, 50, 120), claim_due_monitors(other, 50, 120)
        )
    finally:
        await other.dispose()
    assert set(a) | set(b) == ids
    assert not set(a) & set(b)
    assert await claim_due_monitors(db, 50, 120) == []  # всё в аренде


async def test_claim_respects_limit_pause_and_schedule(db):
    await make_monitor(db, is_active=False)
    await make_monitor(db, next_check_at=utcnow() + timedelta(hours=1))
    due = [await make_monitor(db) for _ in range(3)]
    first = await claim_due_monitors(db, 2, 120)
    second = await claim_due_monitors(db, 2, 120)
    assert len(first) == 2 and len(second) == 1 and set(first + second) == set(due)


async def test_expired_lease_is_reclaimed_after_worker_crash(db):
    mid = await make_monitor(db)
    assert await claim_due_monitors(db, 10, lease_seconds=0) == [mid]  # воркер «умер»
    assert await claim_due_monitors(db, 10, lease_seconds=120) == [mid]


@respx.mock
async def test_slow_monitor_does_not_block_others(db, settings):
    release = asyncio.Event()

    async def slow(request):
        await release.wait()
        return httpx.Response(200)

    slow_id = await make_monitor(db, url="https://slow.test/")
    fast_id = await make_monitor(db, url="https://fast.test/")
    respx.get("https://slow.test/").mock(side_effect=slow)
    respx.get("https://fast.test/").respond(200)

    async def checked(mid):
        async with db.session_factory() as s:
            return (await s.get(Monitor, mid)).last_checked_at is not None

    async with httpx.AsyncClient() as client:
        scheduler = Scheduler(db, client, {}, settings)
        assert await scheduler.tick() == 2
        for _ in range(100):
            if await checked(fast_id):
                break
            await asyncio.sleep(0.01)
        assert await checked(fast_id) and not await checked(slow_id)
        assert await scheduler.tick() == 0  # медленный в аренде, повторно не берём
        release.set()
        await scheduler.wait_idle()
    assert await checked(slow_id)


@respx.mock
async def test_scheduler_delivers_alerts_end_to_end(db, settings):
    await make_monitor(db, failure_threshold=1)
    respx.get(URL).respond(500)
    notifier = FakeNotifier()
    async with httpx.AsyncClient() as client:
        scheduler = Scheduler(db, client, {"fake": notifier}, settings)
        await scheduler.tick()
        await scheduler.wait_idle()
        await scheduler.tick()  # доставка идёт на следующем тике
        await scheduler.wait_idle()
    assert [e.kind for e in notifier.events] == ["down"]


@respx.mock
async def test_next_check_is_scheduled_from_start(db, settings):
    mid = await make_monitor(db, interval_seconds=300)
    respx.get(URL).respond(200)
    before = utcnow()
    async with httpx.AsyncClient() as client:
        await check_monitor(mid, db, client, [], settings)
    async with db.session_factory() as s:
        nxt = (await s.get(Monitor, mid)).next_check_at
    assert timedelta(seconds=299) < nxt - before < timedelta(seconds=302)


async def test_tls_expiry_warns_once(db, settings, monkeypatch):
    from app import service
    from app.checker import Probe

    expires = utcnow() + timedelta(days=5)

    async def fake_probe(client, target):
        return Probe(True, 200, 12.0, None, expires)

    monkeypatch.setattr(service, "run_probe", fake_probe)
    mid = await make_monitor(db)
    async with httpx.AsyncClient() as client:
        for _ in range(3):
            await check_monitor(mid, db, client, ["fake"], settings)
    rows = await alerts(db)
    assert [a.kind for a in rows] == ["tls_expiring"]
    notifier = FakeNotifier()
    await deliver_pending_alerts(db, {"fake": notifier}, settings)
    assert "истекает через" in notifier.events[0].text()


async def test_retention_purge(db):
    mid = await make_monitor(db)
    async with db.session_factory() as s:
        s.add(CheckResult(monitor_id=mid, ok=True, checked_at=utcnow() - timedelta(days=40)))
        s.add(CheckResult(monitor_id=mid, ok=True, checked_at=utcnow()))
        await s.commit()
    assert await purge_old_data(db, 30) == 1


@respx.mock
async def test_many_concurrent_checks_all_recorded(db, settings, caplog):
    """40 проверок завершаются одновременно и пишут в базу параллельно —
    ни одна запись не должна потеряться из-за блокировок (важно для SQLite)."""
    for _ in range(40):
        await make_monitor(db, failure_threshold=1)
    respx.get(URL).respond(500)
    async with httpx.AsyncClient() as client:
        scheduler = Scheduler(db, client, {"fake": FakeNotifier()}, settings)
        assert await scheduler.tick() == 40
        await scheduler.wait_idle()
    async with db.session_factory() as s:
        assert len((await s.scalars(select(CheckResult))).all()) == 40
        assert len((await s.scalars(select(Incident))).all()) == 40
    assert "check failed" not in caplog.text


@respx.mock
async def test_graceful_stop_finishes_fast_checks_and_cancels_stuck_ones(db, settings):
    never = asyncio.Event()

    async def stuck(request):
        await never.wait()

    stuck_id = await make_monitor(db, url="https://stuck.test/")
    fast_id = await make_monitor(db, url="https://fast.test/")
    respx.get("https://stuck.test/").mock(side_effect=stuck)
    respx.get("https://fast.test/").respond(200)
    settings.shutdown_grace_seconds = 0.2
    settings.claim_lease_seconds = 0

    async with httpx.AsyncClient() as client:
        scheduler = Scheduler(db, client, {}, settings)
        runner = asyncio.create_task(scheduler.run())
        await asyncio.sleep(0.05)
        scheduler.stop()
        await asyncio.wait_for(runner, timeout=3)  # не висим на зависшей проверке

    async with db.session_factory() as s:
        assert (await s.get(Monitor, fast_id)).last_checked_at is not None
        assert (await s.get(Monitor, stuck_id)).last_checked_at is None
    # прерванная проверка не потеряна: аренда истекла, монитор снова в очереди
    assert stuck_id in await claim_due_monitors(db, 10, 120)
