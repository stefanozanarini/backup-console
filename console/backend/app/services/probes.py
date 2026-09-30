"""Output dei probe del pacchetto di raccolta: la console lo interpreta (non l'agente),
così una correzione alla raccolta richiede solo un deploy della console.

Ogni probe ha un parser con lo stesso id. Un probe senza parser (pacchetto più nuovo
della console) viene solo registrato nello stato del dispositivo."""

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, Severity
from app.schemas.agent import ProbeResultIn, VeeamPayload
from app.services import veeam as veeam_service
from app.services.problems import raise_problem, resolve_problem

LABELS = {"veeam_vbr": "Veeam B&R", "veeam_vaw": "Veeam Agent", "uranium": "Uranium Backup",
          "synology": "Hyper Backup", "hyperbackup": "Hyper Backup"}


class ProbeOutputError(ValueError):
    """Output del probe non interpretabile."""


def json_output(stdout: str) -> Any:
    # PowerShell può anteporre un BOM o righe di avviso: si parte dalla prima "{".
    i = stdout.find("{")
    if i < 0:
        raise ProbeOutputError("nessun JSON nell'output")
    try:
        return json.loads(stdout[i:])
    except json.JSONDecodeError as e:
        raise ProbeOutputError(f"JSON non valido: {e}") from e


def normalize_result(r: Any) -> str | None:
    """Stessa normalizzazione che faceva l'agente (Success/Warning/Failed/None)."""
    if r is None:
        return "None"
    s = str(r).strip().lower()
    if s in ("success", "succeeded", "ok"):
        return "Success"
    if s == "warning":
        return "Warning"
    if s in ("failed", "failure", "error"):
        return "Failed"
    if s in ("", "none"):
        return "None"
    return str(r)[:32]


def _backup_parser(product: str) -> Callable[[AsyncSession, Device, ProbeResultIn, datetime], Awaitable[None]]:
    async def parse(session: AsyncSession, device: Device, r: ProbeResultIn, ts: datetime) -> None:
        data = json_output(r.stdout)
        if not isinstance(data, dict):
            raise ProbeOutputError("output non è un oggetto JSON")
        data["product"] = product
        data["jobs"] = data.get("jobs") or []
        data["repositories"] = data.get("repositories") or []
        errors = [e for e in (data.get("errors") or []) if isinstance(e, str)]
        if r.note:
            errors.append(r.note)
        data["errors"] = errors[:50]
        for j in data["jobs"]:
            if isinstance(j, dict):
                j["last_result"] = normalize_result(j.get("last_result"))
        try:
            payload = VeeamPayload.model_validate(data)
        except ValidationError as e:
            raise ProbeOutputError(f"dati non validi: {e.errors()[0].get('msg')} in {e.errors()[0].get('loc')}") from e
        await veeam_service.ingest(session, device, payload, ts)

    return parse


PARSERS: dict[str, Callable[[AsyncSession, Device, ProbeResultIn, datetime], Awaitable[None]]] = {
    "veeam_vbr": _backup_parser("vbr"),
    "veeam_vaw": _backup_parser("vaw"),
    "uranium": _backup_parser("uranium"),
}


async def ingest(session: AsyncSession, device: Device, r: ProbeResultIn, ts: datetime) -> None:
    status: dict[str, Any] = {
        "at": r.started_at.isoformat(), "ok": r.ok, "duration_ms": r.duration_ms, "pack": r.pack_version,
        "error": r.error or None, "note": r.note or None,
    }
    labels = {"collector": r.probe}
    label = LABELS.get(r.probe, r.probe)
    problem = None
    if not r.ok:
        problem = {"error": r.error, "stderr": r.stderr[-2000:] or None}
    elif r.truncated:
        problem = {"error": "output del probe troppo grande (troncato dall'agente)"}
    elif (parser := PARSERS.get(r.probe)) is None:
        status["note"] = "nessun parser in questa versione della console"
    else:
        try:
            await parser(session, device, r, ts)
        except ProbeOutputError as e:
            problem = {"error": f"output non interpretabile: {e}", "stdout": r.stdout[:1000]}
    if problem is not None:
        status["ok"] = False
        status["error"] = problem["error"]
        await raise_problem(
            session, device, "agent.collector", Severity.WARNING, f"Raccolta dati {label} non riuscita",
            labels, {"collector": r.probe, "at": r.started_at.isoformat(), "pack": r.pack_version, **problem},
        )
    elif r.probe not in PARSERS:
        pass
    else:
        # Riuscita: il parser (ingest Veeam) ha già chiuso il problema dello stesso collector.
        await resolve_problem(session, device.id, "agent.collector", labels)
    device.probe_status = {**(device.probe_status or {}), r.probe: status}
