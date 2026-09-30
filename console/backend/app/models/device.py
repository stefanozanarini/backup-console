import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPk, utcnow


class DeviceKind(StrEnum):
    NAS = "nas"
    SERVER = "server"
    PC = "pc"


class AgentStatus(StrEnum):
    PENDING = "pending"  # enrollato, in attesa di approvazione manuale
    APPROVED = "approved"
    REVOKED = "revoked"


class Device(UUIDPk, Base):
    __tablename__ = "devices"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    hostname: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(32))  # linux / windows
    arch: Mapped[str] = mapped_column(String(32))
    # Ruoli rilevati dall'agente: ["qnap"], ["veeam_vbr"], ["veeam_agent"], ...
    roles: Mapped[list[Any]] = mapped_column(default=list)
    model: Mapped[str | None] = mapped_column(String(200))
    serial: Mapped[str | None] = mapped_column(String(200))
    os_version: Mapped[str | None] = mapped_column(String(200))
    # ID di Supremo (assistenza remota) letto dal registro dall'agente, se installato.
    supremo_id: Mapped[str | None] = mapped_column(String(32))
    # Schede di rete dall'inventario: [{"name", "mac", "ipv4": [...]}].
    ip_addresses: Mapped[list[Any] | None] = mapped_column(nullable=True)
    # Finestra di backup del PC: None = eredita dal tenant; {"enabled": false} = nessuna.
    backup_window: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    # Ultimo report Veeam: prodotto, versione, repository, licenza (i job sono in veeam_jobs).
    veeam: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime | None]

    agent: Mapped["Agent | None"] = relationship(back_populates="device", uselist=False)


class Agent(UUIDPk, Base):
    __tablename__ = "agents"

    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=AgentStatus.PENDING)
    # SHA-256 (hex) del certificato client; calcolato dal certificato che nginx inoltra
    # dopo averlo verificato (vedi api/agent/deps.py).
    cert_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    cert_not_after: Mapped[datetime]
    version: Mapped[str | None] = mapped_column(String(64))
    capabilities: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # Deciso in fase di installazione sul dispositivo; la console lo legge soltanto.
    allow_shell: Mapped[bool] = mapped_column(default=False)
    enrolled_at: Mapped[datetime] = mapped_column(default=utcnow)
    approved_at: Mapped[datetime | None]
    last_seen_at: Mapped[datetime | None]
    last_ip: Mapped[str | None] = mapped_column(String(64))
    # Token con cui si è registrato: la sua nota aiuta a riconoscere il rollout in Approvazioni.
    enrollment_token_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("enrollment_tokens.id", ondelete="SET NULL")
    )

    device: Mapped[Device] = relationship(back_populates="agent")
    enrollment_token: Mapped["EnrollmentToken | None"] = relationship(lazy="selectin")

    @property
    def enrollment_note(self) -> str | None:
        return self.enrollment_token.note if self.enrollment_token else None


class EnrollmentToken(UUIDPk, Base):
    __tablename__ = "enrollment_tokens"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    # Salviamo solo l'hash: il token in chiaro viene mostrato una volta sola.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    note: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime]
    # Token multiuso per i rollout (GPO, Intune, script): ogni enrollment consuma un utilizzo.
    max_uses: Mapped[int] = mapped_column(default=1)
    uses: Mapped[int] = mapped_column(default=0)
    used_at: Mapped[datetime | None]  # ultimo utilizzo
    revoked_at: Mapped[datetime | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


def token_usable(token: "EnrollmentToken", now: datetime) -> bool:
    return token.revoked_at is None and token.expires_at > now and token.uses < token.max_uses
