"""API operative della console: sessione elevata, comandi remoti, rilasci e aggiornamenti, Veeam."""

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.agent.routes import client_ip
from app.api.apikeys import superadmin_or_key
from app.auth import CurrentUser, CurrentUserDep
from app.config import settings
from app.db import get_session
from fastapi.responses import FileResponse

from app.models import (
    PACKAGES,
    PLATFORMS,
    Agent,
    AgentStatus,
    Command,
    CommandStatus,
    CommandType,
    Device,
    Elevation,
    Release,
    ReleaseAsset,
    Site,
    Tenant,
    VeeamJob,
    VeeamSession,
)
from app.models.base import utcnow
from app.schemas.ui import (
    CommandCreate,
    CommandDetail,
    CommandOut,
    ElevateIn,
    ElevationOut,
    ReleaseAssetOut,
    ReleaseOut,
    TotpCode,
    TotpSetupOut,
    UpgradeRequest,
    UpgradeResult,
    VeeamCleanupIn,
    VeeamCleanupScanIn,
    VeeamJobOut,
    VeeamJobRow,
    VeeamLicenseRow,
    VeeamSessionOut,
)
from app.services import secrets as secret_store
from app.services import totp
from app.services.audit import audit
from app.services.releases import VERSION_RE, asset_key, asset_path, download_name, sha256_hex, verify_signature

router = APIRouter(prefix="/api/v1", tags=["console"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- TOTP e sessione elevata ------------------------------------------------


@router.post("/me/totp/setup", response_model=TotpSetupOut)
async def totp_setup(user: CurrentUserDep, session: SessionDep, request: Request):
    """Genera un nuovo segreto (non ancora attivo finché non viene confermato)."""
    if user.user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "TOTP già attivo: per rigenerarlo serve un reset dell'amministratore")
    secret = totp.new_secret()
    user.user.totp_secret_enc = secret_store.encrypt(secret)
    audit(session, "totp.setup", user_id=user.id, ip=client_ip(request))
    await session.commit()
    return TotpSetupOut(secret=secret, uri=totp.provisioning_uri(secret, user.user.email))


@router.post("/me/totp/confirm", status_code=204)
async def totp_confirm(body: TotpCode, user: CurrentUserDep, session: SessionDep, request: Request):
    if user.user.totp_enabled or not user.user.totp_secret_enc:
        raise HTTPException(status.HTTP_409_CONFLICT, "nessuna configurazione TOTP in corso")
    counter = totp.accept(secret_store.decrypt(user.user.totp_secret_enc), body.code, None)
    if counter is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "codice non valido")
    user.user.totp_enabled, user.user.totp_last_counter = True, counter
    audit(session, "totp.enable", user_id=user.id, ip=client_ip(request))
    await session.commit()


@router.post("/elevate", response_model=ElevationOut)
async def elevate(body: ElevateIn, user: CurrentUserDep, session: SessionDep, request: Request):
    u = user.user
    if not u.totp_enabled or not u.totp_secret_enc:
        raise HTTPException(status.HTTP_412_PRECONDITION_FAILED, "configurare prima il codice TOTP")
    counter = totp.accept(secret_store.decrypt(u.totp_secret_enc), body.code, u.totp_last_counter)
    if counter is None:
        audit(session, "elevation.denied", user_id=user.id, ip=client_ip(request), reason=body.reason)
        await session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "codice non valido o già usato: attendi il successivo")
    u.totp_last_counter = counter
    token = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(minutes=settings.elevation_minutes)
    session.add(Elevation(user_id=u.id, token_hash=_hash(token), reason=body.reason,
                          ip=client_ip(request), expires_at=expires))
    audit(session, "elevation.grant", user_id=user.id, ip=client_ip(request), reason=body.reason)
    await session.commit()
    return ElevationOut(token=token, expires_at=expires)


async def require_elevation(
    user: CurrentUserDep,
    session: SessionDep,
    x_elevation: Annotated[str | None, Header()] = None,
) -> Elevation:
    if x_elevation:
        el = await session.scalar(
            select(Elevation).where(
                Elevation.token_hash == _hash(x_elevation),
                Elevation.user_id == user.id,
                Elevation.expires_at > utcnow(),
            )
        )
        if el is not None:
            return el
    raise HTTPException(status.HTTP_403_FORBIDDEN, "elevation_required")


ElevationDep = Annotated[Elevation, Depends(require_elevation)]


