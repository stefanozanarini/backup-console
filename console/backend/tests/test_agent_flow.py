import uuid
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tests.conftest import agent_headers, as_user, make_csr


def now() -> str:
    return datetime.now(UTC).isoformat()


async def setup_site(client, slug="acme"):
    r = await client.post("/api/v1/tenants", json={"name": slug.title(), "slug": slug}, headers=as_user())
    assert r.status_code == 201, r.text
    tenant_id = r.json()["id"]
    r = await client.post(f"/api/v1/tenants/{tenant_id}/sites", json={"name": "Sede"}, headers=as_user())
    assert r.status_code == 201, r.text
    return tenant_id, r.json()["id"]


async def enroll(client, site_id, roles=("qnap",), platform="linux"):
    r = await client.post(f"/api/v1/sites/{site_id}/enrollment-tokens", json={}, headers=as_user())
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    body = {
        "token": token, "csr_pem": make_csr(), "hostname": "nas01", "platform": platform,
        "arch": "amd64", "agent_version": "0.1.0", "roles": list(roles),
    }
    r = await client.post("/agent/v1/enroll", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    return data, agent_headers(data["cert_pem"]), token, body


def envelope(payload):
    return {"msg_id": str(uuid.uuid4()), "seq": 1, "ts": now(), "payload": payload}


def disk(value, mount="/share/CACHEDEV1_DATA"):
    return envelope({"metrics": [{"ts": now(), "name": "disk.used_pct", "labels": {"mount": mount}, "value": value}]})


async def test_enroll_issues_cert_bound_to_agent_id(client):
    _, site_id = await setup_site(client)
    data, _, _, body = await enroll(client, site_id)

    cert = x509.load_pem_x509_certificate(data["cert_pem"].encode())
    assert cert.subject.rfc4514_string() == f"CN={data['agent_id']}"
    assert data["status"] == "pending"

    # Il token è monouso.
    r = await client.post("/agent/v1/enroll", json={**body, "csr_pem": make_csr()})
    assert r.status_code == 401


async def test_pending_agent_cannot_send_data_until_approved(client):
    _, site_id = await setup_site(client)
    data, auth, _, _ = await enroll(client, site_id)

    r = await client.post("/agent/v1/heartbeat", json={"version": "0.1.0"}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert (await client.post("/agent/v1/metrics", json=disk(50), headers=auth)).status_code == 403

    r = await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())
    assert r.status_code == 204
    assert (await client.post("/agent/v1/metrics", json=disk(50), headers=auth)).status_code == 200


async def test_unknown_or_missing_certificate_is_rejected(client):
    _, site_id = await setup_site(client)
    _, auth, _, _ = await enroll(client, site_id)

    r = await client.post("/agent/v1/heartbeat", json={"version": "x"})
    assert r.json()["detail"] == "certificato client mancante"
    # Certificato presente ma verifica di nginx fallita (o assente): rifiutato.
    for verify in ("NONE", "FAILED:certificate has expired", ""):
        r = await client.post("/agent/v1/heartbeat", json={"version": "x"}, headers={**auth, "X-Client-Verify": verify})
        assert r.status_code == 401 and r.json()["detail"] == "certificato client mancante"
    r = await client.post("/agent/v1/heartbeat", json={"version": "x"},
                          headers={"X-Client-Verify": "SUCCESS", "X-Client-Cert": "-----BEGIN CERTIFICATE-----%0Axx"})
    assert r.json()["detail"] == "certificato client non valido"
    # Certificato ben formato ma di un agente mai registrato.
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "estraneo")])
    other = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
             .serial_number(1).not_valid_before(datetime.now(UTC)).not_valid_after(datetime.now(UTC) + timedelta(days=1))
             .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
    r = await client.post("/agent/v1/heartbeat", json={"version": "x"}, headers=agent_headers(other))
    assert r.json()["detail"] == "agente sconosciuto"
    assert (await client.post("/agent/v1/heartbeat", json={"version": "x"}, headers=auth)).status_code == 200


