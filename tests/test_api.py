from datetime import timedelta

from app.db import utcnow
from app.models import CheckResult

BODY = {"name": "Site", "url": "https://example.com"}


async def test_crud(api):
    r = await api.post("/api/monitors", json=BODY)
    assert r.status_code == 201
    mid = r.json()["id"]
    assert r.json()["status"] == "unknown"

    assert len((await api.get("/api/monitors")).json()) == 1

    r = await api.patch(f"/api/monitors/{mid}", json={"interval_seconds": 120, "is_active": False})
    assert r.json()["interval_seconds"] == 120 and r.json()["is_active"] is False

    assert (await api.delete(f"/api/monitors/{mid}")).status_code == 204
    assert (await api.get(f"/api/monitors/{mid}")).status_code == 404


async def test_validation(api):
    assert (await api.post("/api/monitors", json={**BODY, "url": "not a url"})).status_code == 422
    assert (
        await api.post("/api/monitors", json={**BODY, "interval_seconds": 1})
    ).status_code == 422
    assert (await api.post("/api/monitors", json={**BODY, "url": "ftp://x.com"})).status_code == 422


async def test_ssrf_rejected_on_create(api, settings):
    settings.allow_private_targets = False
    r = await api.post("/api/monitors", json={**BODY, "url": "http://169.254.169.254/"})
    assert r.status_code == 422 and "non-public" in r.json()["detail"]


async def test_api_key_enforced(api, settings):
    settings.api_key = "s3cret"
    assert (await api.get("/api/monitors")).status_code == 401
    assert (await api.get("/api/monitors", headers={"X-API-Key": "wrong"})).status_code == 401
    assert (await api.get("/api/monitors", headers={"X-API-Key": "s3cret"})).status_code == 200
    assert (await api.get("/health")).status_code == 200  # health без ключа


async def test_stats(api, db):
    mid = (await api.post("/api/monitors", json=BODY)).json()["id"]
    now = utcnow()
    async with db.session_factory() as s:
        for i, (ok, lat) in enumerate([(True, 100.0), (True, 200.0), (True, 300.0), (False, None)]):
            s.add(
                CheckResult(
                    monitor_id=mid, ok=ok, latency_ms=lat, checked_at=now - timedelta(minutes=i)
                )
            )
        await s.commit()
    st = (await api.get(f"/api/monitors/{mid}/stats")).json()
    assert st["uptime_percent"]["24h"] == 75.0
    assert st["latency_ms_24h"]["avg"] == 200.0
    assert st["latency_ms_24h"]["p95"] == 300.0

    checks = (await api.get(f"/api/monitors/{mid}/checks?limit=2")).json()
    assert len(checks) == 2


async def test_dashboard_served(api):
    r = await api.get("/")
    assert r.status_code == 200 and "Watchtower" in r.text