# --- comandi remoti ---------------------------------------------------------


async def _device_with_agent(session: AsyncSession, device_id: uuid.UUID, user: CurrentUser) -> Device:
    device = await session.get(Device, device_id, options=[selectinload(Device.agent)])
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_read(device.tenant_id)
    return device


@router.post("/devices/{device_id}/commands", response_model=CommandOut, status_code=201)
async def create_command(
    device_id: uuid.UUID, body: CommandCreate, user: CurrentUserDep, elevation: ElevationDep,
    session: SessionDep, request: Request,
):
    device = await _device_with_agent(session, device_id, user)
    user.require_write(device.tenant_id)
    agent = device.agent
    if agent is None or agent.status != AgentStatus.APPROVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "agente non approvato")
    if not agent.allow_shell:
        raise HTTPException(status.HTTP_409_CONFLICT, "esecuzione remota disabilitata sul dispositivo")
    cmd = Command(
        tenant_id=device.tenant_id, device_id=device.id, agent_id=agent.id, type=CommandType.EXEC,
        payload={"command": body.command, "shell": body.shell}, timeout_s=body.timeout_s,
        reason=elevation.reason, created_by=user.id,
    )
    session.add(cmd)
    await session.flush()
    audit(session, "command.exec", user_id=user.id, agent_id=agent.id, tenant_id=device.tenant_id,
          target=f"{device.name}:{cmd.id}", ip=client_ip(request), reason=elevation.reason,
          command=body.command[:2000], shell=body.shell)
    await session.commit()
    return cmd


@router.get("/devices/{device_id}/commands", response_model=list[CommandOut])
async def list_commands(device_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, limit: int = Query(30, le=200)):
    device = await _device_with_agent(session, device_id, user)
    rows = await session.scalars(
        select(Command).where(Command.device_id == device.id).order_by(Command.created_at.desc()).limit(limit)
    )
    return rows.all()


@router.get("/commands/{command_id}", response_model=CommandDetail)
async def get_command(command_id: uuid.UUID, user: CurrentUserDep, session: SessionDep):
    cmd = await session.get(Command, command_id)
    if cmd is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    user.require_read(cmd.tenant_id)
    return cmd


# --- rilasci dell'agente ----------------------------------------------------


@router.get("/releases", response_model=list[ReleaseOut])
async def list_releases(user: CurrentUserDep, session: SessionDep):
    releases = (await session.scalars(select(Release).order_by(Release.created_at.desc()))).all()
    assets = (await session.scalars(select(ReleaseAsset))).all()
    scope = user.tenant_scope()
    count_stmt = select(Agent.version, func.count()).join(Device, Device.id == Agent.device_id).where(
        Agent.status == AgentStatus.APPROVED
    )
    if scope is not None:
        count_stmt = count_stmt.where(Device.tenant_id.in_(scope))
    counts = dict((await session.execute(count_stmt.group_by(Agent.version))).all())
    by_version: dict[str, list[ReleaseAssetOut]] = {}
    for a in assets:
        by_version.setdefault(a.version, []).append(ReleaseAssetOut.model_validate(a))
    return [
        ReleaseOut(version=r.version, notes=r.notes, created_at=r.created_at,
                   assets=sorted(by_version.get(r.version, []), key=lambda a: a.platform),
                   agents=counts.get(r.version, 0))
        for r in releases
    ]


