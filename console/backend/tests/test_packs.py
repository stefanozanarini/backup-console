import base64
import json
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from tests.conftest import as_user
from tests.test_agent_flow import envelope
from tests.test_ops import approved_agent

KEY = Ed25519PrivateKey.generate()
PUB = base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def bundle(version):
    raw = json.dumps({"format": 1, "version": version, "files": {"a.ps1": "x"},
                      "probes": [{"id": "veeam_vbr", "interpreter": "powershell", "scripts": ["a.ps1"]}]}).encode()
    return raw, base64.b64encode(KEY.sign(raw)).decode()


async def upload(client, version, sig=None):
    raw, good = bundle(version)
    return await client.post("/api/v1/collector-packs", headers=as_user(),
                             files={"bundle": ("collector-pack.json", raw)}, data={"signature": sig or good})


async def test_upload_activate_and_download(client, monkeypatch):
    monkeypatch.setattr(settings, "release_public_key", PUB)
    _, forged = bundle("9.9.9")
    assert (await upload(client, "1.0.0", sig=forged)).status_code == 400  # firma di un altro contenuto
    r = await upload(client, "1.0.0")
    assert r.status_code == 201 and r.json()["active"] and r.json()["probes"] == ["veeam_vbr"]
    assert (await upload(client, "1.0.0")).status_code == 409
    assert (await upload(client, "1.1.0")).status_code == 201

    data, auth = await approved_agent(client, roles=("veeam_vbr",))
    hb = (await client.post("/agent/v1/heartbeat", json={"version": "0.3.0"}, headers=auth)).json()
    assert hb["collector_pack"]["version"] == "1.1.0"
    d = (await client.get("/agent/v1/collector-pack/1.1.0", headers=auth)).json()
    raw, sig = bundle("1.1.0")
    assert base64.b64decode(d["bundle"]) == raw and d["signature"] == sig

    # Ritorno alla versione precedente.
    assert (await client.post("/api/v1/collector-packs/1.0.0/activate", headers=as_user())).json()["active"]
    hb = (await client.post("/agent/v1/heartbeat", json={"version": "0.3.0"}, headers=auth)).json()
    assert hb["collector_pack"]["version"] == "1.0.0"
    packs = (await client.get("/api/v1/collector-packs", headers=as_user())).json()
    assert [(p["version"], p["active"]) for p in packs] == [("1.1.0", False), ("1.0.0", True)]


def probe(probe_id, stdout="", ok=True, **kw):
    return envelope({"probe": probe_id, "pack_version": "1.0.0", "started_at": datetime.now(UTC).isoformat(),
                     "duration_ms": 1200, "ok": ok, "stdout": stdout, **kw})


VBR_OUT = "﻿AVVISO: modulo\r\n" + json.dumps({
    "product": "vbr", "version": "12.3.2.4854", "errors": [],
    "jobs": [{"id": "0b5c1f3e-2a4d-4c6e-9f10-1a2b3c4d5e6f", "name": "Backup-DC", "type": "Backup", "enabled": True,
              "last_result": "warning", "last_end": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
              "last_success": (datetime.now(UTC) - timedelta(hours=2)).isoformat()}],
    "repositories": [{"name": "NAS", "total_bytes": 1000, "free_bytes": 500}],
    "license": {"edition": "EnterprisePlus", "expiration": (datetime.now(UTC) + timedelta(days=200)).isoformat()},
})


async def test_probe_output_is_parsed_by_console(client):
    data, auth = await approved_agent(client, roles=("veeam_vbr",))
    dev = data["device_id"]
    r = await client.post("/agent/v1/probe", headers=auth, json=probe("veeam_vbr", VBR_OUT, note="storico sessioni non raccolto: timeout"))
    assert r.status_code == 200, r.text
    [j] = (await client.get(f"/api/v1/veeam/jobs?device_id={dev}", headers=as_user())).json()
    assert j["last_result"] == "Warning"  # normalizzato dalla console
    d = (await client.get(f"/api/v1/devices/{dev}", headers=as_user())).json()
    assert d["veeam"]["version"] == "12.3.2.4854" and "storico sessioni non raccolto: timeout" in d["veeam"]["errors"]

    # Probe fallito: problema con l'errore; output illeggibile: idem; poi riuscito: chiuso.
    await client.post("/agent/v1/probe", headers=auth, json=probe("veeam_vbr", ok=False, error="oltre 4m0s", stderr="x"))
    [p] = [p for p in (await client.get("/api/v1/problems", headers=as_user())).json() if p["check"] == "agent.collector"]
    assert p["title"] == "Raccolta dati Veeam B&R non riuscita" and p["details"]["error"] == "oltre 4m0s"
    await client.post("/agent/v1/probe", headers=auth, json=probe("veeam_vbr", "niente json"))
    [p] = [p for p in (await client.get("/api/v1/problems", headers=as_user())).json() if p["check"] == "agent.collector"]
    assert p["details"]["error"].startswith("output non interpretabile")
    await client.post("/agent/v1/probe", headers=auth, json=probe("veeam_vbr", VBR_OUT))
    assert [p for p in (await client.get("/api/v1/problems", headers=as_user())).json() if p["check"] == "agent.collector"] == []

    # Probe di un pacchetto più nuovo della console: registrato, nessun problema.
    await client.post("/agent/v1/probe", headers=auth, json=probe("futuro", "{}"))
    from app.db import SessionLocal
    from app.models import Device
    async with SessionLocal() as s:
        st = (await s.get(Device, dev)).probe_status
    assert st["futuro"]["note"] == "nessun parser in questa versione della console" and st["veeam_vbr"]["ok"]


