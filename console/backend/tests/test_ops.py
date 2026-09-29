import base64
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services import totp
from tests.conftest import as_user
from tests.test_agent_flow import enroll, envelope, setup_site


def test_totp_rfc6238_vector():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(secret, 59) == "287082"
    assert totp.code_at(secret, 1111111109) == "081804"
    assert totp.verify(secret, "287082", now=59)
    assert not totp.verify(secret, "287083", now=59)
    counter = totp.accept(secret, "287082", None, now=59)
    assert counter == 1
    assert totp.accept(secret, "287082", counter, now=59) is None


async def approved_agent(client, roles=("qnap",)):
    _, site_id = await setup_site(client)
    data, auth, _, _ = await enroll(client, site_id, roles=roles)
    await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())
    return data, auth


async def elevated(client) -> dict[str, str]:
    setup = (await client.post("/api/v1/me/totp/setup", headers=as_user())).json()
    now = time.time()
    code = totp.code_at(setup["secret"], now - totp.STEP)  # intervallo precedente, ancora accettato
    assert (await client.post("/api/v1/me/totp/confirm", json={"code": code}, headers=as_user())).status_code == 204
    assert (await client.get("/api/v1/me", headers=as_user())).json()["totp_enabled"] is True
    bad = await client.post("/api/v1/elevate", json={"code": "000000", "reason": "test"}, headers=as_user())
    assert bad.status_code == 403
    # Lo stesso codice non può essere riusato.
    reuse = await client.post("/api/v1/elevate", json={"code": code, "reason": "test"}, headers=as_user())
    assert reuse.status_code == 403
    code = totp.code_at(setup["secret"], now)
    r = await client.post("/api/v1/elevate", json={"code": code, "reason": "ticket 42"}, headers=as_user())
    assert r.status_code == 200, r.text
    return {**as_user(), "X-Elevation": r.json()["token"]}


async def test_exec_command_lifecycle(client):
    data, auth = await approved_agent(client)
    dev = data["device_id"]
    body = {"command": "df -h", "timeout_s": 60}

    r = await client.post(f"/api/v1/devices/{dev}/commands", json=body, headers=as_user())
    assert r.status_code == 403 and r.json()["detail"] == "elevation_required"

    h = await elevated(client)
    r = await client.post(f"/api/v1/devices/{dev}/commands", json=body, headers=h)
    assert r.status_code == 201, r.text
    cmd_id = r.json()["id"]
    assert r.json()["reason"] == "ticket 42"

    r = await client.get("/agent/v1/commands/next?wait=0", headers=auth)
    assert r.status_code == 200 and r.json()["id"] == cmd_id
    assert r.json()["payload"] == {"command": "df -h", "shell": "default"}
    assert (await client.get("/agent/v1/commands/next?wait=0", headers=auth)).status_code == 204

    await client.post(f"/agent/v1/commands/{cmd_id}/output", json={"data": "Filesystem  Size\n"}, headers=auth)
    await client.post(f"/agent/v1/commands/{cmd_id}/output", json={"data": "/dev/md1    10T\n"}, headers=auth)
    r = await client.post(f"/agent/v1/commands/{cmd_id}/result", json={"status": "succeeded", "exit_code": 0}, headers=auth)
    assert r.status_code == 204
    # Un comando concluso non accetta altro output.
    assert (await client.post(f"/agent/v1/commands/{cmd_id}/output", json={"data": "x"}, headers=auth)).status_code == 409

    cmd = (await client.get(f"/api/v1/commands/{cmd_id}", headers=as_user())).json()
    assert cmd["status"] == "succeeded" and cmd["exit_code"] == 0
    assert cmd["output"] == "Filesystem  Size\n/dev/md1    10T\n"
    assert [c["id"] for c in (await client.get(f"/api/v1/devices/{dev}/commands", headers=as_user())).json()] == [cmd_id]


async def test_exec_refused_when_disabled_on_device(client):
    data, auth = await approved_agent(client)
    await client.post("/agent/v1/heartbeat", json={"version": "0.1.0", "allow_shell": False}, headers=auth)
    h = await elevated(client)
    r = await client.post(f"/api/v1/devices/{data['device_id']}/commands", json={"command": "id"}, headers=h)
    assert r.status_code == 409


