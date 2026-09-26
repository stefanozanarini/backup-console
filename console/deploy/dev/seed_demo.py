"""Popola uno stack di sviluppo con tenant, dispositivi e 7 giorni di metriche finte.

Passa dagli endpoint reali (enrollment + mTLS via Caddy), quindi esercita anche il
percorso agente. Da lanciare con il venv del backend:

    console/backend/.venv/bin/python console/deploy/dev/seed_demo.py --ca caddy-root.crt

Richiede BC_AUTH_MODE=dev e un superadmin già creato (default admin@example.com).
"""

import argparse
import math
import random
import ssl
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

TB = 1024**4

# (tenant, sito, hostname, ruoli, piattaforma, modello, os, volumi [(mount, TB, % iniziale, %/giorno)], extra)
DEVICES = [
    ("Studio Alfa", "Sede Bologna", "nas-alfa-01", ["qnap"], "linux", "TS-873A", "QTS 5.2.1 20250115",
     [("/share/CACHEDEV1_DATA", 32, 78, 1.2), ("/share/CACHEDEV2_DATA", 8, 40, 0.1)], {}),
    ("Studio Alfa", "Sede Bologna", "vbr-alfa", ["veeam_vbr"], "windows", "PowerEdge R450", "Windows Server 2022",
     [("C:\\", 0.5, 45, 0.05), ("E:\\", 12, 61, 0.3)], {}),
    ("Studio Alfa", "Filiale Modena", "nas-alfa-mo", ["qnap"], "linux", "TS-464", "QTS 5.1.9",
     [("/share/CACHEDEV1_DATA", 16, 55, 0.2)], {"smart_fail": "3"}),
    ("Officine Beta", "Stabilimento", "nas-beta-01", ["qnap_hero"], "linux", "TS-h1290FX", "QuTS hero h5.2.0",
     [("/share/ZFS1_DATA", 96, 88, 0.15)], {}),
    ("Officine Beta", "Stabilimento", "nas-beta-02", ["qnap"], "linux", "TS-431K", "QTS 5.1.4",
     [("/share/CACHEDEV1_DATA", 12, 52, 0.1)], {"raid_degraded": True}),
    ("Officine Beta", "Uffici", "pc-amministrazione", ["veeam_agent"], "windows", "OptiPlex 7010", "Windows 11 Pro",
     [("C:\\", 0.5, 67, 0.05)], {}),
    ("Logistica Gamma", "Magazzino", "nas-gamma", ["qnap"], "linux", "TS-253D", "QTS 5.0.1",
     [("/share/CACHEDEV1_DATA", 8, 35, 0.05)], {"offline": True}),
    ("Logistica Gamma", "Magazzino", "vbr-gamma", ["veeam_vbr"], "windows", "ProLiant DL20", "Windows Server 2019",
     [("C:\\", 0.25, 58, 0.02), ("D:\\", 6, 72, 0.25)], {"pending": True}),
]


def make_csr() -> tuple[str, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "demo")])
    ).sign(key, hashes.SHA256())
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return csr.public_bytes(serialization.Encoding.PEM).decode(), key_pem


