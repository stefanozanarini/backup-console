import uuid
from datetime import datetime
from typing import Any

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


class TokenOut(BaseModel):
    token: str  # mostrato una sola volta
    expires_at: datetime


class AgentOut(ORM):
    id: uuid.UUID
    status: str
    version: str | None
    allow_shell: bool
    last_seen_at: datetime | None
    last_ip: str | None
    cert_not_after: datetime


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
