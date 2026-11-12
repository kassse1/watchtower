from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class MonitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: HttpUrl
    method: Literal["GET", "HEAD"] = "GET"
    interval_seconds: int = Field(60, ge=10, le=86400)
    timeout_seconds: float = Field(10.0, gt=0, le=60)
    expected_status: int = Field(200, ge=100, le=599)
    keyword: str | None = Field(None, min_length=1, max_length=200)
    failure_threshold: int = Field(3, ge=1, le=20)

    @model_validator(mode="after")
    def _head_has_no_body(self) -> Self:
        if self.method == "HEAD" and self.keyword:
            raise ValueError("keyword check requires GET: HEAD responses have no body")
        return self


class MonitorUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    interval_seconds: int | None = Field(None, ge=10, le=86400)
    timeout_seconds: float | None = Field(None, gt=0, le=60)
    expected_status: int | None = Field(None, ge=100, le=599)
    keyword: str | None = Field(None, min_length=1, max_length=200)
    failure_threshold: int | None = Field(None, ge=1, le=20)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _only_keyword_is_nullable(self) -> Self:
        # null означает «сбросить», а сбросить можно только keyword.
        for field in self.model_fields_set - {"keyword"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class MonitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    url: str
    method: str
    interval_seconds: int
    timeout_seconds: float
    expected_status: int
    keyword: str | None
    failure_threshold: int
    is_active: bool
    status: str
    consecutive_failures: int
    last_checked_at: datetime | None
    last_latency_ms: float | None
    tls_expires_at: datetime | None
    created_at: datetime


class RecentCheck(BaseModel):
    at: datetime
    ok: bool
    latency_ms: float | None


class MonitorSummary(MonitorOut):
    stats: dict[str, Any]
    recent: list[RecentCheck]


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    checked_at: datetime
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    monitor_id: int
    started_at: datetime
    resolved_at: datetime | None
    last_error: str | None


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    monitor_id: int
    channel: str
    kind: str
    status: str
    attempts: int
    created_at: datetime
    delivered_at: datetime | None
    last_error: str | None


class DayUptime(BaseModel):
    date: str
    uptime: float | None


class PublicMonitor(BaseModel):
    name: str
    status: str
    uptime_30d: float | None
    days: list[DayUptime]


class PublicIncident(BaseModel):
    monitor: str
    started_at: datetime
    resolved_at: datetime | None


class PublicStatus(BaseModel):
    overall: Literal["operational", "degraded", "outage", "unknown"]
    generated_at: datetime
    monitors: list[PublicMonitor]
    incidents: list[PublicIncident]