def signed_release(version: str, platforms: dict[str, bytes]):
    key = Ed25519PrivateKey.generate()
    assets = {
        p: {"sha256": hashlib.sha256(b).hexdigest(), "size": len(b), "signature": base64.b64encode(key.sign(b)).decode()}
        for p, b in platforms.items()
    }
    pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return json.dumps({"version": version, "assets": assets}).encode(), pub


async def test_release_upload_and_upgrade_flow(client):
    data, auth = await approved_agent(client)  # linux/amd64, versione 0.1.0
    binary = b"\x7fELF fake agent 0.2.0"
    manifest, _ = signed_release("0.2.0", {"linux-amd64": binary})
    files = [("manifest", ("manifest.json", manifest)), ("files", ("bc-agent-linux-amd64", binary))]
    r = await client.post("/api/v1/releases", files=files, data={"notes": "test"}, headers=as_user())
    assert r.status_code == 201, r.text
    assert (await client.post("/api/v1/releases", files=files, headers=as_user())).status_code == 409

    tampered = [("manifest", ("manifest.json", manifest)), ("files", ("bc-agent-linux-amd64", binary + b"!"))]
    manifest_b, _ = signed_release("0.2.1", {"linux-amd64": binary})
    tampered[0] = ("manifest", ("manifest.json", manifest_b))
    assert (await client.post("/api/v1/releases", files=tampered, headers=as_user())).status_code == 400

    r = await client.post("/api/v1/upgrades", json={"version": "0.2.0"}, headers=as_user())
    assert r.json() == {"queued": 1, "skipped": {}}
    assert (await client.post("/api/v1/upgrades", json={"version": "0.2.0"}, headers=as_user())).json()["queued"] == 0

    cmd = (await client.get("/agent/v1/commands/next?wait=0", headers=auth)).json()
    assert cmd["type"] == "upgrade" and cmd["payload"]["sha256"] == hashlib.sha256(binary).hexdigest()
    dl = await client.get(cmd["payload"]["path"], headers=auth)
    assert dl.status_code == 200 and dl.content == binary

    await client.post(f"/agent/v1/commands/{cmd['id']}/result", json={"status": "succeeded", "exit_code": 0}, headers=auth)
    assert (await client.get(f"/api/v1/commands/{cmd['id']}", headers=as_user())).json()["status"] == "running"
    await client.post("/agent/v1/heartbeat", json={"version": "0.2.0"}, headers=auth)
    assert (await client.get(f"/api/v1/commands/{cmd['id']}", headers=as_user())).json()["status"] == "succeeded"
    rel = (await client.get("/api/v1/releases", headers=as_user())).json()
    assert rel[0]["version"] == "0.2.0" and rel[0]["agents"] == 1


async def test_upgrade_rollback_event_raises_problem(client):
    data, auth = await approved_agent(client)
    ev = envelope({"events": [{"ts": datetime.now(UTC).isoformat(), "type": "agent.upgrade_rollback",
                               "severity": "warning", "data": {"from": "0.1.0", "to": "0.2.0"}}]})
    await client.post("/agent/v1/events", json=ev, headers=auth)
    p = (await client.get("/api/v1/problems", headers=as_user())).json()
    assert [x["check"] for x in p] == ["agent.upgrade"]


def veeam_report(jobs, repos=(), license_days=365):
    return envelope({
        "product": "vbr", "version": "12.3.1.1139", "jobs": jobs, "repositories": list(repos),
        "license": {"edition": "Enterprise", "expiration": (datetime.now(UTC) + timedelta(days=license_days)).isoformat()},
    })


def job(id_, result, success_hours_ago=2, enabled=True):
    now = datetime.now(UTC)
    return {"id": id_, "name": f"Job {id_}", "type": "Backup", "enabled": enabled, "last_result": result,
            "last_end": (now - timedelta(hours=1)).isoformat(),
            "last_success": (now - timedelta(hours=success_hours_ago)).isoformat()}


