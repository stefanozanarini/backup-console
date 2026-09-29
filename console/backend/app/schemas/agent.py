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
    # Esecuzione remota: abilitata di default, disattivabile solo in installazione.
    allow_shell: bool = True


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


# --- Veeam -----------------------------------------------------------------


class VeeamJobIn(BaseModel):
    id: str = Field(max_length=200)
    name: str = Field(max_length=300)
    type: str | None = Field(default=None, max_length=100)
    enabled: bool = True
    last_result: str | None = Field(default=None, max_length=32)  # Success / Warning / Failed / None
    last_state: str | None = Field(default=None, max_length=32)
    last_start: datetime | None = None
    last_end: datetime | None = None
    last_success: datetime | None = None
    transferred_bytes: int | None = None
    next_run: datetime | None = None
    details: dict[str, Any] = {}


class VeeamRepositoryIn(BaseModel):
    name: str = Field(max_length=300)
    type: str | None = Field(default=None, max_length=100)
    total_bytes: int | None = None
    free_bytes: int | None = None


class VeeamLicenseIn(BaseModel):
    edition: str | None = None
    status: str | None = None
    expiration: datetime | None = None
    support_expiration: datetime | None = None


class VeeamPayload(BaseModel):
    product: Literal["vbr", "vaw"]
    version: str | None = Field(default=None, max_length=64)
    jobs: list[VeeamJobIn] = Field(default=[], max_length=2000)
    repositories: list[VeeamRepositoryIn] = Field(default=[], max_length=500)
    license: VeeamLicenseIn | None = None
    errors: list[str] = Field(default=[], max_length=50)  # errori non fatali del collector


# --- comandi e aggiornamenti ------------------------------------------------


class AgentCommand(BaseModel):
    id: uuid.UUID
    type: str
    timeout_s: int
    payload: dict[str, Any]


class CommandOutputIn(BaseModel):
    data: str = Field(max_length=200_000)


class CommandResultIn(BaseModel):
    status: Literal["succeeded", "failed"]
    exit_code: int | None = None
    error: str | None = Field(default=None, max_length=1000)
    result: dict[str, Any] | None = None
