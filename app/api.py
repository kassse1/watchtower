import secrets
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import utcnow
from app.metrics import render_metrics
from app.models import AlertDelivery, CheckResult, Incident, Monitor
from app.schemas import (
    AlertOut,
    CheckOut,
    DayUptime,
    IncidentOut,
    MonitorCreate,
    MonitorOut,
    MonitorSummary,
    MonitorUpdate,
    PublicIncident,
    PublicMonitor,
    PublicStatus,
    RecentCheck,
)
from app.security import UnsafeTargetError, resolve_public_ip
from app.stats import daily_uptime, monitor_stats, recent_checks


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.session_factory() as s:
        yield s


def require_api_key(
    x_api_key: str | None = Header(None), settings: Settings = Depends(get_settings)
) -> None:
    if settings.api_key and not secrets.compare_digest(
        (x_api_key or "").encode(), settings.api_key.encode()
    ):
        raise HTTPException(401, "invalid or missing API key")


router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)], tags=["monitors"])
public_router = APIRouter(tags=["public"])
ops_router = APIRouter(dependencies=[Depends(require_api_key)], tags=["ops"])


async def _get_or_404(session: AsyncSession, monitor_id: int) -> Monitor:
    m = await session.get(Monitor, monitor_id)
    if m is None:
        raise HTTPException(404, "monitor not found")
    return m


@router.post("/monitors", response_model=MonitorOut, status_code=201)
async def create_monitor(
    body: MonitorCreate,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Monitor:
    url = str(body.url)
    if not settings.allow_private_targets:
        try:
            await resolve_public_ip(url)
        except UnsafeTargetError as exc:
            raise HTTPException(422, str(exc)) from exc
    m = Monitor(**body.model_dump(exclude={"url"}), url=url, next_check_at=utcnow())
    session.add(m)
    await session.commit()
    return m


@router.get("/monitors", response_model=list[MonitorOut])
async def list_monitors(session: AsyncSession = Depends(get_session)) -> list[Monitor]:
    return list((await session.scalars(select(Monitor).order_by(Monitor.id))).all())


@router.get("/summary", response_model=list[MonitorSummary])
async def summary(session: AsyncSession = Depends(get_session)) -> list[MonitorSummary]:
    """Всё, что нужно дашборду, одним запросом вместо 1 + N."""
    monitors = (await session.scalars(select(Monitor).order_by(Monitor.id))).all()
    result = []
    for m in monitors:
        recent = [
            RecentCheck(at=at, ok=ok, latency_ms=lat)
            for at, ok, lat in await recent_checks(session, m.id, 40)
        ]
        base = MonitorOut.model_validate(m).model_dump()
        result.append(
            MonitorSummary(**base, stats=await monitor_stats(session, m.id), recent=recent)
        )
    return result


@router.get("/monitors/{monitor_id}", response_model=MonitorOut)
async def get_monitor(monitor_id: int, session: AsyncSession = Depends(get_session)) -> Monitor:
    return await _get_or_404(session, monitor_id)


@router.patch("/monitors/{monitor_id}", response_model=MonitorOut)
async def update_monitor(
    monitor_id: int, body: MonitorUpdate, session: AsyncSession = Depends(get_session)
) -> Monitor:
    m = await _get_or_404(session, monitor_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("keyword") and m.method == "HEAD":
        raise HTTPException(422, "keyword check requires GET: HEAD responses have no body")
    for field, value in changes.items():
        setattr(m, field, value)
    if changes.get("is_active"):
        m.next_check_at = utcnow()
    await session.commit()
    return m


@router.delete("/monitors/{monitor_id}", status_code=204)
async def delete_monitor(monitor_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    m = await _get_or_404(session, monitor_id)
    await session.delete(m)  # проверки, инциденты и алерты удалит ON DELETE CASCADE
    await session.commit()
    return Response(status_code=204)


@router.get("/monitors/{monitor_id}/checks", response_model=list[CheckOut])
async def list_checks(
    monitor_id: int,
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[CheckResult]:
    await _get_or_404(session, monitor_id)
    q = (
        select(CheckResult)
        .where(CheckResult.monitor_id == monitor_id)
        .order_by(CheckResult.checked_at.desc())
        .limit(limit)
    )
    return list((await session.scalars(q)).all())


@router.get("/monitors/{monitor_id}/stats")
async def get_stats(
    monitor_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    await _get_or_404(session, monitor_id)
    return await monitor_stats(session, monitor_id)


@router.get("/incidents", response_model=list[IncidentOut])
async def list_incidents(
    monitor_id: int | None = None,
    open_only: bool = False,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[Incident]:
    q = select(Incident).order_by(Incident.started_at.desc()).limit(limit)
    if monitor_id is not None:
        q = q.where(Incident.monitor_id == monitor_id)
    if open_only:
        q = q.where(Incident.resolved_at.is_(None))
    return list((await session.scalars(q)).all())


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    status: str | None = Query(None, pattern="^(pending|delivered|failed)$"),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[AlertDelivery]:
    """Журнал доставки уведомлений: видно, что дошло, что ждёт повтора, что потеряно."""
    q = select(AlertDelivery).order_by(AlertDelivery.id.desc()).limit(limit)
    if status:
        q = q.where(AlertDelivery.status == status)
    return list((await session.scalars(q)).all())


@ops_router.get("/metrics", response_class=PlainTextResponse)
async def metrics(session: AsyncSession = Depends(get_session)) -> str:
    monitors = (await session.scalars(select(Monitor).order_by(Monitor.id))).all()
    return render_metrics(monitors, utcnow())


@public_router.get("/api/public/status", response_model=PublicStatus)
async def public_status(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)
) -> PublicStatus:
    """Данные для публичной status-страницы. Намеренно без URL и текстов ошибок:
    они могут раскрывать внутреннее устройство инфраструктуры."""
    if not settings.public_status_page:
        raise HTTPException(404, "status page is disabled")
    monitors = (
        await session.scalars(select(Monitor).where(Monitor.is_active).order_by(Monitor.id))
    ).all()
    days = await daily_uptime(session, 30)
    out = []
    for m in monitors:
        stats = await monitor_stats(session, m.id)
        out.append(
            PublicMonitor(
                name=m.name,
                status=m.status,
                uptime_30d=stats["uptime_percent"]["30d"],
                days=[DayUptime(**d) for d in days[m.id]],
            )
        )
    names = {m.id: m.name for m in monitors}
    incidents = (
        await session.scalars(
            select(Incident)
            .where(Incident.monitor_id.in_(names))
            .order_by(Incident.started_at.desc())
            .limit(10)
        )
    ).all()

    down = sum(m.status == "down" for m in monitors)
    overall: Literal["operational", "degraded", "outage", "unknown"]
    if not monitors or all(m.status == "unknown" for m in monitors):
        overall = "unknown"
    elif down == 0:
        overall = "operational"
    elif down == len(monitors):
        overall = "outage"
    else:
        overall = "degraded"
    return PublicStatus(
        overall=overall,
        generated_at=utcnow(),
        monitors=out,
        incidents=[
            PublicIncident(
                monitor=names[i.monitor_id], started_at=i.started_at, resolved_at=i.resolved_at
            )
            for i in incidents
        ],
    )
