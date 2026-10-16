from datetime import UTC, datetime, timedelta

from tests.conftest import as_user
from tests.test_agent_flow import envelope
from tests.test_ops import approved_agent, job, veeam_report

NOW = datetime.now(UTC)


def uranium_report(jobs):
    return envelope({"product": "uranium", "version": "9.9.0.7469", "jobs": jobs, "errors": [],
                     "service": {"installed": False, "status": None}})


def weekly(result="Warning", days_since_success=8):
    end = NOW - timedelta(days=days_since_success)
    return {
        "id": "uranium:BkpSet00.bks", "name": "salvat", "type": "UraniumBackupSet", "enabled": True,
        "last_result": result, "last_end": end.isoformat(), "last_success": end.isoformat(),
        "details": {
            "expected_interval_h": 168.0,
            "messages": [{"object": "\\\\192.168.0.8\\Archivio_user", "status": "warning", "text": "Nessun file copiato"}],
            "history": [{"id": "BkpSet00.bks\\127", "start": (end - timedelta(minutes=29)).isoformat(),
                         "end": end.isoformat(), "result": result, "backup_bytes": 64981462456,
                         "reason": "Backup completato con successo"}],
        },
    }


async def test_uranium_jobs_problems_and_history(client):
    data, auth = await approved_agent(client, roles=("veeam_vbr",))
    dev = data["device_id"]
    # Server con Veeam B&R e Uranium: i due report non si sovrascrivono.
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([job("v1", "Success")]))
    r = await client.post("/agent/v1/uranium", headers=auth, json=uranium_report([weekly()]))
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/v1/devices/{dev}", headers=as_user())).json()
    assert d["veeam"]["product"] == "vbr" and d["backup_apps"]["uranium"]["service"]["installed"] is False

    jobs = {j["job_key"]: j for j in (await client.get(f"/api/v1/veeam/jobs?device_id={dev}", headers=as_user())).json()}
    assert set(jobs) == {"v1", "uranium:BkpSet00.bks"} and jobs["uranium:BkpSet00.bks"]["product"] == "uranium"

    # Job settimanale: 8 giorni senza successo non superano la soglia adattata (252 ore);
    # l'esito con avvisi apre il problema, con il dettaglio dell'elemento.
    problems = {p["check"]: p for p in (await client.get("/api/v1/problems", headers=as_user())).json()}
    assert set(problems) == {"veeam.job_result"}
    assert problems["veeam.job_result"]["details"]["messages"][0]["text"] == "Nessun file copiato"

    sessions = (await client.get(f"/api/v1/veeam/sessions?device_id={dev}&job_key=uranium:BkpSet00.bks", headers=as_user())).json()
    assert len(sessions) == 1 and sessions[0]["backup_bytes"] == 64981462456 and sessions[0]["product"] == "uranium"

    # Oltre due volte e mezza l'intervallo abituale: critico.
    await client.post("/agent/v1/uranium", headers=auth, json=uranium_report([weekly("Success", 20)]))
    problems = {p["check"]: p for p in (await client.get("/api/v1/problems", headers=as_user())).json()}
    assert problems["veeam.last_success_age_h"]["severity"] == "critical"

    # Rilancio non disponibile per Uranium; prodotti sugli endpoint sbagliati rifiutati.
    r = await client.post(f"/api/v1/devices/{dev}/veeam/jobs/start", json={"job_key": "uranium:BkpSet00.bks"}, headers=as_user())
    assert r.status_code == 409
    assert (await client.post("/agent/v1/veeam", headers=auth, json=uranium_report([]))).status_code == 422
    assert (await client.post("/agent/v1/uranium", headers=auth, json=veeam_report([]))).status_code == 422


async def test_new_roles_are_accepted(client):
    _, auth = await approved_agent(client)
    hb = {"version": "0.2.18", "roles": ["uranium", "system", "prodotto_futuro"]}
    assert (await client.post("/agent/v1/heartbeat", json=hb, headers=auth)).status_code == 200
    bad = {"version": "0.2.18", "roles": ["Nome Non Valido"]}
    assert (await client.post("/agent/v1/heartbeat", json=bad, headers=auth)).status_code == 422


async def test_hyperbackup(client):
    data, auth = await approved_agent(client, roles=("synology",))
    end = NOW - timedelta(hours=20)
    task = {"id": "hyperbackup:task_1", "name": "BackupNASAzureBlob", "type": "HyperBackup", "enabled": True,
            "last_result": "Success", "last_end": end.isoformat(), "last_success": end.isoformat(),
            "transferred_bytes": 7593855507,
            "details": {"integrity_check": {"result": "Success"}, "expected_interval_h": 24.0,
                        "history": [{"id": "task_1:1", "end": end.isoformat(), "result": "Success"}]}}
    report = envelope({"product": "hyperbackup", "version": "4.1.2-4039", "jobs": [task], "errors": []})
    assert (await client.post("/agent/v1/hyperbackup", headers=auth, json=report)).status_code == 200
    d = (await client.get(f"/api/v1/devices/{data['device_id']}", headers=as_user())).json()
    assert d["kind"] == "nas" and d["backup_apps"]["hyperbackup"]["version"] == "4.1.2-4039" and d["veeam"] == {}
    [j] = (await client.get(f"/api/v1/veeam/jobs?device_id={data['device_id']}", headers=as_user())).json()
    assert j["product"] == "hyperbackup" and j["name"] == "BackupNASAzureBlob"
    assert (await client.get("/api/v1/problems", headers=as_user())).json() == []
    bad = envelope({"product": "hyperbackup", "jobs": [], "errors": []})
    assert (await client.post("/agent/v1/veeam", headers=auth, json=bad)).status_code == 422


async def test_hide_uranium_problems(client):
    from app.db import SessionLocal
    from app.models import Device, Severity
    from app.services.problems import raise_problem

    data, _ = await approved_agent(client, roles=("uranium",))
    async with SessionLocal() as s:
        d = await s.get(Device, data["device_id"])
        await raise_problem(s, d, "veeam.job_result", Severity.CRITICAL, "Uranium fallito", {"job": "u"}, {"job_key": "uranium:Set1.bks"})
        await raise_problem(s, d, "agent.collector", Severity.WARNING, "Raccolta Uranium", {"collector": "uranium"}, {"collector": "uranium"})
        await raise_problem(s, d, "veeam.job_result", Severity.CRITICAL, "VBR fallito", {"job": "v"}, {"job_key": "abc"})
        await s.commit()
    dev = data["device_id"]
    mine = lambda rows: sorted(p["title"] for p in rows if p["device_id"] == dev)  # noqa: E731
    assert mine((await client.get("/api/v1/problems", headers=as_user())).json()) == ["Raccolta Uranium", "Uranium fallito", "VBR fallito"]
    assert mine((await client.get("/api/v1/problems?hide=uranium", headers=as_user())).json()) == ["VBR fallito"]
    full = {r["tenant_id"]: r for r in (await client.get("/api/v1/problems/summary", headers=as_user())).json()}
    hidden = {r["tenant_id"]: r for r in (await client.get("/api/v1/problems/summary?hide=uranium", headers=as_user())).json()}
    t = next(k for k, r in full.items() if r["critical"] == 2 and r["warning"] == 1)
    assert hidden[t]["critical"] == 1 and hidden[t]["warning"] == 0
    assert (await client.get("/api/v1/problems?hide=altro", headers=as_user())).status_code == 422
