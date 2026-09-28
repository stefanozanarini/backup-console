import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import LocalSession, User
from app.models.base import utcnow
from app.services import secrets as secret_store
from app.services import totp
from app.services.passwords import hash_password, verify_password
from tests.conftest import ADMIN

PASSWORD = "una-password-lunga-123"
SECRET = totp.new_secret()


@pytest.fixture(autouse=True)
async def local_mode(monkeypatch, db):
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "local_login", True)
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.email == ADMIN))
        u.password_hash = hash_password(PASSWORD)
        u.totp_secret_enc, u.totp_enabled = secret_store.encrypt(SECRET), True
        await s.commit()


def code(offset: int = 0) -> str:
    return totp.code_at(SECRET, time.time() + offset * totp.STEP)


async def login(client, password=PASSWORD, c=None, email=ADMIN):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password, "code": c or code()})


def test_password_hashing():
    h = hash_password(PASSWORD)
    assert h.startswith("scrypt$") and PASSWORD not in h
    assert verify_password(PASSWORD, h) and not verify_password(PASSWORD + "x", h)
    assert not verify_password(PASSWORD, "rotto")


async def test_login_session_and_logout(client):
    assert (await client.get("/api/v1/auth-config")).json()["local_login"] is True
    r = await login(client)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert token.startswith("bcs_")
    h = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/me", headers=h)).json()["email"] == ADMIN
    assert (await client.post("/api/v1/auth/logout", headers=h)).status_code == 204
    assert (await client.get("/api/v1/me", headers=h)).status_code == 401


async def test_wrong_password_code_and_reuse(client):
    assert (await login(client, password="sbagliata")).status_code == 401
    assert (await login(client, c="000000")).status_code == 401
    assert (await login(client, email="nessuno@example.com")).status_code == 401
    c = code(-1)
    assert (await login(client, c=c)).status_code == 200
    # Stesso codice riusato: rifiutato.
    assert (await login(client, c=c)).status_code == 401


async def test_lockout_after_repeated_failures(client):
    for _ in range(settings.login_max_failures):
        assert (await login(client, password="sbagliata")).status_code == 401
    # Bloccato: anche le credenziali giuste vengono rifiutate.
    r = await login(client)
    assert r.status_code == 429
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.email == ADMIN))
        assert u.locked_until is not None
        u.locked_until = utcnow() - timedelta(seconds=1)
        await s.commit()
    assert (await login(client)).status_code == 200


async def test_expired_session_is_rejected(client):
    token = (await login(client)).json()["token"]
    async with SessionLocal() as s:
        row = await s.scalar(select(LocalSession))
        row.expires_at = utcnow() - timedelta(seconds=1)
        await s.commit()
    r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["detail"] == "sessione scaduta"


async def test_disabled_local_login(client, monkeypatch):
    token = (await login(client)).json()["token"]
    monkeypatch.setattr(settings, "local_login", False)
    assert (await login(client, c=code(1))).status_code == 404
    # Anche le sessioni già aperte smettono di valere.
    assert (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})).status_code == 401
    assert "local_login" in (await client.get("/api/v1/auth-config")).json()


async def test_user_without_totp_cannot_login(client):
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.email == ADMIN))
        u.totp_enabled = False
        await s.commit()
    assert (await login(client)).status_code == 401
