"""Ingest dei report Veeam e valutazione dei relativi problemi."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, Metric, Severity, VeeamJob
from app.models.base import utcnow
from app.schemas.agent import VeeamPayload
from app.services.problems import evaluate_metrics, raise_problem, resolve_problem, thresholds_for

RESULT_SEVERITY = {"failed": Severity.CRITICAL, "warning": Severity.WARNING}
JOB_CHECKS = ("veeam.job_result", "veeam.last_success_age_h", "veeam.job_retry")
MAX_MESSAGES = 30


def _messages(job: VeeamJob) -> list[dict]:
    """Avvisi/errori dell'ultima sessione, come li riporta l'agente (VBR: log dei task;
    Veeam Agent: testo dell'evento)."""
    d = job.details or {}
    msgs = d.get("messages")
    if isinstance(msgs, list):
        return [m for m in msgs if isinstance(m, dict) and m.get("text")][:MAX_MESSAGES]
    if d.get("message"):
        return [{"object": None, "status": (job.last_result or "").lower() or None, "text": str(d["message"])}]
    return []


class _M:
    """Metrica minima per evaluate_metrics."""

    def __init__(self, name: str, labels: dict[str, str], value: float):
        self.name, self.labels, self.value = name, labels, value


async def ingest(session: AsyncSession, device: Device, p: VeeamPayload, ts: datetime) -> None:
    now = utcnow()
    existing = {
        j.job_key: j
        for j in await session.scalars(
            select(VeeamJob).where(VeeamJob.device_id == device.id, VeeamJob.product == p.product)
        )
    }
    seen: set[str] = set()
    for j in p.jobs:
        seen.add(j.id)
        row = existing.get(j.id)
        if row is None:
            row = VeeamJob(tenant_id=device.tenant_id, device_id=device.id, product=p.product, job_key=j.id)
            session.add(row)
        row.name, row.job_type, row.enabled = j.name, j.type, j.enabled
        row.last_result, row.last_state = j.last_result, j.last_state
        row.last_start, row.last_end, row.next_run = j.last_start, j.last_end, j.next_run
        row.transferred_bytes, row.details, row.updated_at = j.transferred_bytes, j.details, now
        if j.last_success is not None:
            row.last_success = j.last_success
        elif (j.last_result or "").lower() == "success" and j.last_end:
            row.last_success = j.last_end
        await _evaluate_job(session, device, row, now)

    # Job eliminati in Veeam: rimuoviamo riga e problemi.
    for key, row in existing.items():
        if key not in seen:
            for check in JOB_CHECKS:
                await resolve_problem(session, device.id, check, {"job": key})
            await session.execute(delete(VeeamJob).where(VeeamJob.id == row.id))

    # Repository: stesse soglie a cascata dei volumi.
    metrics = []
    for r in p.repositories:
        if not r.total_bytes:
            continue
        free = max(0, r.free_bytes or 0)
        pct = (r.total_bytes - free) * 100 / r.total_bytes
        labels = {"repo": r.name}
        for name, value in (("veeam.repo_used_pct", pct), ("veeam.repo_total_bytes", r.total_bytes),
                            ("veeam.repo_free_bytes", free)):
            session.add(Metric(tenant_id=device.tenant_id, device_id=device.id, ts=ts, name=name,
                               labels=labels, value=float(value)))
        metrics.append(_M("veeam.repo_used_pct", labels, pct))
    await evaluate_metrics(session, device, metrics)

    await _evaluate_license(session, device, p, now)
    device.veeam = {
        "product": p.product,
        "version": p.version,
        "repositories": [r.model_dump() for r in p.repositories],
        "license": p.license.model_dump(mode="json") if p.license else None,
        "errors": p.errors,
        "reported_at": ts.isoformat(),
    }


async def _evaluate_job(session: AsyncSession, device: Device, job: VeeamJob, now: datetime) -> None:
    labels = {"job": job.job_key}
    if not job.enabled:
        for check in JOB_CHECKS:
            await resolve_problem(session, device.id, check, labels)
        return

    severity = RESULT_SEVERITY.get((job.last_result or "").lower())
    if severity is None:
        await resolve_problem(session, device.id, "veeam.job_result", labels)
    else:
        esito = "fallito" if severity == Severity.CRITICAL else "completato con avvisi"
        details = {"job": job.name, "result": job.last_result, "last_end": job.last_end.isoformat() if job.last_end else None,
                   "messages": _messages(job)}
        await raise_problem(session, device, "veeam.job_result", severity, f"Job {job.name}: {esito}", labels, details)

    # Retry nelle ultime 24 ore: segnale di un problema anche se il job alla fine riesce.
    d = job.details or {}
    retries = d.get("retries_24h")
    if isinstance(retries, int) and retries > 0:
        sessions = [s for s in d.get("sessions_24h") or [] if isinstance(s, dict)]
        await raise_problem(
            session, device, "veeam.job_retry", Severity.WARNING,
            f"Job {job.name}: {retries} retry nelle ultime 24 ore", labels,
            {"job": job.name, "retries": retries, "sessions": sessions[-20:]},
        )
    else:
        await resolve_problem(session, device.id, "veeam.job_retry", labels)

    # Nessun backup riuscito di recente (anche se l'ultimo non è fallito: es. job che non parte più).
    limits = await thresholds_for(session, "veeam.last_success_age_h", device.tenant_id, device.id)
    if limits is None or job.last_success is None:
        await resolve_problem(session, device.id, "veeam.last_success_age_h", labels)
        return
    age_h = (now - job.last_success.astimezone(UTC)).total_seconds() / 3600
    warn, crit = limits
    sev = Severity.CRITICAL if crit is not None and age_h >= crit else Severity.WARNING if warn is not None and age_h >= warn else None
    if sev is None:
        await resolve_problem(session, device.id, "veeam.last_success_age_h", labels)
    else:
        await raise_problem(
            session, device, "veeam.last_success_age_h", sev,
            f"Job {job.name}: nessun backup riuscito da {age_h:.0f} ore", labels,
            {"job": job.name, "last_success": job.last_success.isoformat(), "warn": warn, "crit": crit},
        )


async def _evaluate_license(session: AsyncSession, device: Device, p: VeeamPayload, now: datetime) -> None:
    exp = p.license.expiration if p.license else None
    if exp is None:
        await resolve_problem(session, device.id, "veeam.license")
        return
    days = (exp.astimezone(UTC) - now).total_seconds() / 86400
    if days < 0:
        await raise_problem(session, device, "veeam.license", Severity.CRITICAL,
                            "Licenza Veeam scaduta", details={"expiration": exp.isoformat()})
    elif days < 30:
        await raise_problem(session, device, "veeam.license", Severity.WARNING,
                            f"Licenza Veeam in scadenza tra {days:.0f} giorni", details={"expiration": exp.isoformat()})
    else:
        await resolve_problem(session, device.id, "veeam.license")
