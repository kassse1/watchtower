from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class MonitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: HttpUrl
    method: Literal["GET", "HEAD"] = "GET"
    interval_seconds: int = Field(60, ge=10, le=86400)
    timeout_seconds: float = Field(10.0, gt=0, le=60)
    expected_status: int = Field(200, ge=100, le=599)
    keyword: str | None = Field(None, max_length=200)
    failure_threshold: int = Field(3, ge=1, le=20)


class MonitorUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    interval_seconds: int | None = Field(None, ge=10, le=86400)
    timeout_seconds: float | None = Field(None, gt=0, le=60)
    expected_status: int | None = Field(None, ge=100, le=599)
    keyword: str | None = Field(None, max_length=200)
    failure_threshold: int | None = Field(None, ge=1, le=20)
    is_active: bool | None = None


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
    created_at: datetime


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
