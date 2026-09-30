"""Finestra di backup dei PC Windows con Veeam Agent.

Intervalli settimanali nell'ora locale (es. dal giovedì alle 18:00 al lunedì alle 9:00):
durante la finestra l'agente disattiva la sospensione automatica e avvisa l'utente
nell'icona della tray; la console segnala se il PC risulta spento. Impostazione per
tenant, con priorità a quella del dispositivo. La stessa logica di valutazione è
nell'agente (internal/window).
"""

from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.config import settings
from app.models import Device, Tenant

WEEK = 7 * 24 * 60


def applies(device: Device) -> bool:
    """Solo PC Windows con Veeam Agent: i server restano accesi per conto loro."""
    return device.kind == "pc" and device.platform == "windows" and "veeam_agent" in (device.roles or [])


def effective(device: Device, tenant: Tenant | None) -> tuple[dict[str, Any] | None, Literal["device", "tenant", "none"]]:
    if device.backup_window is not None:
        w, source = device.backup_window, "device"
    elif tenant is not None and tenant.backup_window is not None:
        w, source = tenant.backup_window, "tenant"
    else:
        return None, "none"
    if not w.get("enabled", True) or not w.get("intervals"):
        return None, source
    return w, source


def for_agent(device: Device, tenant: Tenant | None) -> dict[str, Any] | None:
    if not applies(device):
        return None
    w, _ = effective(device, tenant)
    if not w:
        return None
    return {"intervals": w["intervals"], "remind_before_minutes": int(w.get("remind_before_minutes", 30))}


def _minute(p: dict[str, Any]) -> int:
    h, m = (int(x) for x in p["time"].split(":"))
    return (int(p["day"]) - 1) * 24 * 60 + h * 60 + m


def active_at(w: dict[str, Any] | None, now: datetime) -> tuple[bool, datetime | None]:
    """now deve essere "aware": si valuta nel fuso delle finestre."""
    if not w:
        return False, None
    local = now.astimezone(ZoneInfo(settings.default_timezone))
    cur = local.weekday() * 24 * 60 + local.hour * 60 + local.minute
    until = None
    for iv in w.get("intervals") or []:
        try:
            s, e = _minute(iv["start"]), _minute(iv["end"])
        except (KeyError, ValueError, TypeError):
            continue
        if s == e:
            continue
        length, elapsed = (e - s) % WEEK, (cur - s) % WEEK
        if elapsed >= length:
            continue
        end = local.replace(second=0, microsecond=0) + timedelta(minutes=length - elapsed)
        until = end if until is None or end > until else until
    return until is not None, until
