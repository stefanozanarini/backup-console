import hashlib

from tests.conftest import as_user, make_csr
from tests.test_agent_flow import setup_site
from tests.test_ops import signed_release


def enroll_body(token: str, host: str) -> dict:
    return {"token": token, "csr_pem": make_csr(), "hostname": host, "platform": "windows",
            "arch": "amd64", "agent_version": "0.1.0", "roles": ["veeam_vbr"]}


async def new_token(client, site_id, **kw):
    r = await client.post(f"/api/v1/sites/{site_id}/enrollment-tokens", json=kw, headers=as_user())
    assert r.status_code == 201, r.text
    return r.json()


async def test_multi_use_token_and_bulk_approve(client):
    _, site_id = await setup_site(client)
    tok = await new_token(client, site_id, note="Rollout GPO sede", max_uses=2, valid_hours=2)
    assert tok["max_uses"] == 2

    ids = []
    for host in ("vbr-01", "vbr-02"):
        r = await client.post("/agent/v1/enroll", json=enroll_body(tok["token"], host))
        assert r.status_code == 200, r.text
        ids.append(r.json()["agent_id"])
    # Esaurito.
    assert (await client.post("/agent/v1/enroll", json=enroll_body(tok["token"], "vbr-03"))).status_code == 401
    # Non più elencato tra i token attivi.
    assert (await client.get(f"/api/v1/sites/{site_id}/enrollment-tokens", headers=as_user())).json() == []

    pending = (await client.get("/api/v1/devices?agent_status=pending", headers=as_user())).json()
    assert {d["agent"]["enrollment_note"] for d in pending} == {"Rollout GPO sede"}

    r = await client.post("/api/v1/agents/approve", json={"agent_ids": ids}, headers=as_user())
    assert r.json() == {"approved": 2}
    assert (await client.get("/api/v1/devices?agent_status=pending", headers=as_user())).json() == []


async def test_revoked_token_is_rejected(client):
    _, site_id = await setup_site(client)
    tok = await new_token(client, site_id, max_uses=10)
    active = (await client.get(f"/api/v1/sites/{site_id}/enrollment-tokens", headers=as_user())).json()
    assert [t["id"] for t in active] == [tok["id"]]
    assert (await client.post(f"/api/v1/enrollment-tokens/{tok['id']}/revoke", headers=as_user())).status_code == 204
    assert (await client.post("/agent/v1/enroll", json=enroll_body(tok["token"], "x"))).status_code == 401


async def upload(client, version: str, files: dict[str, tuple[str, bytes]]):
    manifest, _ = signed_release(version, {k: b for k, (_, b) in files.items()})
    parts = [("manifest", ("manifest.json", manifest))] + [("files", (n, b)) for n, b in files.values()]
    r = await client.post("/api/v1/releases", files=parts, headers=as_user())
    assert r.status_code == 201, r.text


async def test_bootstrap_download_with_enrollment_token(client):
    _, site_id = await setup_site(client)
    exe_old, exe_new, qpkg = b"MZ agent 0.2.0", b"MZ agent 0.3.0", b"#!/bin/sh qpkg"
    await upload(client, "0.2.0", {"windows-amd64": ("bc-agent-windows-amd64.exe", exe_old)})
    await upload(client, "0.3.0", {
        "windows-amd64": ("bc-agent-windows-amd64.exe", exe_new),
        "qpkg-x86_64": ("BackupConsoleAgent_0.3.0_x86_64.qpkg", qpkg),
    })
    tok = await new_token(client, site_id, max_uses=1)

    r = await client.get("/agent/v1/download/windows-amd64", headers={"X-Enroll-Token": tok["token"]})
    assert r.status_code == 200 and r.content == exe_new  # sempre l'ultima versione
    assert r.headers["x-agent-version"] == "0.3.0"
    assert r.headers["x-content-sha256"] == hashlib.sha256(exe_new).hexdigest()
    assert "bc-agent.exe" in r.headers["content-disposition"]
    r = await client.get("/agent/v1/download/qpkg-x86_64", headers={"X-Enroll-Token": tok["token"]})
    assert r.content == qpkg and "BackupConsoleAgent_0.3.0_x86_64.qpkg" in r.headers["content-disposition"]

    # Il download non consuma il token; l'enrollment sì.
    assert (await client.post("/agent/v1/enroll", json=enroll_body(tok["token"], "srv"))).status_code == 200
    r = await client.get("/agent/v1/download/windows-amd64", headers={"X-Enroll-Token": tok["token"]})
    assert r.status_code == 401
    assert (await client.get("/agent/v1/download/windows-amd64", headers={"X-Enroll-Token": "x" * 40})).status_code == 401
    assert (await client.get("/agent/v1/download/windows-amd64")).status_code == 422

    # Download manuale dalla console.
    r = await client.get("/api/v1/releases/0.2.0/assets/windows-amd64", headers=as_user())
    assert r.status_code == 200 and r.content == exe_old


async def test_qpkg_with_wrong_version_is_rejected(client):
    manifest, _ = signed_release("0.4.0", {"qpkg-x86_64": b"q"})
    parts = [("manifest", ("manifest.json", manifest)), ("files", ("BackupConsoleAgent_0.3.9_x86_64.qpkg", b"q"))]
    assert (await client.post("/api/v1/releases", files=parts, headers=as_user())).status_code == 400