@router.post("/releases", response_model=ReleaseOut, status_code=201)
async def upload_release(
    session: SessionDep,
    request: Request,
    manifest: Annotated[UploadFile, File(description="manifest.json prodotto da `bc-release sign`")],
    files: Annotated[list[UploadFile], File(description="binari bc-agent-<piattaforma>[.exe] e QPKG")],
    notes: Annotated[str | None, Form(max_length=2000)] = None,
):
    """Superadmin dalla console, oppure la CI con una chiave API con scope releases:upload."""
    actor = await superadmin_or_key(request, session, "releases:upload")
    try:
        man = json.loads(await manifest.read())
        version = man["version"]
        entries: dict = man["assets"]
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "manifest non valido") from e
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "versione non valida")
    if await session.get(Release, version) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "versione già presente")

    uploaded: dict[str, bytes] = {}
    for f in files:
        key = asset_key(f.filename or "")
        if key is None or key not in (*PLATFORMS, *PACKAGES):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"nome file non riconosciuto: {f.filename}")
        if key.startswith("qpkg-") and f"_{version}_" not in (f.filename or ""):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{f.filename}: versione diversa da {version}")
        uploaded[key] = await f.read()
    if not uploaded:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nessun binario")

    assets = []
    for platform, data in uploaded.items():
        meta = entries.get(platform)
        if not isinstance(meta, dict) or meta.get("sha256") != sha256_hex(data) or not meta.get("signature"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{platform}: hash o firma mancanti o non corrispondenti")
        if settings.release_public_key and not verify_signature(data, meta["signature"], settings.release_public_key):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{platform}: firma non valida")
        assets.append(ReleaseAsset(version=version, platform=platform, sha256=meta["sha256"],
                                   size=len(data), signature=meta["signature"]))

    for a in assets:
        path = asset_path(version, a.platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(uploaded[a.platform])
        tmp.replace(path)
    session.add(Release(version=version, notes=notes, created_by=actor.user_id))
    await session.flush()
    session.add_all(assets)
    audit(session, "release.upload", user_id=actor.user_id, target=version, ip=client_ip(request),
          by=actor.label,
          platforms=sorted(uploaded))
    await session.commit()
    return ReleaseOut(version=version, notes=notes, created_at=utcnow(),
                      assets=[ReleaseAssetOut.model_validate(a) for a in assets], agents=0)


@router.get("/releases/{version}/assets/{key}")
async def download_asset(version: str, key: str, user: CurrentUserDep, session: SessionDep):
    """Download manuale di un binario o di un QPKG dalla console."""
    asset = await session.get(ReleaseAsset, (version, key))
    path = asset_path(version, key)
    if asset is None or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")
    return FileResponse(path, media_type="application/octet-stream", filename=download_name(version, key))


def device_platform(device: Device) -> str:
    arch = "armv7" if device.arch == "arm" else device.arch
    return f"{device.platform}-{arch}"


@router.post("/upgrades", response_model=UpgradeResult)
async def request_upgrade(body: UpgradeRequest, user: CurrentUserDep, session: SessionDep, request: Request):
    """Mette in coda l'aggiornamento per gli agenti approvati nel perimetro indicato."""
    user.require_superadmin()
    release = await session.get(Release, body.version)
    if release is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "versione non trovata")
    assets = {a.platform: a for a in await session.scalars(select(ReleaseAsset).where(ReleaseAsset.version == body.version))}

    stmt = select(Device).join(Agent, Agent.device_id == Device.id).where(Agent.status == AgentStatus.APPROVED)
    stmt = stmt.options(selectinload(Device.agent))
    if body.tenant_id:
        stmt = stmt.where(Device.tenant_id == body.tenant_id)
    if body.device_ids is not None:
        stmt = stmt.where(Device.id.in_(body.device_ids))
    devices = (await session.scalars(stmt)).all()

    pending = set(
        await session.scalars(
            select(Command.agent_id).where(
                Command.type == CommandType.UPGRADE,
                Command.status.in_([CommandStatus.QUEUED, CommandStatus.DISPATCHED, CommandStatus.RUNNING]),
            )
        )
    )
    skipped = {"già aggiornati": 0, "piattaforma non inclusa": 0, "aggiornamento in corso": 0}
    queued = 0
    for d in devices:
        agent = d.agent
        assert agent is not None
        platform = device_platform(d)
        if agent.version == body.version:
            skipped["già aggiornati"] += 1
        elif platform not in assets:
            skipped["piattaforma non inclusa"] += 1
        elif agent.id in pending:
            skipped["aggiornamento in corso"] += 1
        else:
            a = assets[platform]
            session.add(Command(
                tenant_id=d.tenant_id, device_id=d.id, agent_id=agent.id, type=CommandType.UPGRADE,
                payload={"version": body.version, "platform": platform, "sha256": a.sha256, "size": a.size,
                         "signature": a.signature, "path": f"/agent/v1/releases/{body.version}/{platform}",
                         "from": agent.version},
                timeout_s=900, reason=f"aggiornamento a {body.version}", created_by=user.id,
            ))
            queued += 1
    audit(session, "upgrade.request", user_id=user.id, tenant_id=body.tenant_id, target=body.version,
          ip=client_ip(request), queued=queued, skipped=skipped)
    await session.commit()
    return UpgradeResult(queued=queued, skipped={k: v for k, v in skipped.items() if v})


# --- Veeam ------------------------------------------------------------------


LICENSE_FIELDS = ("edition", "status", "type", "licensed_to", "expiration", "support_expiration",
                  "instances_total", "instances_used", "sockets_total", "sockets_used")


@router.get("/veeam/licenses", response_model=list[VeeamLicenseRow])
async def list_veeam_licenses(user: CurrentUserDep, session: SessionDep, tenant_id: uuid.UUID | None = None):
    """Licenze dei server Veeam B&R (dall'ultimo report di ciascuno), le più vicine alla scadenza prima."""
    scope = user.tenant_scope()
    stmt = (
        select(Device, Tenant.name, Site.name)
        .join(Tenant, Tenant.id == Device.tenant_id)
        .join(Site, Site.id == Device.site_id)
        .options(selectinload(Device.agent))
        .where(Device.veeam["product"].astext == "vbr")
    )
    if scope is not None:
        stmt = stmt.where(Device.tenant_id.in_(scope))
    if tenant_id:
        stmt = stmt.where(Device.tenant_id == tenant_id)
    online_since = utcnow() - timedelta(seconds=settings.agent_offline_after_s)
    out = []
    for d, tn, sn in (await session.execute(stmt)).all():
        v = d.veeam or {}
        lic = v.get("license") or {}
        seen = d.agent.last_seen_at if d.agent else None
        out.append(VeeamLicenseRow(
            device_id=d.id, device_name=d.name, tenant_id=d.tenant_id, tenant_name=tn, site_name=sn,
            veeam_version=v.get("version"), reported_at=v.get("reported_at"),
            online=seen is not None and seen >= online_since,
            **{k: lic.get(k) for k in LICENSE_FIELDS},
        ))
    far = datetime.max.replace(tzinfo=UTC)
    return sorted(out, key=lambda r: (r.expiration or far, r.tenant_name, r.device_name))


@router.get("/veeam/sessions", response_model=list[VeeamSessionOut])
async def list_veeam_sessions(
    user: CurrentUserDep,
    session: SessionDep,
    device_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    job_key: str | None = Query(None, max_length=200),
    days: int = Query(30, ge=1, le=3650),
    limit: int = Query(1000, ge=1, le=5000),
):
    """Storico delle sessioni salvato in console (anche oltre la retention di Veeam)."""
    if device_id is None and tenant_id is None:
        raise HTTPException(400, "serve device_id o tenant_id")
    scope = user.tenant_scope()
    since = utcnow() - timedelta(days=days)
    when = func.coalesce(VeeamSession.started_at, VeeamSession.ended_at)
    stmt = select(VeeamSession).where(when >= since)
    if scope is not None:
        stmt = stmt.where(VeeamSession.tenant_id.in_(scope))
    if device_id:
        stmt = stmt.where(VeeamSession.device_id == device_id)
    if tenant_id:
        stmt = stmt.where(VeeamSession.tenant_id == tenant_id)
    if job_key:
        stmt = stmt.where(VeeamSession.job_key == job_key)
    return (await session.scalars(stmt.order_by(when.desc()).limit(limit))).all()


@router.get("/veeam/jobs", response_model=list[VeeamJobRow])
async def list_veeam_jobs(
    user: CurrentUserDep,
    session: SessionDep,
    tenant_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    result: str | None = None,
):
    scope = user.tenant_scope()
    stmt = (
        select(VeeamJob, Device.name, Tenant.name, Site.name)
        .join(Device, Device.id == VeeamJob.device_id)
        .join(Tenant, Tenant.id == VeeamJob.tenant_id)
        .join(Site, Site.id == Device.site_id)
    )
    if scope is not None:
        stmt = stmt.where(VeeamJob.tenant_id.in_(scope))
    if tenant_id:
        stmt = stmt.where(VeeamJob.tenant_id == tenant_id)
    if device_id:
        stmt = stmt.where(VeeamJob.device_id == device_id)
    if result:
        stmt = stmt.where(func.lower(VeeamJob.last_result) == result.lower())
    rows = (await session.execute(stmt.order_by(Tenant.name, Site.name, Device.name, VeeamJob.name))).all()
    return [
        VeeamJobRow(**VeeamJobOut.model_validate(j).model_dump(), device_name=dn, tenant_name=tn, site_name=sn)
        for j, dn, tn, sn in rows
    ]


# --- pulizia guidata dei backup Veeam ------------------------------------------
#
# 1. analisi (solo lettura): l'agente elenca i backup e marca come eliminabili solo quelli
#    di job disabilitati o eliminati con l'ultimo restore point più vecchio della soglia;
# 2. eliminazione (sessione elevata): solo id marcati eliminabili da un'analisi recente
#    dello stesso dispositivo; l'agente li ricontrolla uno per uno prima di cancellarli.

SCAN_MAX_AGE = timedelta(hours=1)


async def _vbr_device(session: AsyncSession, device_id: uuid.UUID, user: CurrentUser) -> Device:
    device = await _device_with_agent(session, device_id, user)
    user.require_write(device.tenant_id)
    if device.agent is None or device.agent.status != AgentStatus.APPROVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "agente non approvato")
    if (device.veeam or {}).get("product") != "vbr":
        raise HTTPException(status.HTTP_409_CONFLICT, "il dispositivo non è un server Veeam Backup & Replication")
    return device


