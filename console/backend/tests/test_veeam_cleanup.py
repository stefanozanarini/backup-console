from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models import Command
from tests.conftest import as_user
from tests.test_agent_flow import enroll, setup_site
from tests.test_ops import approved_agent, elevated, veeam_report

OLD = "0b5c1f3e-2a4d-4c6e-9f10-1a2b3c4d5e6f"
ACTIVE = "11111111-2222-3333-4444-555555555555"


def scan_result():
    return {
        "older_than_days": 30,
        "items": [
            {"id": OLD, "name": "Job eliminato", "job": "Vecchio", "state": "orphan", "size_bytes": 2 << 30,
             "last_point": "2026-01-01T00:00:00Z", "candidate": True},
            {"id": ACTIVE, "name": "Backup VM", "job": "VM", "state": "active", "size_bytes": 5 << 30,
             "last_point": datetime.now(UTC).isoformat(), "candidate": False},
        ],
        "repositories": [{"name": "NAS", "total_bytes": 100, "free_bytes": 1}],
        "errors": [],
    }


async def vbr_with_scan(client, slug="acme"):
    _, site_id = await setup_site(client, slug)
    data, auth, _, _ = await enroll(client, site_id, roles=("veeam_vbr",))
    await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([]))
    dev = data["device_id"]
    r = await client.post(f"/api/v1/devices/{dev}/veeam/cleanup-scan", json={"older_than_days": 30}, headers=as_user())
    assert r.status_code == 201, r.text
    scan_id = r.json()["id"]
    cmd = (await client.get("/agent/v1/commands/next?wait=0", headers=auth)).json()
    assert cmd["type"] == "veeam_scan" and cmd["payload"] == {"older_than_days": 30}
    r = await client.post(f"/agent/v1/commands/{scan_id}/result", headers=auth,
                          json={"status": "succeeded", "exit_code": 0, "result": scan_result()})
    assert r.status_code == 204
    return dev, auth, scan_id


async def test_scan_threshold_and_non_vbr(client):
    data, auth = await approved_agent(client)  # QNAP, niente Veeam
    r = await client.post(f"/api/v1/devices/{data['device_id']}/veeam/cleanup-scan", json={}, headers=as_user())
    assert r.status_code == 409
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([]))
    r = await client.post(f"/api/v1/devices/{data['device_id']}/veeam/cleanup-scan", json={"older_than_days": 3}, headers=as_user())
    assert r.status_code == 422


async def test_scan_result_is_stored_and_cleanup_is_restricted(client):
    dev, auth, scan_id = await vbr_with_scan(client)
    detail = (await client.get(f"/api/v1/commands/{scan_id}", headers=as_user())).json()
    assert detail["status"] == "succeeded" and len(detail["result"]["items"]) == 2

    body = {"scan_id": scan_id, "backup_ids": [OLD]}
    # Senza sessione elevata: no.
    assert (await client.post(f"/api/v1/devices/{dev}/veeam/cleanup", json=body, headers=as_user())).status_code == 403
    h = await elevated(client)
    # Un backup di job attivo non è mai eliminabile, anche se lo si chiede.
    r = await client.post(f"/api/v1/devices/{dev}/veeam/cleanup", json={"scan_id": scan_id, "backup_ids": [OLD, ACTIVE]}, headers=h)
    assert r.status_code == 400 and ACTIVE in r.json()["detail"]

    r = await client.post(f"/api/v1/devices/{dev}/veeam/cleanup", json=body, headers=h)
    assert r.status_code == 201, r.text
    cmd = (await client.get("/agent/v1/commands/next?wait=0", headers=auth)).json()
    assert cmd["type"] == "veeam_cleanup"
    assert cmd["payload"]["backup_ids"] == [OLD] and cmd["payload"]["older_than_days"] == 30
    assert cmd["payload"]["total_bytes"] == 2 << 30


async def test_stale_or_foreign_scan_is_rejected(client):
    dev, _, scan_id = await vbr_with_scan(client)
    h = await elevated(client)
    async with SessionLocal() as s:
        c = await s.get(Command, scan_id)
        c.finished_at = datetime.now(UTC) - timedelta(hours=2)
        await s.commit()
    r = await client.post(f"/api/v1/devices/{dev}/veeam/cleanup", json={"scan_id": scan_id, "backup_ids": [OLD]}, headers=h)
    assert r.status_code == 409 and "vecchia" in r.json()["detail"]

    other, _, _ = await vbr_with_scan(client, "beta")
    async with SessionLocal() as s:
        c = await s.get(Command, scan_id)
        c.finished_at = datetime.now(UTC)
        await s.commit()
    # Analisi di un altro dispositivo.
    r = await client.post(f"/api/v1/devices/{other}/veeam/cleanup", json={"scan_id": scan_id, "backup_ids": [OLD]}, headers=h)
    assert r.status_code == 409
