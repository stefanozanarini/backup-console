import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPk, utcnow


class VeeamJob(UUIDPk, Base):
    """Stato corrente di un job Veeam (B&R o Veeam Agent), aggiornato a ogni report dell'agente."""

    __tablename__ = "veeam_jobs"
    __table_args__ = (UniqueConstraint("device_id", "job_key"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    product: Mapped[str] = mapped_column(String(32))  # vbr / vaw
    job_key: Mapped[str] = mapped_column(String(200))  # id Veeam del job
    name: Mapped[str] = mapped_column(String(300))
    job_type: Mapped[str | None] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(default=True)
    last_result: Mapped[str | None] = mapped_column(String(32))  # Success / Warning / Failed / None
    last_state: Mapped[str | None] = mapped_column(String(32))  # Stopped / Working / ...
    last_start: Mapped[datetime | None]
    last_end: Mapped[datetime | None]
    last_success: Mapped[datetime | None]
    transferred_bytes: Mapped[int | None] = mapped_column(BigInteger)
    next_run: Mapped[datetime | None]
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class VeeamSession(Base):
    """Storico delle sessioni dei job Veeam. Le riporta l'agente (ultimi 10 giorni a ogni
    report) e restano qui anche dopo che Veeam le ha cancellate o il job è stato eliminato:
    per questo niente chiave esterna verso veeam_jobs, solo job_key e nome."""

    __tablename__ = "veeam_sessions"
    __table_args__ = (
        UniqueConstraint("device_id", "job_key", "session_key"),
        Index("ix_veeam_sessions_device_started", "device_id", "started_at"),
        Index("ix_veeam_sessions_tenant_ended", "tenant_id", "ended_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    product: Mapped[str] = mapped_column(String(32))
    job_key: Mapped[str] = mapped_column(String(200))
    job_name: Mapped[str] = mapped_column(String(300))
    # Id della sessione Veeam (B&R); per Veeam Agent l'istante di fine.
    session_key: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    result: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(32))
    mode: Mapped[str | None] = mapped_column(String(16))  # full / incremental
    retry: Mapped[bool] = mapped_column(default=False)
    transferred_bytes: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