async def test_veeam_ingest_and_problems(client):
    data, auth = await approved_agent(client, roles=("veeam_vbr",))
    repos = [{"name": "Repo NAS", "total_bytes": 1000, "free_bytes": 30}]
    r = await client.post("/agent/v1/veeam", headers=auth,
                          json=veeam_report([job("a", "Success"), job("b", "Failed", 100), job("c", "Warning")], repos, 10))
    assert r.status_code == 200, r.text

    problems = {(p["check"], p["severity"]) for p in (await client.get("/api/v1/problems", headers=as_user())).json()}
    assert problems == {
        ("veeam.job_result", "critical"), ("veeam.job_result", "warning"),
        ("veeam.last_success_age_h", "critical"), ("veeam.repo_used_pct", "critical"), ("veeam.license", "warning"),
    }
    jobs = (await client.get(f"/api/v1/veeam/jobs?device_id={data['device_id']}", headers=as_user())).json()
    assert [(j["name"], j["last_result"]) for j in jobs] == [("Job a", "Success"), ("Job b", "Failed"), ("Job c", "Warning")]
    assert (await client.get("/api/v1/veeam/jobs?result=failed", headers=as_user())).json()[0]["name"] == "Job b"

    # Tutto a posto, e il job "c" è stato eliminato in Veeam.
    repos_ok = [{"name": "Repo NAS", "total_bytes": 1000, "free_bytes": 600}]
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([job("a", "Success"), job("b", "Success")], repos_ok))
    assert (await client.get("/api/v1/problems", headers=as_user())).json() == []
    assert len((await client.get("/api/v1/veeam/jobs", headers=as_user())).json()) == 2
    device = (await client.get(f"/api/v1/devices/{data['device_id']}", headers=as_user())).json()
    assert device["veeam"]["version"] == "12.3.1.1139"


async def test_veeam_messages_and_retries(client):
    _, auth = await approved_agent(client, roles=("veeam_vbr",))
    warn = job("w", "Warning")
    warn["details"] = {"messages": [
        {"object": "SQL01", "status": "warning", "text": "Unable to truncate Microsoft SQL Server transaction logs."},
        {"object": None, "status": "info", "text": ""},  # scartato: senza testo
    ]}
    retried = job("r", "Success")
    retried["details"] = {"retries_24h": 1, "sessions_24h": [
        {"start": "2026-09-28T20:00:00Z", "result": "Failed", "retry": False, "reason": "Connection lost"},
        {"start": "2026-09-28T20:15:00Z", "result": "Success", "retry": True, "reason": None},
    ]}
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([warn, retried]))
    problems = {p["check"]: p for p in (await client.get("/api/v1/problems", headers=as_user())).json()}
    assert set(problems) == {"veeam.job_result", "veeam.job_retry"}
    assert problems["veeam.job_result"]["details"]["messages"] == [warn["details"]["messages"][0]]
    r = problems["veeam.job_retry"]
    assert r["severity"] == "warning" and r["title"] == "Job Job r: 1 retry nelle ultime 24 ore"
    assert [s["result"] for s in r["details"]["sessions"]] == ["Failed", "Success"]

    # Niente più retry nelle ultime 24 ore: il problema si chiude.
    retried["details"] = {"retries_24h": 0, "sessions_24h": []}
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([job("w", "Success"), retried]))
    assert (await client.get("/api/v1/problems", headers=as_user())).json() == []


async def test_running_job_keeps_last_completed_result(client):
    data, auth = await approved_agent(client, roles=("veeam_vbr",))
    # Sessione in corso: l'agente riporta l'esito dell'ultima terminata e l'ultimo successo.
    running = job("n", "Warning", success_hours_ago=34)
    running["details"] = {"running": True, "progress_pct": 85}
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([running]))
    [j] = (await client.get(f"/api/v1/veeam/jobs?device_id={data['device_id']}", headers=as_user())).json()
    assert j["last_result"] == "Warning" and j["last_success"] is not None
    assert j["site_name"]
    assert j["details"]["running"] is True and j["details"]["progress_pct"] == 85


async def test_disabled_job_is_ignored(client):
    _, auth = await approved_agent(client, roles=("veeam_vbr",))
    await client.post("/agent/v1/veeam", headers=auth, json=veeam_report([job("x", "Failed", 500, enabled=False)]))
    assert (await client.get("/api/v1/problems", headers=as_user())).json() == []


async def test_worker_expires_stale_commands(client):
    from app.db import SessionLocal
    from app.models import Command
    from app.worker import expire_commands

    data, _ = await approved_agent(client)
    h = await elevated(client)
    cmd_id = (await client.post(f"/api/v1/devices/{data['device_id']}/commands", json={"command": "id"}, headers=h)).json()["id"]
    async with SessionLocal() as s:
        c = await s.get(Command, cmd_id)
        c.created_at = datetime.now(UTC) - timedelta(hours=2)
        await s.commit()
    assert await expire_commands() == 1
    assert (await client.get(f"/api/v1/commands/{cmd_id}", headers=as_user())).json()["status"] == "expired"
