import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPk, utcnow


class Severity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class ProblemState(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class CheckRule(UUIDPk, Base):
    """Soglie a cascata: dispositivo > tenant > globale (entrambi NULL)."""

    __tablename__ = "check_rules"

    check: Mapped[str] = mapped_column(String(100))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    warn: Mapped[float | None]
    crit: Mapped[float | None]
    enabled: Mapped[bool] = mapped_column(default=True)


class Problem(UUIDPk, Base):
    __tablename__ = "problems"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    check: Mapped[str] = mapped_column(String(100))
    # dispositivo + check + oggetto (es. mount): lo stesso problema aggiorna la riga esistente.
    dedup_key: Mapped[str] = mapped_column(String(400))
    severity: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default=ProblemState.OPEN)
    title: Mapped[str] = mapped_column(String(300))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[datetime | None]
    acked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acked_at: Mapped[datetime | None]

    __table_args__ = (
        # Al massimo un problema non risolto per chiave.
        Index("uq_problems_active_dedup", "dedup_key", unique=True, postgresql_where=text("state <> 'resolved'")),
        Index("ix_problems_state_severity", "state", "severity"),
    )
