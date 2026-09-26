import secrets
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.agent.routes import client_ip, hash_token
from app.auth import CurrentUserDep
from app.config import settings
from app.db import get_session
from app.models import (
    Agent,
    AgentStatus,
    Device,
    EnrollmentToken,
    InventorySnapshot,
    Problem,
    ProblemState,
    RoleAssignment,
    Severity,
    Site,
    Tenant,
    User,
)
from app.models.base import utcnow
from app.schemas.ui import (
    DeviceDetail,
    DeviceOut,
    MeOut,
    ProblemOut,
    ProblemRow,
    SiteCreate,
    SiteOut,
    TenantCreate,
    TenantOut,
    TokenCreate,
    TokenOut,
    UserCreate,
)
from app.services.audit import audit

router = APIRouter(prefix="/api/v1", tags=["console"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _scoped(stmt, column, user):
    scope = user.tenant_scope()
    return stmt if scope is None else stmt.where(column.in_(scope))


# --- utenti ---------------------------------------------------------------


@router.get("/me", response_model=MeOut)
async def me(user: CurrentUserDep):
    return user.user


@router.post("/users", response_model=MeOut, status_code=201)
async def create_user(body: UserCreate, user: CurrentUserDep, session: SessionDep, request: Request):
    user.require_superadmin()
    new = User(email=body.email.lower(), display_name=body.display_name)
    new.roles = [RoleAssignment(role=r.role, tenant_id=r.tenant_id) for r in body.roles]
    session.add(new)
    try:
        await session.flush()
    except IntegrityError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "utente già esistente") from e
    audit(session, "user.create", user_id=user.id, target=new.email, ip=client_ip(request),
          roles=[r.model_dump(mode="json") for r in body.roles])
    await session.commit()
    return new


# --- tenant e siti --------------------------------------------------------


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(user: CurrentUserDep, session: SessionDep):
    return (await session.scalars(_scoped(select(Tenant), Tenant.id, user).order_by(Tenant.name))).all()


@router.post("/tenants", response_model=TenantOut, status_code=201)
async def create_tenant(body: TenantCreate, user: CurrentUserDep, session: SessionDep, request: Request):
    user.require_superadmin()
    tenant = Tenant(name=body.name, slug=body.slug)
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "tenant già esistente") from e
    audit(session, "tenant.create", user_id=user.id, tenant_id=tenant.id, target=tenant.slug, ip=client_ip(request))
    await session.commit()
    return tenant


@router.get("/tenants/{tenant_id}/sites", response_model=list[SiteOut])
async def list_sites(tenant_id: uuid.UUID, user: CurrentUserDep, session: SessionDep):
    user.require_read(tenant_id)
    return (await session.scalars(select(Site).where(Site.tenant_id == tenant_id).order_by(Site.name))).all()


@router.post("/tenants/{tenant_id}/sites", response_model=SiteOut, status_code=201)
async def create_site(tenant_id: uuid.UUID, body: SiteCreate, user: CurrentUserDep, session: SessionDep):
    user.require_write(tenant_id)
    if await session.get(Tenant, tenant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    site = Site(tenant_id=tenant_id, name=body.name)
    session.add(site)
    try:
        await session.commit()
    except IntegrityError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "sito già esistente") from e
    return site


@router.post("/sites/{site_id}/enrollment-tokens", response_model=TokenOut, status_code=201)
async def create_enrollment_token(
    site_id: uuid.UUID, body: TokenCreate, user: CurrentUserDep, session: SessionDep, request: Request
):
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_write(site.tenant_id)
    plain = secrets.token_urlsafe(32)
    token = EnrollmentToken(
        tenant_id=site.tenant_id,
        site_id=site.id,
        token_hash=hash_token(plain),
        note=body.note,
        expires_at=utcnow() + timedelta(hours=settings.enroll_token_ttl_hours),
        created_by=user.id,
    )
    session.add(token)
    audit(session, "enrollment_token.create", user_id=user.id, tenant_id=site.tenant_id,
          target=str(site.id), ip=client_ip(request), note=body.note)
    await session.commit()
    return TokenOut(token=plain, expires_at=token.expires_at)


# --- dispositivi e agenti -------------------------------------------------


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(
    user: CurrentUserDep, session: SessionDep, tenant_id: uuid.UUID | None = None, kind: str | None = None
):
    stmt = _scoped(select(Device), Device.tenant_id, user).options(selectinload(Device.agent))
    if tenant_id:
        stmt = stmt.where(Device.tenant_id == tenant_id)
    if kind:
        stmt = stmt.where(Device.kind == kind)
    return (await session.scalars(stmt.order_by(Device.name))).all()


