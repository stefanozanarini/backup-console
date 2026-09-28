from datetime import UTC, datetime

from app.api.apikeys import new_api_key
from app.db import SessionLocal
from app.models import ApiKey
from tests.conftest import as_user
from tests.test_ops import signed_release


async def make_key(scopes, revoked=False) -> str:
    token, digest = new_api_key()
    async with SessionLocal() as s:
        s.add(ApiKey(name=f"ci-{digest[:6]}", token_hash=digest, scopes=scopes,
                     revoked_at=datetime.now(UTC) if revoked else None))
        await s.commit()
    return token


def release_parts(version: str):
    binary = f"bc-agent {version}".encode()
    manifest, _ = signed_release(version, {"linux-amd64": binary})
    return [("manifest", ("manifest.json", manifest)), ("files", ("bc-agent-linux-amd64", binary))]


async def test_ci_key_uploads_release(client):
    token = await make_key(["releases:upload"])
    r = await client.post("/api/v1/releases", files=release_parts("0.5.0"), headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, r.text
    rel = (await client.get("/api/v1/releases", headers=as_user())).json()
    assert [x["version"] for x in rel] == ["0.5.0"]
    async with SessionLocal() as s:
        from sqlalchemy import select
        key = await s.scalar(select(ApiKey))
        assert key.last_used_at is not None


async def test_key_limits(client):
    no_scope = await make_key([])
    revoked = await make_key(["releases:upload"], revoked=True)
    good = await make_key(["releases:upload"])
    h = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731

    assert (await client.post("/api/v1/releases", files=release_parts("0.6.0"), headers=h(no_scope))).status_code == 403
    assert (await client.post("/api/v1/releases", files=release_parts("0.6.0"), headers=h(revoked))).status_code == 401
    assert (await client.post("/api/v1/releases", files=release_parts("0.6.0"), headers=h("bck_inventata"))).status_code == 401
    # La chiave non vale come utente per il resto dell'API (in dev: niente X-Dev-User → 401).
    assert (await client.get("/api/v1/me", headers=h(good))).status_code == 401
    assert (await client.post("/api/v1/upgrades", json={"version": "0.6.0"}, headers=h(good))).status_code == 401


async def test_superadmin_upload_still_works(client):
    r = await client.post("/api/v1/releases", files=release_parts("0.7.0"), headers=as_user())
    assert r.status_code == 201
