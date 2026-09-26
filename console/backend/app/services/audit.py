import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def audit(
    session: AsyncSession,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    target: str | None = None,
    ip: str | None = None,
    **details: Any,
) -> None:
    """Aggiunge una riga di audit alla transazione corrente (commit a carico del chiamante)."""
    session.add(
        AuditLog(
            action=action,
            user_id=user_id,
            agent_id=agent_id,
            tenant_id=tenant_id,
            target=target,
            ip=ip,
            details=details,
        )
    )