@router.get("/devices/{device_id}", response_model=DeviceDetail)
async def get_device(device_id: uuid.UUID, user: CurrentUserDep, session: SessionDep):
    device = await session.get(Device, device_id, options=[selectinload(Device.agent)])
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_read(device.tenant_id)
    snap = await session.scalar(
        select(InventorySnapshot)
        .where(InventorySnapshot.device_id == device_id)
        .order_by(InventorySnapshot.collected_at.desc())
        .limit(1)
    )
    out = DeviceDetail.model_validate(device)
    if snap:
        out.inventory, out.inventory_at = snap.data, snap.collected_at
    return out


async def _set_agent_status(
    agent_id: uuid.UUID, new_status: AgentStatus, user, session: AsyncSession, request: Request
) -> Agent:
    agent = await session.get(Agent, agent_id, options=[selectinload(Agent.device)])
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_write(agent.device.tenant_id)
    if agent.status == AgentStatus.REVOKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "agente revocato: serve un nuovo enrollment")
    agent.status = new_status
    if new_status == AgentStatus.APPROVED:
        agent.approved_at = utcnow()
    audit(session, f"agent.{'approve' if new_status == AgentStatus.APPROVED else 'revoke'}",
          user_id=user.id, agent_id=agent.id, tenant_id=agent.device.tenant_id,
          target=agent.device.name, ip=client_ip(request))
    await session.commit()
    return agent


@router.post("/agents/{agent_id}/approve", status_code=204)
async def approve_agent(agent_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, request: Request):
    await _set_agent_status(agent_id, AgentStatus.APPROVED, user, session, request)


@router.post("/agents/{agent_id}/revoke", status_code=204)
async def revoke_agent(agent_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, request: Request):
    await _set_agent_status(agent_id, AgentStatus.REVOKED, user, session, request)


# --- problemi (vista unica trasversale ai tenant) -------------------------


@router.get("/problems", response_model=list[ProblemRow])
async def list_problems(
    user: CurrentUserDep,
    session: SessionDep,
    tenant_id: uuid.UUID | None = None,
    state: Annotated[list[ProblemState] | None, Query()] = None,
    severity: Severity | None = None,
    limit: int = Query(200, le=1000),
):
    states = state or [ProblemState.OPEN, ProblemState.ACKNOWLEDGED]
    sev_order = case((Problem.severity == Severity.CRITICAL, 0), else_=1)
    stmt = (
        _scoped(select(Problem, Tenant.name, Device.name), Problem.tenant_id, user)
        .join(Tenant, Tenant.id == Problem.tenant_id)
        .join(Device, Device.id == Problem.device_id)
        .where(Problem.state.in_(states))
        .order_by(sev_order, Problem.last_seen.desc())
        .limit(limit)
    )
    if tenant_id:
        stmt = stmt.where(Problem.tenant_id == tenant_id)
    if severity:
        stmt = stmt.where(Problem.severity == severity)
    rows = (await session.execute(stmt)).all()
    return [
        ProblemRow(**ProblemOut.model_validate(p).model_dump(), tenant_name=tn, device_name=dn)
        for p, tn, dn in rows
    ]


@router.get("/problems/summary")
async def problems_summary(user: CurrentUserDep, session: SessionDep):
    """Conteggio problemi aperti per tenant e gravità, per la dashboard."""
    stmt = (
        _scoped(select(Tenant.id, Tenant.name, Problem.severity, func.count()), Problem.tenant_id, user)
        .join(Tenant, Tenant.id == Problem.tenant_id)
        .where(Problem.state != ProblemState.RESOLVED)
        .group_by(Tenant.id, Tenant.name, Problem.severity)
    )
    out: dict[str, dict] = {}
    for tid, name, sev, n in (await session.execute(stmt)).all():
        row = out.setdefault(str(tid), {"tenant_id": tid, "tenant_name": name, "warning": 0, "critical": 0})
        row[sev] = n
    return sorted(out.values(), key=lambda r: (-r["critical"], -r["warning"], r["tenant_name"]))


@router.post("/problems/{problem_id}/ack", response_model=ProblemOut)
async def ack_problem(problem_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, request: Request):
    problem = await session.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_write(problem.tenant_id)
    if problem.state == ProblemState.RESOLVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "problema già risolto")
    problem.state = ProblemState.ACKNOWLEDGED
    problem.acked_by, problem.acked_at = user.id, utcnow()
    audit(session, "problem.ack", user_id=user.id, tenant_id=problem.tenant_id,
          target=str(problem.id), ip=client_ip(request))
    await session.commit()
    return problem
