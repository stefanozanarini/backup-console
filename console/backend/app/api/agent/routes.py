import asyncio
import hashlib
import time
import uuid

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent.deps import AgentDep, ApprovedAgentDep, CADep, SessionDep
from app.config import settings
from app.db import SessionLocal
from app.models import (
    FINAL_STATUSES,
    MAX_OUTPUT,
    Agent,
    Command,
    CommandStatus,
    CommandType,
    ReleaseAsset,
    Severity,
    AgentMessage,
    AgentStatus,
    Device,
    DeviceKind,
    EnrollmentToken,
    Event,
    InventorySnapshot,
    Metric,
    Release,
    Tenant,
    token_usable,
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
    AgentCommand,
    CommandOutputIn,
    CommandResultIn,
    VeeamPayload,
)
from app.services.audit import audit
from app.services.ca import CSRError
from app.services import backup_window
from app.services import veeam as veeam_service
from app.services.problems import evaluate_metrics, raise_problem, resolve_problem
from app.services.releases import asset_path, download_name

router = APIRouter(prefix="/agent/v1", tags=["agent"])


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def kind_from_roles(roles: list[str]) -> DeviceKind:
    if "qnap" in roles or "qnap_hero" in roles or "synology" in roles:
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
    if token is None or not token_usable(token, now):
        # Stessa risposta per token inesistente, esaurito, revocato o scaduto.
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
        enrollment_token_id=token.id,
    )
    session.add(agent)
    token.used_at = now
    token.uses += 1
    await session.flush()
    audit(
        session, "agent.enroll", agent_id=agent_id, tenant_id=device.tenant_id,
        target=str(device.id), ip=client_ip(request), hostname=body.hostname, roles=list(body.roles),
    )
    await session.commit()
    return EnrollResponse(
        agent_id=agent_id, device_id=device.id, status=agent.status, cert_pem=cert_pem, ca_pem=ca.ca_pem()
    )


@router.get("/download/{key}")
async def bootstrap_download(
    key: str, request: Request, session: SessionDep, x_enroll_token: str = Header(...),
):
    """Ultima versione dell'agente o del QPKG, per l'installazione con un solo comando.
    Autorizzato dal token di enrollment (nell'header, mai nell'URL), senza consumarlo."""
    token = await session.scalar(
        select(EnrollmentToken).where(EnrollmentToken.token_hash == hash_token(x_enroll_token))
    )
    if token is None or not token_usable(token, utcnow()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token di enrollment non valido")
    row = await session.execute(
        select(ReleaseAsset)
        .join(Release, Release.version == ReleaseAsset.version)
        .where(ReleaseAsset.platform == key)
        .order_by(Release.created_at.desc())
        .limit(1)
    )
    asset = row.scalar()
    path = asset_path(asset.version, key) if asset else None
    if asset is None or path is None or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"nessun rilascio disponibile per {key}")
    audit(session, "agent.bootstrap_download", tenant_id=token.tenant_id, target=f"{asset.version}/{key}",
          ip=client_ip(request), token_note=token.note)
    await session.commit()
    return FileResponse(path, media_type="application/octet-stream",
                        filename=download_name(asset.version, key),
                        headers={"X-Agent-Version": asset.version, "X-Content-SHA256": asset.sha256})


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
        await _complete_upgrades(session, agent)
    window = None
    if agent.status == AgentStatus.APPROVED:
        window = backup_window.for_agent(agent.device, await session.get(Tenant, agent.device.tenant_id))
    await session.commit()
    return HeartbeatResponse(status=agent.status, server_time=now, backup_window=window)


