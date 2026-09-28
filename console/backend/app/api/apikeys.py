"""Chiavi API per automazioni. Una chiave vale solo per gli scope assegnati.

Scope disponibili:
  releases:upload   caricare rilasci dell'agente (CI). Non può avviare aggiornamenti:
                    quelli restano un'azione del superadmin in console.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.models import ApiKey
from app.models.base import utcnow

API_KEY_PREFIX = "bck_"
SCOPES = {"releases:upload"}


def new_api_key() -> tuple[str, str]:
    token = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Actor:
    """Chi esegue l'azione: un utente o una chiave API."""

    user_id: uuid.UUID | None
    api_key: str | None  # nome della chiave

    @property
    def label(self) -> str:
        return f"api-key:{self.api_key}" if self.api_key else str(self.user_id)


async def superadmin_or_key(request: Request, session: AsyncSession, scope: str) -> Actor:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if token.startswith(API_KEY_PREFIX):
        key = await session.scalar(
            select(ApiKey).where(
                ApiKey.token_hash == hashlib.sha256(token.encode()).hexdigest(), ApiKey.revoked_at.is_(None)
            )
        )
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "chiave API non valida")
        if scope not in (key.scopes or []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"chiave API senza lo scope {scope}")
        key.last_used_at = utcnow()
        return Actor(user_id=None, api_key=key.name)
    user = await get_current_user(request, session)
    user.require_superadmin()
    return Actor(user_id=user.id, api_key=None)
