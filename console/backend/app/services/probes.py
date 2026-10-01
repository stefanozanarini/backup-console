"""Output dei probe del pacchetto di raccolta: la console lo interpreta (non l'agente),
così una correzione alla raccolta richiede solo un deploy della console.

Ogni probe ha un parser con lo stesso id. Un probe senza parser (pacchetto più nuovo
della console) viene solo registrato nello stato del dispositivo."""

import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, Severity
from app.schemas.agent import ProbeResultIn, VeeamPayload
from app.services import veeam as veeam_service
from app.services.problems import raise_problem, resolve_problem

LABELS = {"veeam_vbr": "Veeam B&R", "veeam_vaw": "Veeam Agent", "uranium": "Uranium Backup",
          "synology": "Hyper Backup", "hyperbackup": "Hyper Backup", "qnap_codesign": "firma app QTS",
          "nas_discovery": "NAS nella rete"}


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


# --- QNAP: firma digitale obbligatoria per le app ------------------------------------

# Righe di /var/log/log.qpkg (ora locale del NAS, senza anno): "10/01 07:50:01(24415) funzione riga: testo"
_QPKG_LOG = re.compile(r"^(\d\d)/(\d\d) (\d\d):(\d\d):(\d\d)\(\d+\) (\w+) \d+: (.*)$")


def _parse_kv(stdout: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    values: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in stdout.splitlines():
        line = line.rstrip()
        if line.startswith("--"):
            current = sections.setdefault(line[2:], [])
        elif current is not None:
            if line:
                current.append(line)
        elif "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values, sections


def _log_time(m: re.Match, nas_now: datetime, offset: timedelta) -> datetime:
    """Ora UTC di una riga del log: l'anno è quello del NAS, o il precedente se la data è nel futuro."""
    month, day, hh, mm, ss = (int(m.group(i)) for i in range(1, 6))
    local = nas_now.replace(month=month, day=day, hour=hh, minute=mm, second=ss)
    if local > nas_now + timedelta(days=1):
        local = local.replace(year=local.year - 1)
    return local - offset


async def _qnap_codesign(session: AsyncSession, device: Device, r: ProbeResultIn, ts: datetime) -> None:
    values, sections = _parse_kv(r.stdout)
    if "now" not in values:
        raise ProbeOutputError("manca l'ora del NAS")
    try:
        nas_now = datetime.strptime(values["now"], "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        raise ProbeOutputError(f"ora del NAS non valida: {values['now']}") from e
    # Fuso del NAS: differenza dall'avvio del probe (UTC), arrotondata al quarto d'ora.
    started = r.started_at
    utc_now = started.astimezone(UTC).replace(tzinfo=None) if started.tzinfo else started
    offset = timedelta(minutes=round((nas_now - utc_now).total_seconds() / 900) * 15)

    checks: list[datetime] = []
    disables: list[datetime] = []
    for line in sections.get("log", []):
        m = _QPKG_LOG.match(line)
        if not m:
            continue
        at = _log_time(m, nas_now, offset)
        if m.group(6) == "qpkgd_check_code_signing" and "fail" in m.group(7):
            checks.append(at)
        elif m.group(6) == "add_mission" and "[CLI] disable (BackupConsoleAgent)" in m.group(7):
            disables.append(at)
    # Disattivazione dovuta alla firma: segue di pochi secondi un controllo fallito.
    blocked = [d for d in disables if any(timedelta(0) <= d - c <= timedelta(seconds=30) for c in checks)]
    forced = values.get("force_enable_code_signing", "").upper() == "TRUE"
    last_check = max(checks) if checks else None
    details = {
        "force_enable_code_signing": values.get("force_enable_code_signing") or None,
        "last_check": last_check.isoformat() + "Z" if last_check else None,
        "last_disabled": max(blocked).isoformat() + "Z" if blocked else None,
        "disabled_count": len(blocked) or None,
        "qpkg_version": values.get("qpkg_version") or None,
        "settings": "; ".join(sections.get("conf", [])[:5]) or None,
    }
    labels = {"app": "BackupConsoleAgent"}
    if last_check is not None:
        # Il verdetto di QTS conta più delle chiavi: l'ultimo controllo ha disattivato l'agente?
        risk = bool(blocked) and max(blocked) >= last_check
    else:
        risk = forced
    if risk:
        title = ("QTS disattiva l'agente: firma digitale delle app obbligatoria" if blocked
                 else "QTS impone la firma digitale delle app: l'agente verrà disattivato")
        await raise_problem(session, device, "qnap.code_signing", Severity.WARNING, title, labels,
                            {k: v for k, v in details.items() if v is not None})
    else:
        await resolve_problem(session, device.id, "qnap.code_signing", labels)


# --- NAS nella stessa rete ------------------------------------------------------------

_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


async def _nas_discovery(session: AsyncSession, device: Device, r: ProbeResultIn, ts: datetime) -> None:
    _, sections = _parse_kv(r.stdout)
    if "found" not in sections:
        raise ProbeOutputError("manca l'elenco dei NAS trovati")
    networks = []
    for line in sections.get("self", []):
        parts = line.split()
        if len(parts) == 3 and _IPV4.match(parts[1]):
            networks.append({"interface": parts[0][:32], "ip": parts[1], "prefix": int(parts[2]) if parts[2].isdigit() else None})
    arp = {}
    for line in sections.get("arp", []):
        parts = line.split()
        if len(parts) >= 4 and _IPV4.match(parts[0]) and _MAC.match(parts[3].lower()) and parts[3] != "00:00:00:00:00:00":
            arp[parts[0]] = parts[3].lower()
    found: dict[str, dict[str, Any]] = {}
    for line in sections["found"]:
        kind, ip, model, hostname, firmware = (line.split("|") + [""] * 5)[:5]
        if kind not in ("qnap", "synology") or not _IPV4.match(ip):
            continue
        found[ip] = {"ip": ip, "kind": kind, "model": model[:100] or None, "hostname": hostname[:100] or None,
                     "firmware": firmware[:50] or None, "mac": arp.get(ip)}
    device.nas_neighbors = {
        "at": r.started_at.isoformat(), "networks": networks,
        "found": sorted(found.values(), key=lambda f: tuple(int(x) for x in f["ip"].split("."))),
    }


PARSERS: dict[str, Callable[[AsyncSession, Device, ProbeResultIn, datetime], Awaitable[None]]] = {
    "veeam_vbr": _backup_parser("vbr"),
    "veeam_vaw": _backup_parser("vaw"),
    "uranium": _backup_parser("uranium"),
    "qnap_codesign": _qnap_codesign,
    "nas_discovery": _nas_discovery,
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
