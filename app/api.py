import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import utcnow
from app.models import CheckResult, Incident, Monitor
from app.schemas import (
    CheckOut,
    IncidentOut,
    MonitorCreate,
    MonitorOut,
    MonitorUpdate,
)
from app.security import UnsafeTargetError, assert_public_url
from app.stats import monitor_stats


async def get_session(request: Request):
    async with request.app.state.db.session_factory() as s:
        yield s


def require_api_key(
    x_api_key: str | None = Header(None), settings: Settings = Depends(get_settings)
) -> None:
    if settings.api_key and not secrets.compare_digest(x_api_key or "", settings.api_key):
        raise HTTPException(401, "invalid or missing API key")


router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])


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
):
    url = str(body.url)
    if not settings.allow_private_targets:
        try:
            await assert_public_url(url)
        except UnsafeTargetError as exc:
            raise HTTPException(422, str(exc)) from exc
    m = Monitor(**body.model_dump(exclude={"url"}), url=url, next_check_at=utcnow())
    session.add(m)
    await session.commit()
    return m


@router.get("/monitors", response_model=list[MonitorOut])
async def list_monitors(session: AsyncSession = Depends(get_session)):
    return (await session.scalars(select(Monitor).order_by(Monitor.id))).all()


@router.get("/monitors/{monitor_id}", response_model=MonitorOut)
async def get_monitor(monitor_id: int, session: AsyncSession = Depends(get_session)):
    return await _get_or_404(session, monitor_id)


@router.patch("/monitors/{monitor_id}", response_model=MonitorOut)
async def update_monitor(
    monitor_id: int, body: MonitorUpdate, session: AsyncSession = Depends(get_session)
):
    m = await _get_or_404(session, monitor_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(m, field, value)
    if body.is_active:
        m.next_check_at = utcnow()
    await session.commit()
    return m


@router.delete("/monitors/{monitor_id}", status_code=204)
async def delete_monitor(monitor_id: int, session: AsyncSession = Depends(get_session)):
    m = await _get_or_404(session, monitor_id)
    await session.execute(delete(CheckResult).where(CheckResult.monitor_id == m.id))
    await session.execute(delete(Incident).where(Incident.monitor_id == m.id))
    await session.delete(m)
    await session.commit()
    return Response(status_code=204)


@router.get("/monitors/{monitor_id}/checks", response_model=list[CheckOut])
async def list_checks(
    monitor_id: int,
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    await _get_or_404(session, monitor_id)
    q = (
        select(CheckResult)
        .where(CheckResult.monitor_id == monitor_id)
        .order_by(CheckResult.checked_at.desc())
        .limit(limit)
    )
    return (await session.scalars(q)).all()


@router.get("/monitors/{monitor_id}/stats")
async def get_stats(monitor_id: int, session: AsyncSession = Depends(get_session)):
    await _get_or_404(session, monitor_id)
    return await monitor_stats(session, monitor_id)


@router.get("/incidents", response_model=list[IncidentOut])
async def list_incidents(
    monitor_id: int | None = None,
    open_only: bool = False,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    q = select(Incident).order_by(Incident.started_at.desc()).limit(limit)
    if monitor_id is not None:
        q = q.where(Incident.monitor_id == monitor_id)
    if open_only:
        q = q.where(Incident.resolved_at.is_(None))
    return (await session.scalars(q)).all()
