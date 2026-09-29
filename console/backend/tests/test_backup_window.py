from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.db import SessionLocal
from app.models import Agent
from app.services.backup_window import active_at
from app.worker import check_backup_windows
from tests.conftest import as_user
from tests.test_agent_flow import enroll, setup_site

ROME = ZoneInfo("Europe/Rome")
THU_MON = {"enabled": True, "intervals": [{"start": {"day": 4, "time": "18:00"}, "end": {"day": 1, "time": "09:00"}}]}


def rome(s):
    return datetime.fromisoformat(s).replace(tzinfo=ROME)


def test_active_at_across_weekend():
    # Il 1° ottobre 2026 è un giovedì.
    assert active_at(THU_MON, rome("2026-10-01T17:59")) == (False, None)
    ok, until = active_at(THU_MON, rome("2026-10-03T03:00"))
    assert ok and until == rome("2026-10-05T09:00")
    assert active_at(THU_MON, rome("2026-10-05T09:00"))[0] is False
    assert active_at(THU_MON, rome("2026-10-01T18:00"))[0] is True


async def approved(client, site_id, roles, platform):
    data, auth, _, _ = await enroll(client, site_id, roles=roles, platform=platform)
    await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())
    return data, auth


async def test_tenant_window_device_override_and_heartbeat(client):
    tenant_id, site_id = await setup_site(client)
    pc, pc_auth = await approved(client, site_id, ("veeam_agent",), "windows")
    srv, srv_auth = await approved(client, site_id, ("veeam_vbr",), "windows")
    hb = {"version": "0.2.13", "roles": ["veeam_agent"]}

    r = await client.put(f"/api/v1/tenants/{tenant_id}/backup-window", json=THU_MON, headers=as_user())
    assert r.status_code == 200 and r.json()["source"] == "tenant"
    # Il PC eredita dal tenant e riceve gli intervalli con l'heartbeat; il server no.
    resp = (await client.post("/agent/v1/heartbeat", json=hb, headers=pc_auth)).json()
    assert resp["backup_window"] == {"intervals": THU_MON["intervals"]}
    resp = (await client.post("/agent/v1/heartbeat", json={"version": "0.2.13", "roles": ["veeam_vbr"]}, headers=srv_auth)).json()
    assert resp["backup_window"] is None
    st = (await client.get(f"/api/v1/devices/{pc['device_id']}/backup-window", headers=as_user())).json()
    assert st["source"] == "tenant" and st["applies"] and st["device"] is None

    # Il dispositivo ha la priorità: finestra propria, poi disattivata, poi di nuovo ereditata.
    own = {"enabled": True, "intervals": [{"start": {"day": 5, "time": "20:00"}, "end": {"day": 6, "time": "08:00"}}]}
    r = await client.put(f"/api/v1/devices/{pc['device_id']}/backup-window", json=own, headers=as_user())
    assert r.json()["source"] == "device" and r.json()["effective"]["intervals"] == own["intervals"]
    resp = (await client.post("/agent/v1/heartbeat", json=hb, headers=pc_auth)).json()
    assert resp["backup_window"] == {"intervals": own["intervals"]}
    await client.put(f"/api/v1/devices/{pc['device_id']}/backup-window", json={"enabled": False}, headers=as_user())
    assert (await client.post("/agent/v1/heartbeat", json=hb, headers=pc_auth)).json()["backup_window"] is None
    r = await client.put(f"/api/v1/devices/{pc['device_id']}/backup-window", json=None, headers=as_user())
    assert r.json()["source"] == "tenant"

    # Non per i server; intervalli non validi rifiutati.
    r = await client.put(f"/api/v1/devices/{srv['device_id']}/backup-window", json=own, headers=as_user())
    assert r.status_code == 409
    bad = {"intervals": [{"start": {"day": 4, "time": "18:00"}, "end": {"day": 4, "time": "18:00"}}]}
    assert (await client.put(f"/api/v1/tenants/{tenant_id}/backup-window", json=bad, headers=as_user())).status_code == 422
    assert (await client.put(f"/api/v1/tenants/{tenant_id}/backup-window", json={"intervals": [
        {"start": {"day": 8, "time": "18:00"}, "end": {"day": 1, "time": "09:00"}}]}, headers=as_user())).status_code == 422


async def test_pc_off_during_window(client):
    tenant_id, site_id = await setup_site(client)
    pc, pc_auth = await approved(client, site_id, ("veeam_agent",), "windows")
    # Finestra sempre attiva adesso: da un minuto fa a tra un giorno.
    now = datetime.now(ROME)
    start, end = now - timedelta(minutes=1), now + timedelta(days=1)
    always = {"intervals": [{"start": {"day": start.isoweekday(), "time": start.strftime("%H:%M")},
                             "end": {"day": end.isoweekday(), "time": end.strftime("%H:%M")}}]}
    await client.put(f"/api/v1/devices/{pc['device_id']}/backup-window", json=always, headers=as_user())

    await client.post("/agent/v1/heartbeat", json={"version": "0.2.13", "roles": ["veeam_agent"]}, headers=pc_auth)
    assert await check_backup_windows() == 0  # in linea

    async with SessionLocal() as s:
        a = await s.get(Agent, pc["agent_id"])
        a.last_seen_at = datetime.now(UTC) - timedelta(hours=2)
        await s.commit()
    assert await check_backup_windows() == 1
    [p] = (await client.get("/api/v1/problems", headers=as_user())).json()
    assert p["check"] == "pc.off_in_window" and p["title"].startswith("PC spento durante la finestra di backup")

    # Torna in linea: il problema si chiude.
    await client.post("/agent/v1/heartbeat", json={"version": "0.2.13", "roles": ["veeam_agent"]}, headers=pc_auth)
    assert await check_backup_windows() == 0
    assert (await client.get("/api/v1/problems", headers=as_user())).json() == []
