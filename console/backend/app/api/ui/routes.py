import secrets
import uuid
from datetime import timedelta
from typing import Any
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
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
    Metric,
    Problem,
    ProblemState,
    RoleAssignment,
    Severity,
    Site,
    Tenant,
    User,
    token_usable,
)
from app.models.base import utcnow
from app.schemas.ui import (
    BackupWindowIn,
    BackupWindowState,
    DeviceDetail,
    DeviceOut,
    MeOut,
    ProblemOut,
    ProblemRow,
    SiteCreate,
    SiteOut,
    TenantCreate,
    TenantOut,
    BulkApprove,
    TokenCreate,
    TokenInfo,
    TokenOut,
    UserCreate,
)
from app.services import backup_window
from app.services.audit import audit

router = APIRouter(prefix="/api/v1", tags=["console"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _scoped(stmt, column, user):
    scope = user.tenant_scope()
    return stmt if scope is None else stmt.where(column.in_(scope))


# --- configurazione pubblica per il frontend -----------------------------


@router.get("/auth-config")
async def auth_config() -> dict[str, Any]:
    """Senza autenticazione: serve al frontend per sapere come fare login."""
    if settings.auth_mode == "dev":
        return {"mode": "dev", "agents_url": settings.agents_url}
    return {
        "mode": "oidc",
        "local_login": settings.local_login,
        "agents_url": settings.agents_url,
        "client_id": settings.oidc_spa_client_id,
        "authority": settings.oidc_authority,
        "scopes": [settings.oidc_scope] if settings.oidc_scope else [],
    }


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
        max_uses=body.max_uses,
        expires_at=utcnow() + timedelta(hours=body.valid_hours or settings.enroll_token_ttl_hours),
        created_by=user.id,
    )
    session.add(token)
    await session.flush()
    audit(session, "enrollment_token.create", user_id=user.id, tenant_id=site.tenant_id,
          target=str(site.id), ip=client_ip(request), note=body.note, max_uses=body.max_uses)
    await session.commit()
    return TokenOut(id=token.id, token=plain, expires_at=token.expires_at, max_uses=token.max_uses)


@router.get("/sites/{site_id}/enrollment-tokens", response_model=list[TokenInfo])
async def list_enrollment_tokens(site_id: uuid.UUID, user: CurrentUserDep, session: SessionDep):
    """Token ancora utilizzabili del sito (non scaduti, non esauriti, non revocati)."""
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_read(site.tenant_id)
    now = utcnow()
    rows = await session.scalars(
        select(EnrollmentToken)
        .where(EnrollmentToken.site_id == site_id, EnrollmentToken.expires_at > now)
        .order_by(EnrollmentToken.created_at.desc())
    )
    return [t for t in rows if token_usable(t, now)]


@router.post("/enrollment-tokens/{token_id}/revoke", status_code=204)
async def revoke_enrollment_token(token_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, request: Request):
    token = await session.get(EnrollmentToken, token_id)
    if token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_write(token.tenant_id)
    token.revoked_at = utcnow()
    audit(session, "enrollment_token.revoke", user_id=user.id, tenant_id=token.tenant_id,
          target=str(token.id), ip=client_ip(request), note=token.note, uses=token.uses)
    await session.commit()


# --- dispositivi e agenti -------------------------------------------------


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(
    user: CurrentUserDep,
    session: SessionDep,
    tenant_id: uuid.UUID | None = None,
    kind: str | None = None,
    agent_status: AgentStatus | None = None,
):
    stmt = _scoped(select(Device), Device.tenant_id, user).options(selectinload(Device.agent))
    if tenant_id:
        stmt = stmt.where(Device.tenant_id == tenant_id)
    if kind:
        stmt = stmt.where(Device.kind == kind)
    if agent_status:
        stmt = stmt.join(Agent, Agent.device_id == Device.id).where(Agent.status == agent_status)
    devices = (await session.scalars(stmt.order_by(Device.name))).all()
    site_ids = {d.site_id for d in devices}
    names = dict((await session.execute(select(Site.id, Site.name).where(Site.id.in_(site_ids)))).all()) if site_ids else {}
    return [DeviceOut.model_validate(d).model_copy(update={"site_name": names.get(d.site_id)}) for d in devices]


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


@router.get("/devices/{device_id}/metrics")
async def device_metrics(
    device_id: uuid.UUID,
    name: str,
    user: CurrentUserDep,
    session: SessionDep,
    hours: int = Query(24 * 7, ge=1, le=24 * 90),
):
    """Serie storica di una metrica, media oraria, raggruppata per label (es. per volume)."""
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_read(device.tenant_id)
    bucket = func.date_trunc("hour", Metric.ts).label("bucket")
    rows = (
        await session.execute(
            select(Metric.labels, bucket, func.avg(Metric.value))
            .where(
                Metric.device_id == device_id,
                Metric.name == name,
                Metric.ts >= utcnow() - timedelta(hours=hours),
            )
            .group_by(Metric.labels, bucket)
            .order_by(bucket)
        )
    ).all()
    series: dict[str, dict[str, Any]] = {}
    for labels, ts, value in rows:
        key = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        series.setdefault(key, {"labels": labels, "points": []})["points"].append([ts, float(value)])
    return list(series.values())


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


@router.post("/agents/approve")
async def approve_agents(body: BulkApprove, user: CurrentUserDep, session: SessionDep, request: Request):
    """Approvazione in blocco (es. dopo un rollout con token multiuso)."""
    agents = (
        await session.scalars(
            select(Agent).where(Agent.id.in_(body.agent_ids)).options(selectinload(Agent.device))
        )
    ).all()
    approved = 0
    for agent in agents:
        if agent.status != AgentStatus.PENDING or not user.can_write(agent.device.tenant_id):
            continue
        agent.status, agent.approved_at = AgentStatus.APPROVED, utcnow()
        audit(session, "agent.approve", user_id=user.id, agent_id=agent.id, tenant_id=agent.device.tenant_id,
              target=agent.device.name, ip=client_ip(request), bulk=True)
        approved += 1
    await session.commit()
    return {"approved": approved}


@router.post("/agents/{agent_id}/revoke", status_code=204)
async def revoke_agent(agent_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, request: Request):
    await _set_agent_status(agent_id, AgentStatus.REVOKED, user, session, request)


# --- problemi (vista unica trasversale ai tenant) -------------------------


@router.get("/problems", response_model=list[ProblemRow])
async def list_problems(
    user: CurrentUserDep,
    session: SessionDep,
    tenant_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    state: Annotated[list[ProblemState] | None, Query()] = None,
    severity: Severity | None = None,
    limit: int = Query(200, le=1000),
):
    states = state or [ProblemState.OPEN]
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
    if device_id:
        stmt = stmt.where(Problem.device_id == device_id)
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


# --- finestra di backup dei PC ------------------------------------------------


def _window(v: dict | None) -> BackupWindowIn | None:
    return BackupWindowIn.model_validate(v) if v else None


def _window_state(tenant: Tenant, device: Device | None = None) -> BackupWindowState:
    now = utcnow()
    if device is None:
        eff = tenant.backup_window if tenant.backup_window and tenant.backup_window.get("enabled", True) else None
        source = "tenant" if tenant.backup_window is not None else "none"
        applies = True
    else:
        eff, source = backup_window.effective(device, tenant)
        applies = backup_window.applies(device)
    active, until = backup_window.active_at(eff, now) if applies else (False, None)
    return BackupWindowState(
        tenant=_window(tenant.backup_window), device=_window(device.backup_window) if device else None,
        effective=_window(eff), source=source, applies=applies, active=active, until=until,
    )


@router.get("/tenants/{tenant_id}/backup-window", response_model=BackupWindowState)
async def get_tenant_window(tenant_id: uuid.UUID, user: CurrentUserDep, session: SessionDep):
    user.require_read(tenant_id)
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    return _window_state(tenant)


@router.put("/tenants/{tenant_id}/backup-window", response_model=BackupWindowState)
async def set_tenant_window(
    tenant_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, request: Request,
    body: Annotated[BackupWindowIn | None, Body()] = None,
):
    """body = null: nessuna finestra per i PC del tenant (salvo quelli con un'impostazione propria)."""
    user.require_write(tenant_id)
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    tenant.backup_window = body.model_dump() if body else None
    audit(session, "backup_window.tenant", user_id=user.id, tenant_id=tenant_id, target=tenant.slug,
          ip=client_ip(request), window=tenant.backup_window)
    await session.commit()
    return _window_state(tenant)


@router.get("/devices/{device_id}/backup-window", response_model=BackupWindowState)
async def get_device_window(device_id: uuid.UUID, user: CurrentUserDep, session: SessionDep):
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_read(device.tenant_id)
    return _window_state(await session.get(Tenant, device.tenant_id), device)


@router.put("/devices/{device_id}/backup-window", response_model=BackupWindowState)
async def set_device_window(
    device_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, request: Request,
    body: Annotated[BackupWindowIn | None, Body()] = None,
):
    """body = null: il PC eredita la finestra del tenant; {"enabled": false}: nessuna finestra."""
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_write(device.tenant_id)
    if body is not None and not backup_window.applies(device):
        raise HTTPException(status.HTTP_409_CONFLICT, "la finestra di backup vale solo per i PC Windows con Veeam Agent")
    device.backup_window = body.model_dump() if body else None
    audit(session, "backup_window.device", user_id=user.id, tenant_id=device.tenant_id, target=device.name,
          ip=client_ip(request), window=device.backup_window)
    await session.commit()
    return _window_state(await session.get(Tenant, device.tenant_id), device)