def env(payload) -> dict:
    return {"msg_id": str(uuid.uuid4()), "seq": 1, "ts": datetime.now(UTC).isoformat(), "payload": payload}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--console", default="https://console.localhost:18443")
    ap.add_argument("--agents", default="https://agents.localhost:18443")
    ap.add_argument("--ca", required=True, help="root CA di Caddy (tls internal)")
    ap.add_argument("--user", default="admin@example.com")
    args = ap.parse_args()

    ui = httpx.Client(base_url=f"{args.console}/api/v1", verify=args.ca, headers={"X-Dev-User": args.user})
    tmp = Path(tempfile.mkdtemp(prefix="bc-seed-"))
    rnd = random.Random(42)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    tenants = {t["name"]: t for t in ui.get("/tenants").raise_for_status().json()}
    sites: dict[tuple[str, str], str] = {}
    for tname, sname, *_ in DEVICES:
        if tname not in tenants:
            slug = tname.lower().replace(" ", "-")
            tenants[tname] = ui.post("/tenants", json={"name": tname, "slug": slug}).raise_for_status().json()
        tid = tenants[tname]["id"]
        if (tname, sname) not in sites:
            existing = {s["name"]: s["id"] for s in ui.get(f"/tenants/{tid}/sites").raise_for_status().json()}
            if sname not in existing:
                existing[sname] = ui.post(f"/tenants/{tid}/sites", json={"name": sname}).raise_for_status().json()["id"]
            sites[(tname, sname)] = existing[sname]

    for tname, sname, host, roles, platform, model, osv, volumes, extra in DEVICES:
        token = ui.post(f"/sites/{sites[(tname, sname)]}/enrollment-tokens", json={"note": "demo"}).raise_for_status().json()["token"]
        csr, key_pem = make_csr()
        r = httpx.post(f"{args.agents}/agent/v1/enroll", verify=args.ca, json={
            "token": token, "csr_pem": csr, "hostname": host, "platform": platform,
            "arch": "amd64", "agent_version": "0.1.0-demo", "roles": roles,
        }).raise_for_status().json()
        (tmp / f"{host}.crt").write_text(r["cert_pem"])
        (tmp / f"{host}.key").write_bytes(key_pem)
        if extra.get("pending"):
            print(f"{host}: in attesa di approvazione")
            continue
        ui.post(f"/agents/{r['agent_id']}/approve").raise_for_status()

        tls = ssl.create_default_context(cafile=args.ca)
        tls.load_cert_chain(tmp / f"{host}.crt", tmp / f"{host}.key")
        agent = httpx.Client(base_url=f"{args.agents}/agent/v1", verify=tls)
        if not extra.get("offline"):
            agent.post("/heartbeat", json={"version": "0.1.0-demo", "roles": roles}).raise_for_status()

        vols = []
        for mount, tb, pct0, growth in volumes:
            pct = min(99.0, pct0 + growth * 7)
            total = int(tb * TB)
            vols.append({"mount": mount, "fs_type": "ext4" if platform == "linux" else "NTFS",
                         "total_bytes": total, "free_bytes": int(total * (1 - pct / 100)), "used_pct": pct})
        data = {"system": {"os": platform, "arch": "amd64", "volumes": vols}}
        if roles[0].startswith("qnap"):
            ndisk = 4
            disks = [{"slot": str(i), "model": "WDC WD80EFPX", "temp_c": 34 + i,
                      "smart": "Abnormal" if extra.get("smart_fail") == str(i) else "GOOD"} for i in range(1, ndisk + 1)]
            arrays = [{"name": "md1", "healthy": not extra.get("raid_degraded"),
                       "detail": "[4/3] [UU_U]" if extra.get("raid_degraded") else "[4/4] [UUUU]"}]
            data["qnap"] = {"firmware": osv.split()[-2] if len(osv.split()) > 2 else osv, "build": osv.split()[-1], "disks": disks, "arrays": arrays}
        agent.post("/inventory", json=env({"model": model, "os_version": osv, "serial": f"Q{rnd.randint(10**8, 10**9)}", "data": data})).raise_for_status()

        # 7 giorni di storico orario, un messaggio per giorno.
        for day in range(7, -1, -1):
            metrics = []
            for h in range(24):
                ts = now - timedelta(days=day, hours=23 - h)
                if ts > now:
                    continue
                elapsed = 7 - (now - ts).total_seconds() / 86400
                for mount, _tb, pct0, growth in volumes:
                    v = pct0 + growth * elapsed + 0.4 * math.sin(elapsed * 6.3) + rnd.uniform(-0.15, 0.15)
                    metrics.append({"ts": ts.isoformat(), "name": "disk.used_pct", "labels": {"mount": mount}, "value": round(min(99.5, v), 2)})
            if day == 0 and roles[0].startswith("qnap"):
                ts = now.isoformat()
                metrics.append({"ts": ts, "name": "hw.sys_temp_c", "value": 41})
                for d in data["qnap"]["disks"]:
                    metrics.append({"ts": ts, "name": "hw.disk_temp_c", "labels": {"slot": d["slot"]}, "value": d["temp_c"]})
                    metrics.append({"ts": ts, "name": "hw.disk_smart_ok", "labels": {"slot": d["slot"]}, "value": 0 if d["smart"] == "Abnormal" else 1})
                for a in data["qnap"]["arrays"]:
                    metrics.append({"ts": ts, "name": "raid.healthy", "labels": {"array": a["name"]}, "value": 1 if a["healthy"] else 0})
            if metrics:
                agent.post("/metrics", json=env({"metrics": metrics})).raise_for_status()
        print(f"{host}: ok")

    print(f"certificati demo in {tmp}")


if __name__ == "__main__":
    main()
