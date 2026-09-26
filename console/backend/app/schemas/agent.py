"""Protocollo agente ↔ console (v1). Da tenere allineato con agent/internal/client."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Platform = Literal["linux", "windows"]
AgentRole = Literal["qnap", "qnap_hero", "veeam_vbr", "veeam_agent", "system"]


class EnrollRequest(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    csr_pem: str = Field(max_length=8192)
    hostname: str = Field(max_length=255)
    platform: Platform
    arch: str = Field(max_length=32)
    agent_version: str = Field(max_length=64)
    roles: list[AgentRole] = []
    allow_shell: bool = False


class EnrollResponse(BaseModel):
    agent_id: uuid.UUID
    device_id: uuid.UUID
    status: str
    cert_pem: str
    ca_pem: str


class HeartbeatRequest(BaseModel):
    version: str = Field(max_length=64)
    roles: list[AgentRole] = []
    capabilities: dict[str, Any] = {}
    allow_shell: bool = False
    uptime_s: int | None = None


class HeartbeatResponse(BaseModel):
    status: str
    server_time: datetime
    # Placeholder per le fasi successive (config remota, aggiornamenti).
    config_version: int = 0
    desired_version: str | None = None


class Envelope[T](BaseModel):
    msg_id: uuid.UUID
    seq: int
    ts: datetime
    payload: T


class InventoryPayload(BaseModel):
    hostname: str | None = None
    model: str | None = None
    serial: str | None = None
    os_version: str | None = None
    # Tutto il resto (volumi, dischi, job Veeam...) finisce nello snapshot così com'è.
    data: dict[str, Any] = {}


class MetricIn(BaseModel):
    ts: datetime
    name: str = Field(max_length=100)
    labels: dict[str, str] = {}
    value: float


class MetricsPayload(BaseModel):
    metrics: list[MetricIn] = Field(max_length=5000)


class EventIn(BaseModel):
    ts: datetime
    type: str = Field(max_length=100)
    severity: Literal["info", "warning", "error", "critical"] = "info"
    data: dict[str, Any] = {}


class EventsPayload(BaseModel):
    events: list[EventIn] = Field(max_length=1000)


class IngestResponse(BaseModel):
    accepted: bool
    duplicate: bool = False