@router.post("/devices/{device_id}/veeam/cleanup-scan", response_model=CommandOut, status_code=201)
async def veeam_cleanup_scan(
    device_id: uuid.UUID, body: VeeamCleanupScanIn, user: CurrentUserDep, session: SessionDep, request: Request
):
    device = await _vbr_device(session, device_id, user)
    cmd = Command(
        tenant_id=device.tenant_id, device_id=device.id, agent_id=device.agent.id, type=CommandType.VEEAM_SCAN,
        payload={"older_than_days": body.older_than_days}, timeout_s=900,
        reason=f"analisi backup più vecchi di {body.older_than_days} giorni", created_by=user.id,
    )
    session.add(cmd)
    await session.flush()
    audit(session, "veeam.cleanup_scan", user_id=user.id, agent_id=device.agent.id, tenant_id=device.tenant_id,
          target=f"{device.name}:{cmd.id}", ip=client_ip(request), older_than_days=body.older_than_days)
    await session.commit()
    return cmd


@router.post("/devices/{device_id}/veeam/cleanup", response_model=CommandOut, status_code=201)
async def veeam_cleanup(
    device_id: uuid.UUID, body: VeeamCleanupIn, user: CurrentUserDep, elevation: ElevationDep,
    session: SessionDep, request: Request,
):
    device = await _vbr_device(session, device_id, user)
    if not device.agent.allow_shell:
        raise HTTPException(status.HTTP_409_CONFLICT, "esecuzione remota disabilitata sul dispositivo")
    scan = await session.get(Command, body.scan_id)
    if (
        scan is None or scan.device_id != device.id or scan.type != CommandType.VEEAM_SCAN
        or scan.status != CommandStatus.SUCCEEDED or not scan.result
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "analisi non valida per questo dispositivo")
    if scan.finished_at is None or scan.finished_at < utcnow() - SCAN_MAX_AGE:
        raise HTTPException(status.HTTP_409_CONFLICT, "analisi troppo vecchia: ripeterla")

    candidates = {
        str(i.get("id")): i for i in scan.result.get("items", []) if isinstance(i, dict) and i.get("candidate") is True
    }
    ids = list(dict.fromkeys(body.backup_ids))  # senza duplicati, ordine preservato
    not_allowed = [i for i in ids if i not in candidates]
    if not_allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"backup non eliminabili secondo l'analisi: {', '.join(not_allowed)}")

    items = [
        {k: candidates[i].get(k) for k in ("id", "name", "job", "state", "repository", "size_bytes", "last_point")}
        for i in ids
    ]
    total = sum(int(it.get("size_bytes") or 0) for it in items)
    cmd = Command(
        tenant_id=device.tenant_id, device_id=device.id, agent_id=device.agent.id, type=CommandType.VEEAM_CLEANUP,
        payload={"older_than_days": scan.payload.get("older_than_days"), "backup_ids": ids, "items": items,
                 "scan_id": str(scan.id), "total_bytes": total},
        timeout_s=3600, reason=elevation.reason, created_by=user.id,
    )
    session.add(cmd)
    await session.flush()
    audit(session, "veeam.cleanup", user_id=user.id, agent_id=device.agent.id, tenant_id=device.tenant_id,
          target=f"{device.name}:{cmd.id}", ip=client_ip(request), reason=elevation.reason,
          backups=[it.get("name") for it in items], total_bytes=total)
    await session.commit()
    return cmd
