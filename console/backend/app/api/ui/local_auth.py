"""Login con utente locale (password + TOTP), attivo solo con BC_LOCAL_LOGIN=true.

Pensato come soluzione temporanea finché il login Entra ID non è operativo.
Le password si impostano solo da CLI: python -m app.cli set-password <email>
"""

import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent.routes import client_ip
from app.auth import LOCAL_TOKEN_PREFIX, CurrentUserDep, hash_session_token
from app.config import settings
from app.db import get_session
from app.models import LocalSession, User
from app.models.base import utcnow
from app.services import secrets as secret_store
from app.services import totp
from app.services.audit import audit
from app.services.passwords import DUMMY_HASH, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["console"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=8)


class LoginOut(BaseModel):
    token: str
    expires_at: str


INVALID = "credenziali o codice non validi"


@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn, session: SessionDep, request: Request):
    if not settings.local_login:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "login locale disabilitato")
    now = utcnow()
    ip = client_ip(request)
    user = await session.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))

    if user is not None and user.locked_until and user.locked_until > now:
        audit(session, "login.locked", user_id=user.id, ip=ip)
        await session.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "troppi tentativi: account bloccato temporaneamente")

    # La verifica della password si fa sempre (anche per utenti inesistenti), così i
    # tempi di risposta non rivelano quali email esistono.
    password_ok = verify_password(body.password, user.password_hash if user and user.password_hash else DUMMY_HASH)
    counter = None
    if user and user.is_active and user.password_hash and password_ok and user.totp_enabled and user.totp_secret_enc:
        counter = totp.accept(secret_store.decrypt(user.totp_secret_enc), body.code, user.totp_last_counter)

    if counter is None:
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
                user.failed_logins = 0
                audit(session, "login.lock", user_id=user.id, ip=ip, minutes=settings.login_lock_minutes)
        audit(session, "login.failed", user_id=user.id if user else None, ip=ip, email=body.email[:320])
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID)

    user.failed_logins, user.locked_until, user.totp_last_counter = 0, None, counter
    token = LOCAL_TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires = now + timedelta(hours=settings.local_session_hours)
    session.add(LocalSession(user_id=user.id, token_hash=hash_session_token(token), ip=ip, expires_at=expires))
    # Pulizia delle sessioni scadute dell'utente.
    await session.execute(delete(LocalSession).where(LocalSession.user_id == user.id, LocalSession.expires_at < now))
    audit(session, "login.local", user_id=user.id, ip=ip)
    await session.commit()
    return LoginOut(token=token, expires_at=expires.isoformat())


@router.post("/logout", status_code=204)
async def logout(user: CurrentUserDep, session: SessionDep, request: Request):
    auth = request.headers.get("authorization", "")[7:].strip()
    if auth.startswith(LOCAL_TOKEN_PREFIX):
        await session.execute(delete(LocalSession).where(LocalSession.token_hash == hash_session_token(auth)))
        audit(session, "logout.local", user_id=user.id, ip=client_ip(request))
        await session.commit()
