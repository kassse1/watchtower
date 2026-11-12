from datetime import timedelta

from sqlalchemy import func, select

from app.db import utcnow
from app.models import AlertDelivery, CheckResult, Incident, Monitor

BODY = {"name": "Site", "url": "https://example.com"}


async def create(api, **kw) -> int:
    r = await api.post("/api/monitors", json={**BODY, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def add_checks(db, mid, results):
    now = utcnow()
    async with db.session_factory() as s:
        for i, (ok, lat) in enumerate(results):
            s.add(
                CheckResult(
                    monitor_id=mid, ok=ok, latency_ms=lat, checked_at=now - timedelta(minutes=i)
                )
            )
        await s.commit()


async def test_crud(api):
    mid = await create(api)
    assert (await api.get(f"/api/monitors/{mid}")).json()["status"] == "unknown"
    assert len((await api.get("/api/monitors")).json()) == 1

    r = await api.patch(f"/api/monitors/{mid}", json={"interval_seconds": 120, "is_active": False})
    assert r.json()["interval_seconds"] == 120 and r.json()["is_active"] is False

    assert (await api.delete(f"/api/monitors/{mid}")).status_code == 204
    assert (await api.get(f"/api/monitors/{mid}")).status_code == 404
    assert (await api.delete(f"/api/monitors/{mid}")).status_code == 404


async def test_delete_cascades_to_history(api, db):
    mid = await create(api)
    await add_checks(db, mid, [(True, 1.0)])
    async with db.session_factory() as s:
        s.add(Incident(monitor_id=mid))
        s.add(AlertDelivery(monitor_id=mid, channel="x", kind="down", payload={}))
        await s.commit()
    await api.delete(f"/api/monitors/{mid}")
    async with db.session_factory() as s:
        for model in (CheckResult, Incident, AlertDelivery):
            assert await s.scalar(select(func.count()).select_from(model)) == 0


async def test_validation(api):
    for bad in (
        {"url": "not a url"},
        {"url": "ftp://x.com"},
        {"interval_seconds": 1},
        {"name": ""},
        {"method": "POST"},
        {"method": "HEAD", "keyword": "x"},
    ):
        r = await api.post("/api/monitors", json={**BODY, **bad})
        assert r.status_code == 422, bad


async def test_patch_semantics(api):
    mid = await create(api, keyword="hello")
    r = await api.patch(f"/api/monitors/{mid}", json={"keyword": None})
    assert r.status_code == 200 and r.json()["keyword"] is None  # null сбрасывает keyword
    # …но обязательное поле обнулить нельзя (раньше это давало 500 от базы)
    assert (await api.patch(f"/api/monitors/{mid}", json={"name": None})).status_code == 422
    assert (await api.get(f"/api/monitors/{mid}")).json()["name"] == "Site"


async def test_resume_schedules_immediate_check(api, db):
    mid = await create(api)
    async with db.session_factory() as s:
        (await s.get(Monitor, mid)).next_check_at = utcnow() + timedelta(hours=1)
        await s.commit()
    await api.patch(f"/api/monitors/{mid}", json={"is_active": True})
    async with db.session_factory() as s:
        assert (await s.get(Monitor, mid)).next_check_at <= utcnow()


async def test_ssrf_rejected_on_create(api, settings):
    settings.allow_private_targets = False
    r = await api.post("/api/monitors", json={**BODY, "url": "http://169.254.169.254/"})
    assert r.status_code == 422 and "non-public" in r.json()["detail"]


async def test_api_key_enforced(api, settings):
    settings.api_key = "s3cret"
    for path in ("/api/monitors", "/api/summary", "/api/alerts", "/metrics"):
        assert (await api.get(path)).status_code == 401, path
        assert (await api.get(path, headers={"X-API-Key": "wrong"})).status_code == 401
        assert (await api.get(path, headers={"X-API-Key": "s3cret"})).status_code == 200
    # без ключа доступны только health и публичный статус
    assert (await api.get("/health")).status_code == 200
    assert (await api.get("/api/public/status")).status_code == 200


async def test_stats_and_checks(api, db):
    mid = await create(api)
    await add_checks(db, mid, [(True, 100.0), (True, 200.0), (True, 300.0), (False, None)])
    st = (await api.get(f"/api/monitors/{mid}/stats")).json()
    assert st["uptime_percent"] == {"24h": 75.0, "7d": 75.0, "30d": 75.0}
    assert st["latency_ms_24h"] == {"avg": 200.0, "p95": 300.0}
    assert len((await api.get(f"/api/monitors/{mid}/checks?limit=2")).json()) == 2


async def test_stats_windows_differ(api, db):
    mid = await create(api)
    async with db.session_factory() as s:
        s.add(CheckResult(monitor_id=mid, ok=True, checked_at=utcnow()))
        s.add(CheckResult(monitor_id=mid, ok=False, checked_at=utcnow() - timedelta(days=3)))
        await s.commit()
    up = (await api.get(f"/api/monitors/{mid}/stats")).json()["uptime_percent"]
    assert up == {"24h": 100.0, "7d": 50.0, "30d": 50.0}


async def test_empty_stats(api):
    mid = await create(api)
    st = (await api.get(f"/api/monitors/{mid}/stats")).json()
    assert st["uptime_percent"]["24h"] is None and st["latency_ms_24h"]["p95"] is None


async def test_summary(api, db):
    mid = await create(api)
    await add_checks(db, mid, [(True, 50.0), (False, None)])
    (item,) = (await api.get("/api/summary")).json()
    assert item["id"] == mid and item["stats"]["uptime_percent"]["24h"] == 50.0
    assert [c["ok"] for c in item["recent"]] == [False, True]  # от старых к новым


async def test_public_status_hides_internals(api, db):
    mid = await create(api, name="Shop", url="https://internal-admin.example.com/secret")
    await create(api, name="Paused")
    await api.patch("/api/monitors/2", json={"is_active": False})
    await add_checks(db, mid, [(True, 10.0)])
    async with db.session_factory() as s:
        (await s.get(Monitor, mid)).status = "down"
        s.add(Incident(monitor_id=mid, last_error="connect to 10.0.0.5 refused"))
        await s.commit()

    r = await api.get("/api/public/status")
    data = r.json()
    assert data["overall"] == "outage"
    assert [m["name"] for m in data["monitors"]] == ["Shop"]  # без приостановленных
    assert len(data["monitors"][0]["days"]) == 30
    assert data["monitors"][0]["days"][-1]["uptime"] == 100.0
    assert data["incidents"][0]["monitor"] == "Shop"
    assert "internal-admin" not in r.text and "10.0.0.5" not in r.text


async def test_public_status_overall_states(api, db):
    assert (await api.get("/api/public/status")).json()["overall"] == "unknown"
    a, b = await create(api), await create(api)

    async def set_status(**statuses):
        async with db.session_factory() as s:
            for mid, st in zip((a, b), statuses.values(), strict=True):
                (await s.get(Monitor, mid)).status = st
            await s.commit()
        return (await api.get("/api/public/status")).json()["overall"]

    assert await set_status(a="up", b="up") == "operational"
    assert await set_status(a="up", b="down") == "degraded"
    assert await set_status(a="down", b="down") == "outage"


async def test_public_status_can_be_disabled(api, settings):
    settings.public_status_page = False
    assert (await api.get("/api/public/status")).status_code == 404


async def test_metrics(api, db):
    mid = await create(api, name='My "site"')
    async with db.session_factory() as s:
        m = await s.get(Monitor, mid)
        m.status, m.last_latency_ms = "up", 42.5
        m.tls_expires_at = utcnow() + timedelta(days=30)
        await s.commit()
    text = (await api.get("/metrics")).text
    assert f'watchtower_monitor_up{{monitor_id="{mid}",name="My \\"site\\""}} 1' in text
    assert "watchtower_monitor_latency_ms" in text and "42.5" in text
    assert "watchtower_monitor_tls_days_left" in text
    assert "# TYPE watchtower_monitor_up gauge" in text


async def test_alert_log(api, db):
    mid = await create(api)
    async with db.session_factory() as s:
        s.add(AlertDelivery(monitor_id=mid, channel="telegram", kind="down", payload={"a": 1}))
        await s.commit()
    (row,) = (await api.get("/api/alerts?status=pending")).json()
    assert row["channel"] == "telegram" and "payload" not in row
    assert (await api.get("/api/alerts?status=delivered")).json() == []
    assert (await api.get("/api/alerts?status=bogus")).status_code == 422


async def test_pages_and_health(api):
    assert "Watchtower" in (await api.get("/")).text
    assert "Статус сервисов" in (await api.get("/status")).text
    assert (await api.get("/health")).json() == {"status": "ok"}