async def _complete_upgrades(session: AsyncSession, agent: Agent) -> None:
    """Un upgrade è riuscito quando l'agente si ripresenta con la versione richiesta."""
    pending = await session.scalars(
        select(Command).where(
            Command.agent_id == agent.id,
            Command.type == CommandType.UPGRADE,
            Command.status.in_([CommandStatus.DISPATCHED, CommandStatus.RUNNING]),
        )
    )
    for cmd in pending:
        if cmd.payload.get("version") == agent.version:
            cmd.status, cmd.finished_at, cmd.exit_code = CommandStatus.SUCCEEDED, utcnow(), 0
            cmd.output += f"\nagente ripartito con la versione {agent.version}\n"
            await resolve_problem(session, agent.device_id, "agent.upgrade")


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
    # L'hostname segue il sistema (PC rinominato in Windows); il nome in console no.
    if p.hostname:
        device.hostname = p.hostname[:255]
    for field in ("model", "serial", "os_version"):
        if (value := getattr(p, field)) is not None:
            setattr(device, field, value[:200])
    # L'agente raggruppa i dati per collector (data["system"], data["veeam_vbr"], ...).
    sysdata = p.data.get("system") if isinstance(p.data.get("system"), dict) else {}
    # remote_access e network ci sono solo con agenti che li rilevano: se mancano non
    # tocchiamo i valori salvati.
    ra = sysdata.get("remote_access")
    if isinstance(ra, dict):
        sid = ra.get("supremo_id")
        device.supremo_id = str(sid)[:32] if isinstance(sid, (str, int)) and str(sid).isdigit() else None
    nics = sysdata.get("network")
    if isinstance(nics, list):
        device.ip_addresses = [
            {"name": str(n.get("name", ""))[:100], "mac": str(n.get("mac") or "")[:32] or None,
             "ipv4": [str(ip)[:45] for ip in (n.get("ipv4") or [])][:10]}
            for n in nics[:20] if isinstance(n, dict)
        ]
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


COLLECTOR_LABELS = {"veeam_vbr": "Veeam B&R", "veeam_vaw": "Veeam Agent", "uranium": "Uranium Backup",
                    "synology": "Hyper Backup"}


@router.post("/events", response_model=IngestResponse)
async def events(env: Envelope[EventsPayload], agent: ApprovedAgentDep, session: SessionDep):
    if not await _first_delivery(session, agent, env.msg_id, "events"):
        return IngestResponse(accepted=True, duplicate=True)
    device = agent.device
    session.add_all(
        Event(tenant_id=device.tenant_id, device_id=device.id, ts=e.ts, type=e.type, severity=e.severity, data=e.data)
        for e in env.payload.events
    )
    for e in env.payload.events:
        if e.type == "agent.upgrade_rollback":
            await raise_problem(
                session, device, "agent.upgrade", Severity.WARNING,
                f"Aggiornamento a {e.data.get('to', '?')} fallito: ripristinata la {e.data.get('from', '?')}",
                details=e.data,
            )
        elif e.type == "collector.report_failed":
            collector = str(e.data.get("collector", "?"))[:50]
            label = COLLECTOR_LABELS.get(collector, collector)
            await raise_problem(
                session, device, "agent.collector", Severity.WARNING, f"Raccolta dati {label} non riuscita",
                {"collector": collector}, {"collector": collector, "error": str(e.data.get("error", ""))[:2000],
                                           "at": e.ts.isoformat()},
            )
    await session.commit()
    return IngestResponse(accepted=True)


async def _app_report(product: str, env: Envelope[VeeamPayload], agent, session: AsyncSession) -> IngestResponse:
    if not await _first_delivery(session, agent, env.msg_id, product):
        return IngestResponse(accepted=True, duplicate=True)
    if env.payload.product != product:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "prodotto non valido per questo endpoint")
    await veeam_service.ingest(session, agent.device, env.payload, env.ts)
    await session.commit()
    return IngestResponse(accepted=True)


@router.post("/uranium", response_model=IngestResponse)
async def uranium(env: Envelope[VeeamPayload], agent: ApprovedAgentDep, session: SessionDep):
    """Uranium Backup: stesso formato e stessi controlli dei job Veeam."""
    return await _app_report("uranium", env, agent, session)