async def test_disk_threshold_opens_escalates_and_resolves_problem(client):
    _, site_id = await setup_site(client)
    data, auth, _, _ = await enroll(client, site_id)
    await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())

    await client.post("/agent/v1/metrics", json=disk(88), headers=auth)
    problems = (await client.get("/api/v1/problems", headers=as_user())).json()
    assert [(p["check"], p["severity"]) for p in problems] == [("disk.used_pct", "warning")]
    assert problems[0]["device_name"] == "nas01"

    # Stessa chiave di dedup: aggiorna la riga, non ne crea un'altra.
    await client.post("/agent/v1/metrics", json=disk(97), headers=auth)
    problems = (await client.get("/api/v1/problems", headers=as_user())).json()
    assert [(p["id"], p["severity"]) for p in problems] == [(problems[0]["id"], "critical")]

    await client.post("/agent/v1/metrics", json=disk(40), headers=auth)
    assert (await client.get("/api/v1/problems", headers=as_user())).json() == []
    resolved = (await client.get("/api/v1/problems?state=resolved", headers=as_user())).json()
    assert len(resolved) == 1


async def test_duplicate_message_is_ignored(client):
    _, site_id = await setup_site(client)
    data, auth, _, _ = await enroll(client, site_id)
    await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())

    msg = disk(97)
    assert (await client.post("/agent/v1/metrics", json=msg, headers=auth)).json() == {"accepted": True, "duplicate": False}
    assert (await client.post("/agent/v1/metrics", json=msg, headers=auth)).json()["duplicate"] is True


async def test_revoked_agent_is_locked_out(client):
    _, site_id = await setup_site(client)
    data, auth, _, _ = await enroll(client, site_id)
    await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())
    await client.post(f"/api/v1/agents/{data['agent_id']}/revoke", headers=as_user())

    r = await client.post("/agent/v1/heartbeat", json={"version": "0.1.0"}, headers=auth)
    assert r.status_code == 403
    # Un agente revocato non può essere riapprovato: serve un nuovo enrollment.
    r = await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())
    assert r.status_code == 409


async def test_tenant_operator_sees_only_own_tenant(client):
    tenant_a, site_a = await setup_site(client, "alfa")
    tenant_b, _ = await setup_site(client, "beta")
    data, auth, _, _ = await enroll(client, site_a)
    await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())
    await client.post("/agent/v1/metrics", json=disk(99), headers=auth)

    r = await client.post("/api/v1/users", headers=as_user(), json={
        "email": "op-beta@example.com", "roles": [{"role": "operator", "tenant_id": tenant_b}],
    })
    assert r.status_code == 201, r.text
    op = as_user("op-beta@example.com")

    assert [t["slug"] for t in (await client.get("/api/v1/tenants", headers=op)).json()] == ["beta"]
    assert (await client.get("/api/v1/problems", headers=op)).json() == []
    assert (await client.get(f"/api/v1/devices/{data['device_id']}", headers=op)).status_code == 404
    assert (await client.post(f"/api/v1/tenants/{tenant_a}/sites", json={"name": "x"}, headers=op)).status_code == 404

    # L'amministratore vede tutto in un'unica lista.
    summary = (await client.get("/api/v1/problems/summary", headers=as_user())).json()
    assert [(s["tenant_name"], s["critical"]) for s in summary] == [("Alfa", 1)]


async def test_inventory_updates_device(client):
    _, site_id = await setup_site(client)
    data, auth, _, _ = await enroll(client, site_id)
    await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())

    inv = envelope({"model": "TS-873A", "os_version": "QTS 5.2.1", "data": {"volumes": [{"name": "DataVol1"}]}})
    assert (await client.post("/agent/v1/inventory", json=inv, headers=auth)).status_code == 200
    device = (await client.get(f"/api/v1/devices/{data['device_id']}", headers=as_user())).json()
    assert device["model"] == "TS-873A" and device["kind"] == "nas"
    assert device["inventory"]["data"]["volumes"][0]["name"] == "DataVol1"

    # Nome modificabile in console; l'hostname resta quello del sistema e segue l'inventario.
    r = await client.patch(f"/api/v1/devices/{data['device_id']}", json={"name": "  NAS   ufficio  "}, headers=as_user())
    assert r.status_code == 200 and r.json()["name"] == "NAS ufficio"
    await client.post("/agent/v1/inventory", json=envelope({"hostname": "nas-new", "data": {}}), headers=auth)
    device = (await client.get(f"/api/v1/devices/{data['device_id']}", headers=as_user())).json()
    assert device["name"] == "NAS ufficio" and device["hostname"] == "nas-new"
    assert (await client.patch(f"/api/v1/devices/{data['device_id']}", json={"name": "   "}, headers=as_user())).status_code == 422
    assert device["supremo_id"] is None

    # ID di Supremo dall'inventario; un agente vecchio (senza remote_access) non lo cancella.
    # Stessa struttura dell'agente: dati raggruppati per collector.
    nics = [{"name": "Ethernet", "mac": "00:15:5d:01:02:03", "ipv4": ["192.168.50.10"]}]
    inv = envelope({"data": {"system": {"remote_access": {"supremo_id": "431202296"}, "network": nics}}})
    await client.post("/agent/v1/inventory", json=inv, headers=auth)
    await client.post("/agent/v1/inventory", json=envelope({"data": {"system": {}}}), headers=auth)
    [d] = (await client.get("/api/v1/devices", headers=as_user())).json()
    assert d["supremo_id"] == "431202296" and d["ip_addresses"] == nics
    # Supremo disinstallato: remote_access vuoto.
    await client.post("/agent/v1/inventory", json=envelope({"data": {"system": {"remote_access": {}}}}), headers=auth)
    assert (await client.get(f"/api/v1/devices/{data['device_id']}", headers=as_user())).json()["supremo_id"] is None


