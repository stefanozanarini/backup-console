from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import AuditLog, Event, InventorySnapshot, Metric, Problem, VeeamSession
from app.services import retention
from tests.conftest import as_user
from tests.test_ops import approved_agent, job, veeam_report

NOW = datetime.now(UTC)


def iso(dt):
    return dt.isoformat()


def session(sid, hours_ago, result, state="Stopped", **kw):
    start = NOW - timedelta(hours=hours_ago)
    end = None if state != "Stopped" else start + timedelta(minutes=30)
    return {"id": sid, "start": iso(start), "end": iso(end) if end else None, "result": result, "state": state, **kw}


async def test_veeam_sessions_are_kept(client):
    data, auth = await approved_agent(client, roles=("veeam_vbr",))
    dev = data["device_id"]
    j = job("a", "Success")
    j["details"] = {"history": [
        session("s3", 1, "None", state="Working"),
        session("s2", 25, "Warning", mode="incremental", transferred_bytes=5 << 30, backup_bytes=3 << 30, reason="lento"),
        session("s1", 49, "Failed", retry=True),
    ]}
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([j]))
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([j]))  # nessun duplicato

    r = await client.get(f"/api/v1/veeam/sessions?device_id={dev}&job_key=a&days=10", headers=as_user())
    rows = r.json()
    assert [s["result"] for s in rows] == ["None", "Warning", "Failed"]
    assert rows[1]["mode"] == "incremental" and rows[1]["transferred_bytes"] == 5 << 30 and rows[2]["retry"]
    assert rows[1]["backup_bytes"] == 3 << 30

    # La sessione in corso termina: stessa riga aggiornata.
    j["details"]["history"][0] = session("s3", 1, "Success", mode="full")
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([j]))
    rows = (await client.get(f"/api/v1/veeam/sessions?device_id={dev}", headers=as_user())).json()
    assert len(rows) == 3 and rows[0]["result"] == "Success" and rows[0]["mode"] == "full"

    # Il job sparisce da Veeam (e lo storico dall'agente): le sessioni restano.
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([]))
    assert len((await client.get(f"/api/v1/veeam/sessions?device_id={dev}", headers=as_user())).json()) == 3
    assert (await client.get("/api/v1/veeam/sessions", headers=as_user())).status_code == 400


async def test_vaw_sessions_keyed_by_end(client):
    data, auth = await approved_agent(client, roles=("veeam_agent",))
    end = iso(NOW - timedelta(hours=2))
    j = job("vaw:Backup", "Success")
    j["details"] = {"history": [{"start": None, "end": end, "result": "Success"}]}
    def report():
        r = veeam_report([j])
        r["payload"]["product"] = "vaw"
        return r

    await client.post("/agent/v1/veeam", headers=auth, json=report())
    # Dalla versione successiva dell'agente arriva anche l'avvio: stessa sessione.
    j["details"]["history"][0]["start"] = iso(NOW - timedelta(hours=3))
    await client.post("/agent/v1/veeam", headers=auth, json=report())
    rows = (await client.get(f"/api/v1/veeam/sessions?device_id={data['device_id']}", headers=as_user())).json()
    assert len(rows) == 1 and rows[0]["started_at"] is not None


async def test_retention(client):
    data, auth = await approved_agent(client)
    dev = data["device_id"]
    async with SessionLocal() as s:
        from app.models import Device
        d = await s.get(Device, dev)
        tenant = d.tenant_id
        old = NOW - timedelta(days=100)
        hour = old.replace(minute=0, second=0, microsecond=0)
        # 4 campioni nella stessa ora oltre il dettaglio completo → 1 valore medio.
        for i, v in enumerate((10, 20, 30, 40)):
            s.add(Metric(tenant_id=tenant, device_id=dev, ts=hour + timedelta(minutes=i * 10), name="disk.used_pct",
                         labels={"mount": "/"}, value=v))
        # Recente: resta com'è. Troppo vecchia: cancellata.
        for i in range(3):
            s.add(Metric(tenant_id=tenant, device_id=dev, ts=NOW - timedelta(minutes=i), name="disk.used_pct",
                         labels={"mount": "/"}, value=1))
        s.add(Metric(tenant_id=tenant, device_id=dev, ts=NOW - timedelta(days=800), name="disk.used_pct",
                     labels={"mount": "/"}, value=1))
        # Inventario: 3 nello stesso giorno vecchio → resta l'ultima; molto vecchio → via.
        day = (NOW - timedelta(days=30)).replace(hour=8, minute=0, second=0, microsecond=0)
        for h in range(3):
            s.add(InventorySnapshot(device_id=dev, collected_at=day + timedelta(hours=h), data={"n": h}))
        s.add(InventorySnapshot(device_id=dev, collected_at=NOW - timedelta(days=400), data={"n": "old"}))
        s.add(Event(tenant_id=tenant, device_id=dev, ts=NOW - timedelta(days=91), type="x"))
        s.add(Event(tenant_id=tenant, device_id=dev, ts=NOW, type="y"))
        s.add(AuditLog(ts=NOW - timedelta(days=800), action="old"))
        s.add(VeeamSession(tenant_id=tenant, device_id=dev, product="vbr", job_key="a", job_name="A",
                           session_key="k", ended_at=NOW - timedelta(days=800)))
        await s.commit()

    done = await retention.run()
    assert done["metrics"] == 1 and done["inventory_thinned"] == 2 and done["inventory_deleted"] == 1
    assert done["events"] == 1 and done["veeam_sessions"] == 1 and done["audit_log"] >= 1

    async with SessionLocal() as s:
        vals = (await s.scalars(select(Metric.value).where(Metric.device_id == dev, Metric.ts < NOW - timedelta(days=90)))).all()
        assert vals == [25.0]
        assert await s.scalar(select(func.count()).select_from(Metric).where(Metric.device_id == dev)) == 4
        snaps = (await s.scalars(select(InventorySnapshot.data).where(InventorySnapshot.device_id == dev))).all()
        assert {"n": 2} in snaps and {"n": 0} not in snaps and {"n": "old"} not in snaps
    # Seconda passata: niente da fare, il riassunto è idempotente.
    again = await retention.run()
    assert not any(again.values())
