from datetime import timedelta

import httpx
import respx
from sqlalchemy import select

from app.db import utcnow
from app.models import CheckResult, Incident, Monitor
from app.service import check_monitor, purge_old_results, run_due_checks
from tests.conftest import FakeNotifier

URL = "https://example.test/"


async def make_monitor(db, **kw) -> int:
    async with db.session_factory() as s:
        m = Monitor(name="site", url=URL, failure_threshold=2, interval_seconds=60, **kw)
        s.add(m)
        await s.commit()
        return m.id


@respx.mock
async def test_full_incident_lifecycle(db, settings):
    mid = await make_monitor(db)
    notifier = FakeNotifier()
    route = respx.get(URL)
    async with httpx.AsyncClient() as client:
        route.respond(500)
        await check_monitor(mid, db, client, [notifier], settings)
        assert notifier.events == []  # 1 сбой < порога

        await check_monitor(mid, db, client, [notifier], settings)
        assert [e.kind for e in notifier.events] == ["down"]

        await check_monitor(mid, db, client, [notifier], settings)
        assert len(notifier.events) == 1  # без спама пока лежит

        route.respond(200)
        await check_monitor(mid, db, client, [notifier], settings)
        assert [e.kind for e in notifier.events] == ["down", "recovered"]
        assert notifier.events[1].downtime_seconds is not None

    async with db.session_factory() as s:
        incidents = (await s.scalars(select(Incident))).all()
        assert len(incidents) == 1 and incidents[0].resolved_at is not None
        assert (await s.get(Monitor, mid)).status == "up"
        assert len((await s.scalars(select(CheckResult))).all()) == 4


@respx.mock
async def test_broken_notifier_does_not_break_checks(db, settings):
    class Boom:
        async def send(self, event):
            raise RuntimeError("telegram is down")

    mid = await make_monitor(db)
    respx.get(URL).respond(500)
    async with httpx.AsyncClient() as client:
        for _ in range(2):
            await check_monitor(mid, db, client, [Boom()], settings)
    async with db.session_factory() as s:
        assert (await s.get(Monitor, mid)).status == "down"


@respx.mock
async def test_private_target_blocked_at_check_time(db, settings):
    settings.allow_private_targets = False
    async with db.session_factory() as s:
        m = Monitor(name="evil", url="http://169.254.169.254/", failure_threshold=1)
        s.add(m)
        await s.commit()
        mid = m.id
    async with httpx.AsyncClient() as client:
        await check_monitor(mid, db, client, [], settings)
    async with db.session_factory() as s:
        r = (await s.scalars(select(CheckResult))).one()
        assert not r.ok and r.error.startswith("blocked")


@respx.mock
async def test_only_due_active_monitors_are_checked(db, settings):
    due = await make_monitor(db)
    later = await make_monitor(db)
    paused = await make_monitor(db, is_active=False)
    async with db.session_factory() as s:
        (await s.get(Monitor, later)).next_check_at = utcnow() + timedelta(hours=1)
        await s.commit()
    respx.get(URL).respond(200)
    async with httpx.AsyncClient() as client:
        assert await run_due_checks(db, client, [], settings) == 1
    async with db.session_factory() as s:
        assert (await s.get(Monitor, due)).last_checked_at is not None
        assert (await s.get(Monitor, later)).last_checked_at is None
        assert (await s.get(Monitor, paused)).last_checked_at is None


async def test_retention_purge(db):
    mid = await make_monitor(db)
    async with db.session_factory() as s:
        s.add(CheckResult(monitor_id=mid, ok=True, checked_at=utcnow() - timedelta(days=40)))
        s.add(CheckResult(monitor_id=mid, ok=True, checked_at=utcnow()))
        await s.commit()
    assert await purge_old_results(db, 30) == 1
