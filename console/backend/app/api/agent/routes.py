import hashlib
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent.deps import AgentDep, ApprovedAgentDep, CADep, SessionDep
from app.config import settings
from app.models import (
    Agent,
    AgentMessage,
    AgentStatus,
    Device,
    DeviceKind,
    EnrollmentToken,
    Event,
    InventorySnapshot,
    Metric,
)
from app.models.base import utcnow
from app.schemas.agent import (
    EnrollRequest,
    EnrollResponse,
    Envelope,
    EventsPayload,
    HeartbeatRequest,
    HeartbeatResponse,
    IngestResponse,
    InventoryPayload,
    MetricsPayload,
)
from app.services.audit import audit
from app.services.ca import CSRError
from app.services.problems import evaluate_metrics, resolve_problem

router = APIRouter(prefix="/agent/v1", tags=["agent"])


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def kind_from_roles(roles: list[str]) -> DeviceKind:
    if "qnap" in roles or "qnap_hero" in roles:
        return DeviceKind.NAS
    if "veeam_vbr" in roles:
        return DeviceKind.SERVER
    if "veeam_agent" in roles:
        return DeviceKind.PC
    return DeviceKind.SERVER


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/enroll", response_model=EnrollResponse)
async def enroll(body: EnrollRequest, request: Request, session: SessionDep, ca: CADep) -> EnrollResponse:
    now = utcnow()
    token = await session.scalar(
        select(EnrollmentToken)
        .where(EnrollmentToken.token_hash == hash_token(body.token))
        .with_for_update()
    )
    if token is None or token.used_at is not None or token.expires_at < now:
        # Stessa risposta per token inesistente, usato o scaduto.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token di enrollment non valido")

    agent_id = uuid.uuid4()
    try:
        cert_pem, fp, not_after = ca.sign_csr(body.csr_pem, agent_id, settings.agent_cert_days)
    except CSRError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    device = Device(
        tenant_id=token.tenant_id,
        site_id=token.site_id,
        kind=kind_from_roles(body.roles),
        name=body.hostname,
        hostname=body.hostname,
        platform=body.platform,
        arch=body.arch,
        roles=list(body.roles),
    )
    session.add(device)
    await session.flush()
    agent = Agent(
        id=agent_id,
        device_id=device.id,
        status=AgentStatus.PENDING,
        cert_fingerprint=fp,
        cert_not_after=not_after,
        version=body.agent_version,
        allow_shell=body.allow_shell,
        last_ip=client_ip(request),
    )
    session.add(agent)
    token.used_at = now
    await session.flush()
    audit(
        session, "agent.enroll", agent_id=agent_id, tenant_id=device.tenant_id,
        target=str(device.id), ip=client_ip(request), hostname=body.hostname, roles=list(body.roles),
    )
    await session.commit()
    return EnrollResponse(
        agent_id=agent_id, device_id=device.id, status=agent.status, cert_pem=cert_pem, ca_pem=ca.ca_pem()
    )


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(body: HeartbeatRequest, request: Request, agent: AgentDep, session: SessionDep):
    now = utcnow()
    agent.last_seen_at = now
    agent.last_ip = client_ip(request)
    agent.version = body.version
    agent.capabilities = body.capabilities
    agent.allow_shell = body.allow_shell
    agent.device.last_seen_at = now
    if body.roles:
        agent.device.roles = list(body.roles)
    if agent.status == AgentStatus.APPROVED:
        await resolve_problem(session, agent.device_id, "agent.offline")
    await session.commit()
    return HeartbeatResponse(status=agent.status, server_time=now)


async def _first_delivery(session: AsyncSession, agent: Agent, msg_id: uuid.UUID, kind: str) -> bool:
    """Registra msg_id; False se il messaggio era già stato ricevuto (reinvio dal buffer)."""
    result = await session.execute(
        insert(AgentMessage)
        .values(msg_id=msg_id, agent_id=agent.id, kind=kind, received_at=utcnow())
        .on_conflict_do_nothing(index_elements=["msg_id"])
    )
    return result.rowcount == 1


@router.post("/inventory", response_model=IngestResponse)
async def inventory(env: Envelope[InventoryPayload], agent: ApprovedAgentDep, session: SessionDep):
    if not await _first_delivery(session, agent, env.msg_id, "inventory"):
        return IngestResponse(accepted=True, duplicate=True)
    p = env.payload
    device = agent.device
    for field in ("model", "serial", "os_version"):
        if (value := getattr(p, field)) is not None:
            setattr(device, field, value[:200])
    session.add(InventorySnapshot(device_id=device.id, collected_at=env.ts, data=p.model_dump()))
    await session.commit()
    return IngestResponse(accepted=True)


@router.post("/metrics", response_model=IngestResponse)
async def metrics(env: Envelope[MetricsPayload], agent: ApprovedAgentDep, session: SessionDep):
    if not await _first_delivery(session, agent, env.msg_id, "metrics"):
        return IngestResponse(accepted=True, duplicate=True)
    device = agent.device
    session.add_all(
        Metric(tenant_id=device.tenant_id, device_id=device.id, ts=m.ts, name=m.name, labels=m.labels, value=m.value)
        for m in env.payload.metrics
    )
    await evaluate_metrics(session, device, env.payload.metrics)
    await session.commit()
    return IngestResponse(accepted=True)


@router.post("/events", response_model=IngestResponse)
async def events(env: Envelope[EventsPayload], agent: ApprovedAgentDep, session: SessionDep):
    if not await _first_delivery(session, agent, env.msg_id, "events"):
        return IngestResponse(accepted=True, duplicate=True)
    device = agent.device
    session.add_all(
        Event(tenant_id=device.tenant_id, device_id=device.id, ts=e.ts, type=e.type, severity=e.severity, data=e.data)
        for e in env.payload.events
    )
    await session.commit()
    return IngestResponse(accepted=True)
