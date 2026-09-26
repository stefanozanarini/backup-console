import uuid
from datetime import datetime
from typing import Any

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import Role


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RoleOut(ORM):
    role: str
    tenant_id: uuid.UUID | None


class MeOut(ORM):
    id: uuid.UUID
    email: str
    display_name: str | None
    roles: list[RoleOut]
    totp_enabled: bool = False


class RoleIn(BaseModel):
    role: Role
    tenant_id: uuid.UUID | None = None


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str | None = None
    roles: list[RoleIn] = Field(min_length=1)


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")


class TenantOut(ORM):
    id: uuid.UUID
    name: str
    slug: str


class SiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SiteOut(ORM):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str


class TokenCreate(BaseModel):
    note: str | None = Field(default=None, max_length=200)
    # 1 = singolo dispositivo; >1 per rollout via GPO/Intune/script.
    max_uses: int = Field(1, ge=1, le=500)
    valid_hours: int | None = Field(default=None, ge=1, le=24 * 14)


class TokenOut(BaseModel):
    id: uuid.UUID
    token: str  # mostrato una sola volta
    expires_at: datetime
    max_uses: int


class TokenInfo(ORM):
    id: uuid.UUID
    site_id: uuid.UUID
    note: str | None
    uses: int
    max_uses: int
    expires_at: datetime
    used_at: datetime | None
    created_at: datetime


class BulkApprove(BaseModel):
    agent_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class AgentOut(ORM):
    id: uuid.UUID
    status: str
    version: str | None
    allow_shell: bool
    last_seen_at: datetime | None
    last_ip: str | None
    cert_not_after: datetime
    enrolled_at: datetime
    approved_at: datetime | None
    enrollment_note: str | None = None


class DeviceOut(ORM):
    id: uuid.UUID
    tenant_id: uuid.UUID
    site_id: uuid.UUID
    kind: str
    name: str
    hostname: str
    platform: str
    arch: str
    roles: list[Any]
    model: str | None
    serial: str | None
    os_version: str | None
    last_seen_at: datetime | None
    agent: AgentOut | None


class DeviceDetail(DeviceOut):
    veeam: dict[str, Any] = {}
    inventory: dict[str, Any] | None = None
    inventory_at: datetime | None = None


class ProblemOut(ORM):
    id: uuid.UUID
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    check: str
    severity: str
    state: str
    title: str
    details: dict[str, Any]
    first_seen: datetime
    last_seen: datetime
    resolved_at: datetime | None
    acked_at: datetime | None


class ProblemRow(ProblemOut):
    tenant_name: str
    device_name: str


# --- sicurezza --------------------------------------------------------------


class TotpSetupOut(BaseModel):
    secret: str
    uri: str


class TotpCode(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class ElevateIn(TotpCode):
    reason: str = Field(min_length=3, max_length=300)


class ElevationOut(BaseModel):
    token: str
    expires_at: datetime


# --- comandi ----------------------------------------------------------------


class CommandCreate(BaseModel):
    command: str = Field(min_length=1, max_length=20_000)
    # default: sh su Linux/QNAP, PowerShell su Windows
    shell: Literal["default", "sh", "bash", "powershell", "pwsh", "cmd"] = "default"
    timeout_s: int = Field(300, ge=5, le=3600)


class CommandOut(ORM):
    id: uuid.UUID
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    type: str
    payload: dict[str, Any]
    status: str
    timeout_s: int
    reason: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    dispatched_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    error: str | None
    output_truncated: bool


class CommandDetail(CommandOut):
    output: str


# --- rilasci e aggiornamenti --------------------------------------------------


class ReleaseAssetOut(ORM):
    platform: str
    sha256: str
    size: int


class ReleaseOut(BaseModel):
    version: str
    notes: str | None
    created_at: datetime
    assets: list[ReleaseAssetOut]
    agents: int  # agenti attualmente su questa versione


class UpgradeRequest(BaseModel):
    version: str
    device_ids: list[uuid.UUID] | None = None  # None = tutti i dispositivi nel perimetro
    tenant_id: uuid.UUID | None = None


class UpgradeResult(BaseModel):
    queued: int
    skipped: dict[str, int]


# --- Veeam ------------------------------------------------------------------


class VeeamJobOut(ORM):
    id: uuid.UUID
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    product: str
    name: str
    job_type: str | None
    enabled: bool
    last_result: str | None
    last_state: str | None
    last_start: datetime | None
    last_end: datetime | None
    last_success: datetime | None
    transferred_bytes: int | None
    next_run: datetime | None
    updated_at: datetime


class VeeamJobRow(VeeamJobOut):
    device_name: str
    tenant_name: str
