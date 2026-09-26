"""Valutazione delle soglie e ciclo di vita dei problemi (open → acknowledged → resolved)."""

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CheckRule, Device, Problem, ProblemState, Severity
from app.models.base import utcnow

# Soglie predefinite, usate se non esiste nessuna CheckRule applicabile.
DEFAULT_THRESHOLDS: dict[str, tuple[float | None, float | None]] = {
    "disk.used_pct": (85.0, 95.0),
    "hw.disk_temp_c": (50.0, 60.0),
    "hw.sys_temp_c": (55.0, 70.0),
    "veeam.repo_used_pct": (85.0, 95.0),
    "veeam.last_success_age_h": (36.0, 72.0),
}

# Metriche booleane: 0 = problema.
BOOLEAN_CHECKS: dict[str, tuple[Severity, str]] = {
    "hw.disk_smart_ok": (Severity.CRITICAL, "SMART non OK sul disco {slot}"),
    "raid.healthy": (Severity.CRITICAL, "RAID {array} degradato"),
}

THRESHOLD_TITLES = {
    "disk.used_pct": "Volume {mount} al {value:.0f}%",
    "hw.disk_temp_c": "Temperatura disco {slot}: {value:.0f}°C",
    "hw.sys_temp_c": "Temperatura sistema: {value:.0f}°C",
    "veeam.repo_used_pct": "Repository {repo} al {value:.0f}%",
}


async def thresholds_for(
    session: AsyncSession, check: str, tenant_id: uuid.UUID, device_id: uuid.UUID
) -> tuple[float | None, float | None] | None:
    """Ritorna (warn, crit) con precedenza dispositivo > tenant > globale, None se disabilitato."""
    rows = (
        await session.scalars(
            select(CheckRule).where(
                CheckRule.check == check,
                or_(CheckRule.device_id == device_id, CheckRule.device_id.is_(None)),
                or_(CheckRule.tenant_id == tenant_id, CheckRule.tenant_id.is_(None)),
            )
        )
    ).all()

    def rank(r: CheckRule) -> int:
        return 2 if r.device_id else 1 if r.tenant_id else 0

    if rows:
        best = max(rows, key=rank)
        return (best.warn, best.crit) if best.enabled else None
    return DEFAULT_THRESHOLDS.get(check)


def dedup_key(device_id: uuid.UUID, check: str, labels: dict[str, Any] | None = None) -> str:
    suffix = ",".join(f"{k}={v}" for k, v in sorted((labels or {}).items()))
    return f"{device_id}:{check}:{suffix}"


async def raise_problem(
    session: AsyncSession,
    device: Device,
    check: str,
    severity: Severity,
    title: str,
    labels: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> Problem:
    key = dedup_key(device.id, check, labels)
    now = utcnow()
    problem = await session.scalar(
        select(Problem).where(Problem.dedup_key == key, Problem.state != ProblemState.RESOLVED)
    )
    if problem is None:
        problem = Problem(
            tenant_id=device.tenant_id,
            device_id=device.id,
            check=check,
            dedup_key=key,
            severity=severity,
            title=title,
            details=details or {},
            first_seen=now,
            last_seen=now,
        )
        session.add(problem)
    else:
        # Un peggioramento riapre l'attenzione anche se il problema era già preso in carico.
        if severity == Severity.CRITICAL and problem.severity != Severity.CRITICAL:
            problem.state = ProblemState.OPEN
        problem.severity = severity
        problem.title = title
        problem.details = details or {}
        problem.last_seen = now
    return problem


async def resolve_problem(
    session: AsyncSession, device_id: uuid.UUID, check: str, labels: dict[str, Any] | None = None
) -> None:
    key = dedup_key(device_id, check, labels)
    problem = await session.scalar(
        select(Problem).where(Problem.dedup_key == key, Problem.state != ProblemState.RESOLVED)
    )
    if problem is not None:
        problem.state = ProblemState.RESOLVED
        problem.resolved_at = utcnow()


def _title(template: str, labels: dict[str, Any], value: float) -> str:
    try:
        return template.format(value=value, **labels)
    except (KeyError, IndexError, ValueError):
        return template.split(":")[0]


async def evaluate_metrics(session: AsyncSession, device: Device, metrics: Iterable[Any]) -> None:
    """Metriche con .name, .labels, .value: apre/aggiorna/chiude i problemi relativi."""
    for m in metrics:
        labels = dict(m.labels or {})
        if m.name in BOOLEAN_CHECKS:
            severity, template = BOOLEAN_CHECKS[m.name]
            if m.value < 0.5:
                await raise_problem(
                    session, device, m.name, severity, _title(template, labels, m.value), labels, labels
                )
            else:
                await resolve_problem(session, device.id, m.name, labels)
            continue

        if m.name not in THRESHOLD_TITLES:
            continue
        limits = await thresholds_for(session, m.name, device.tenant_id, device.id)
        if limits is None:
            await resolve_problem(session, device.id, m.name, labels)
            continue
        warn, crit = limits
        severity = None
        if crit is not None and m.value >= crit:
            severity = Severity.CRITICAL
        elif warn is not None and m.value >= warn:
            severity = Severity.WARNING
        if severity is None:
            await resolve_problem(session, device.id, m.name, labels)
        else:
            details = {**labels, "value": m.value, "warn": warn, "crit": crit}
            title = _title(THRESHOLD_TITLES[m.name], labels, m.value)
            await raise_problem(session, device, m.name, severity, title, labels, details)