QNAP_OUT = """now={now}
force_enable_code_signing={force}
enable=TRUE
qpkg_version=0.2.17
--log
{log}
--conf
force_enable_code_signing = {force}
"""


async def test_qnap_code_signing(client):
    from app.models import Device  # noqa: F401
    data, auth = await approved_agent(client, roles=("qnap",))
    nas = datetime.now(UTC) + timedelta(hours=2)  # NAS in ora locale (UTC+2)

    def line(delta_s, text):
        t = nas - timedelta(seconds=delta_s)
        return t.strftime("%m/%d %H:%M:%S") + "(123) " + text

    def out(force, *lines):
        return QNAP_OUT.format(now=nas.strftime("%Y-%m-%d %H:%M:%S"), force=force, log="\n".join(lines))

    async def problems():
        return [p for p in (await client.get("/api/v1/problems", headers=as_user())).json() if p["check"] == "qnap.code_signing"]

    check = 'qpkgd_check_code_signing 2104: echo -n "BackupConsoleAgent" | qsh -0e cs_qdaemon.get_cms_info return fail'
    disable = "add_mission 489: [CLI] disable (BackupConsoleAgent)"

    # Nessun controllo nel log, firma imposta: avviso preventivo.
    await client.post("/agent/v1/probe", headers=auth, json=probe("qnap_codesign", out("TRUE")))
    [p] = await problems()
    assert p["title"].endswith("verrà disattivato") and p["severity"] == "warning"

    # QTS ha disattivato l'agente subito dopo un controllo fallito.
    r = await client.post("/agent/v1/probe", headers=auth, json=probe("qnap_codesign", out("FALSE", line(3600, check), line(3599, disable))))
    assert r.status_code == 200, r.text
    [p] = await problems()
    assert p["title"].startswith("QTS disattiva l'agente") and p["details"]["disabled_count"] == 1
    last = datetime.fromisoformat(p["details"]["last_disabled"])
    assert abs((last - (datetime.now(UTC) - timedelta(seconds=3599))).total_seconds()) < 120  # convertito in UTC

    # Controllo successivo senza disattivazione (opzione abilitata in App Center): risolto.
    await client.post("/agent/v1/probe", headers=auth,
                      json=probe("qnap_codesign", out("FALSE", line(3600, check), line(3599, disable), line(60, check))))
    assert await problems() == []


DISCOVERY_OUT = """now=2026-10-01 18:00:00
--self
qvs0 192.168.50.183 24
--found
qnap|192.168.50.20|TS-453D|NAS-PIANO2|5.2.1
synology|192.168.50.30|||
qnap|192.168.50.40|TS-231|nas-vecchio|4.3.6
qnap|not-an-ip|x|y|z
--arp
IP address       HW type     Flags       HW address            Mask     Device
192.168.50.20    0x1         0x2         24:5E:BE:00:00:01     *        qvs0
192.168.50.30    0x1         0x2         00:11:32:00:00:02     *        qvs0
"""


async def test_nas_discovery(client):
    from app.db import SessionLocal
    from app.models import Device

    data, auth = await approved_agent(client, roles=("qnap",))
    dev = data["device_id"]
    async with SessionLocal() as s:
        me = await s.get(Device, dev)
        common = {"tenant_id": me.tenant_id, "site_id": me.site_id, "kind": me.kind, "platform": "linux", "arch": "x86_64"}
        s.add(Device(name="Piano 2", hostname="qnap-p2", ip_addresses=[{"name": "eth0", "mac": "24:5e:be:00:00:01", "ipv4": ["10.9.9.9"]}], **common))
        s.add(Device(name="NAS vecchio", hostname="NAS-VECCHIO", **common))
        await s.commit()

    r = await client.post("/agent/v1/probe", headers=auth, json=probe("nas_discovery", DISCOVERY_OUT))
    assert r.status_code == 200, r.text
    nb = (await client.get(f"/api/v1/devices/{dev}", headers=as_user())).json()["nas_neighbors"]
    assert nb["networks"] == [{"interface": "qvs0", "ip": "192.168.50.183", "prefix": 24}]
    by_ip = {f["ip"]: f for f in nb["found"]}
    assert list(by_ip) == ["192.168.50.20", "192.168.50.30", "192.168.50.40"]
    assert by_ip["192.168.50.20"]["device_name"] == "Piano 2"  # per MAC
    assert by_ip["192.168.50.40"]["device_name"] == "NAS vecchio"  # per nome host
    assert by_ip["192.168.50.30"]["device_id"] is None and by_ip["192.168.50.30"]["mac"] == "00:11:32:00:00:02"