@router.post("/hyperbackup", response_model=IngestResponse)
async def hyperbackup(env: Envelope[VeeamPayload], agent: ApprovedAgentDep, session: SessionDep):
    """Hyper Backup dei NAS Synology: stesso formato e stessi controlli dei job Veeam."""
    return await _app_report("hyperbackup", env, agent, session)


@router.post("/veeam", response_model=IngestResponse)
async def veeam(env: Envelope[VeeamPayload], agent: ApprovedAgentDep, session: SessionDep):
    if not await _first_delivery(session, agent, env.msg_id, "veeam"):
        return IngestResponse(accepted=True, duplicate=True)
    if env.payload.product not in ("vbr", "vaw"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{env.payload.product} ha un endpoint proprio")
    await veeam_service.ingest(session, agent.device, env.payload, env.ts)
    await session.commit()
    return IngestResponse(accepted=True)


# --- comandi ----------------------------------------------------------------


async def _dispatch_next(agent_id: uuid.UUID) -> AgentCommand | None:
    async with SessionLocal() as s:
        cmd = await s.scalar(
            select(Command)
            .where(Command.agent_id == agent_id, Command.status == CommandStatus.QUEUED)
            .order_by(Command.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if cmd is None:
            return None
        cmd.status, cmd.dispatched_at = CommandStatus.DISPATCHED, utcnow()
        await s.commit()
        return AgentCommand(id=cmd.id, type=cmd.type, timeout_s=cmd.timeout_s, payload=cmd.payload)


@router.get("/commands/next", response_model=AgentCommand, responses={204: {"description": "nessun comando"}})
async def next_command(agent: ApprovedAgentDep, session: SessionDep, wait: int = Query(25, ge=0, le=55)):
    """Long-poll: risponde appena c'è un comando in coda, altrimenti 204 dopo `wait` secondi."""
    agent_id = agent.id
    # Rilasciamo subito la connessione: l'attesa non deve occupare il pool del DB.
    await session.close()
    deadline = time.monotonic() + min(wait, settings.command_poll_wait_s)
    while True:
        cmd = await _dispatch_next(agent_id)
        if cmd is not None:
            return cmd
        if time.monotonic() >= deadline:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        await asyncio.sleep(1)


async def _own_command(session: AsyncSession, agent: Agent, command_id: uuid.UUID) -> Command:
    cmd = await session.get(Command, command_id, with_for_update=True)
    if cmd is None or cmd.agent_id != agent.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "comando sconosciuto")
    if cmd.status in FINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"comando già concluso ({cmd.status})")
    return cmd


@router.post("/commands/{command_id}/output", status_code=204)
async def command_output(command_id: uuid.UUID, body: CommandOutputIn, agent: ApprovedAgentDep, session: SessionDep):
    cmd = await _own_command(session, agent, command_id)
    cmd.status = CommandStatus.RUNNING
    room = MAX_OUTPUT - len(cmd.output)
    if room <= 0:
        cmd.output_truncated = True
    else:
        cmd.output += body.data[:room]
        cmd.output_truncated = len(body.data) > room
    await session.commit()


@router.post("/commands/{command_id}/result", status_code=204)
async def command_result(command_id: uuid.UUID, body: CommandResultIn, agent: ApprovedAgentDep, session: SessionDep):
    cmd = await _own_command(session, agent, command_id)
    cmd.exit_code, cmd.error = body.exit_code, body.error
    if body.result is not None:
        cmd.result = body.result
    if cmd.type == CommandType.UPGRADE and body.status == "succeeded":
        # Binario sostituito: la conferma arriva dall'heartbeat con la nuova versione.
        cmd.status = CommandStatus.RUNNING
    else:
        cmd.status = CommandStatus(body.status)
        cmd.finished_at = utcnow()
    await session.commit()


@router.get("/releases/{version}/{platform}")
async def download_release(version: str, platform: str, agent: ApprovedAgentDep, session: SessionDep):
    asset = await session.get(ReleaseAsset, (version, platform))
    path = asset_path(version, platform)
    if asset is None or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rilascio non disponibile")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)
