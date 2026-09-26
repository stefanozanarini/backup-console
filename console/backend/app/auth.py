"""Autenticazione utenti della console e controllo di accesso per tenant.

Gli utenti sono pre-registrati (CLI o API superadmin): un login Entra ID valido
ma senza utente corrispondente riceve 403.
"""

import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Role, User

GLOBAL_READ_ROLES = {Role.SUPERADMIN, Role.OPERATOR}
WRITE_ROLES = {Role.SUPERADMIN, Role.OPERATOR, Role.TENANT_ADMIN}


@dataclass
class CurrentUser:
    user: User

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def is_superadmin(self) -> bool:
        return any(r.role == Role.SUPERADMIN for r in self.user.roles)

    def tenant_scope(self, roles: set[Role] | None = None) -> set[uuid.UUID] | None:
        """Tenant accessibili con i ruoli indicati. None = tutti i tenant."""
        roles = roles or set(Role)
        scope: set[uuid.UUID] = set()
        for r in self.user.roles:
            if r.role not in roles:
                continue
            if r.tenant_id is None:
                if r.role in GLOBAL_READ_ROLES:
                    return None
                continue
            scope.add(r.tenant_id)
        return scope

    def can_read(self, tenant_id: uuid.UUID) -> bool:
        scope = self.tenant_scope()
        return scope is None or tenant_id in scope

    def can_write(self, tenant_id: uuid.UUID) -> bool:
        scope = self.tenant_scope(WRITE_ROLES)
        return scope is None or tenant_id in scope

    def require_read(self, tenant_id: uuid.UUID) -> None:
        # 404 anziché 403: non riveliamo l'esistenza di oggetti di altri tenant.
        if not self.can_read(tenant_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "non trovato")

    def require_write(self, tenant_id: uuid.UUID) -> None:
        self.require_read(tenant_id)
        if not self.can_write(tenant_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "permessi insufficienti")

    def require_superadmin(self) -> None:
        if not self.is_superadmin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "riservato al superadmin")


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(settings.oidc_jwks_url, cache_keys=True)


def _decode_oidc(token: str) -> dict:
    try:
        key = _jwks_client().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token non valido") from e


async def get_current_user(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> CurrentUser:
    if settings.auth_mode == "dev":
        email = request.headers.get("x-dev-user")
        if not email:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "header X-Dev-User mancante")
        user = await session.scalar(select(User).where(func.lower(User.email) == email.lower()))
    else:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token mancante")
        claims = _decode_oidc(auth[7:])
        subject = claims.get("oid") or claims["sub"]
        email = claims.get("preferred_username") or claims.get("email") or claims.get("upn")
        user = await session.scalar(select(User).where(User.oidc_subject == subject))
        if user is None and email:
            # Primo login: colleghiamo l'identità Entra all'utente pre-registrato.
            user = await session.scalar(
                select(User).where(func.lower(User.email) == email.lower(), User.oidc_subject.is_(None))
            )
            if user is not None:
                user.oidc_subject = subject
                await session.commit()

    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "utente non abilitato")
    return CurrentUser(user)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