async def test_offline_worker_skips_pcs(client):
    from app.worker import check_offline_agents

    _, site_id = await setup_site(client)
    nas, _, _, _ = await enroll(client, site_id, roles=("qnap",))
    pc, _, _, _ = await enroll(client, site_id, roles=("veeam_agent",))
    for a in (nas, pc):
        await client.post(f"/api/v1/agents/{a['agent_id']}/approve", headers=as_user())

    assert await check_offline_agents() == 1
    problems = (await client.get("/api/v1/problems", headers=as_user())).json()
    assert [(p["check"], p["device_id"]) for p in problems] == [("agent.offline", nas["device_id"])]


async def test_auth_config_is_public(client):
    r = await client.get("/api/v1/auth-config")
    assert r.status_code == 200 and r.json()["mode"] == "dev"


async def test_metrics_history_grouped_by_labels(client):
    _, site_id = await setup_site(client)
    data, auth, _, _ = await enroll(client, site_id)
    await client.post(f"/api/v1/agents/{data['agent_id']}/approve", headers=as_user())
    for v in (40, 50):
        await client.post("/agent/v1/metrics", json=disk(v), headers=auth)
    await client.post("/agent/v1/metrics", json=disk(10, mount="/share/USB"), headers=auth)

    r = await client.get(f"/api/v1/devices/{data['device_id']}/metrics?name=disk.used_pct", headers=as_user())
    series = {s["labels"]["mount"]: s["points"] for s in r.json()}
    assert [p[1] for p in series["/share/CACHEDEV1_DATA"]] == [45.0]  # media oraria
    assert [p[1] for p in series["/share/USB"]] == [10.0]


async def test_pending_filter_and_problem_device_filter(client):
    _, site_id = await setup_site(client)
    a, _, _, _ = await enroll(client, site_id)
    b, auth_b, _, _ = await enroll(client, site_id)
    await client.post(f"/api/v1/agents/{b['agent_id']}/approve", headers=as_user())
    await client.post("/agent/v1/metrics", json=disk(99), headers=auth_b)

    pending = (await client.get("/api/v1/devices?agent_status=pending", headers=as_user())).json()
    assert [d["id"] for d in pending] == [a["device_id"]]
    assert (await client.get(f"/api/v1/problems?device_id={a['device_id']}", headers=as_user())).json() == []
    assert len((await client.get(f"/api/v1/problems?device_id={b['device_id']}", headers=as_user())).json()) == 1


async def test_client_cert_headers_require_proxy_secret(client, monkeypatch):
    from app.config import settings

    _, site_id = await setup_site(client)
    _, auth, _, _ = await enroll(client, site_id)
    monkeypatch.setattr(settings, "proxy_secret", "s3greto-condiviso")
    # Un container sulla rete "proxy" che chiama l'api direttamente, senza passare da nginx.
    r = await client.post("/agent/v1/heartbeat", json={"version": "x"}, headers=auth)
    assert r.json()["detail"] == "certificato client mancante"
    r = await client.post("/agent/v1/heartbeat", json={"version": "x"}, headers={**auth, "X-Proxy-Secret": "sbagliato"})
    assert r.status_code == 401
    r = await client.post("/agent/v1/heartbeat", json={"version": "x"}, headers={**auth, "X-Proxy-Secret": "s3greto-condiviso"})
    assert r.status_code == 200
