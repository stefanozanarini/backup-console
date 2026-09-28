import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPk, utcnow


class Role(StrEnum):
    SUPERADMIN = "superadmin"  # globale: tutto, su tutti i tenant
    OPERATOR = "operator"  # globale o per tenant: monitoring, task, presa in carico
    TENANT_ADMIN = "tenant_admin"  # futuro: utenti cliente
    TENANT_VIEWER = "tenant_viewer"  # futuro: sola lettura cliente


class Tenant(UUIDPk, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    sites: Mapped[list["Site"]] = relationship(back_populates="tenant")


class Site(UUIDPk, Base):
    __tablename__ = "sites"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    tenant: Mapped[Tenant] = relationship(back_populates="sites")


class User(UUIDPk, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    # "oid" di Entra ID, valorizzato al primo login.
    oidc_subject: Mapped[str | None] = mapped_column(String(200), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Secondo fattore per la sessione elevata (comandi remoti). Segreto cifrato con la
    # chiave locale della console (services/secrets.py).
    totp_secret_enc: Mapped[str | None] = mapped_column(String(500))
    totp_enabled: Mapped[bool] = mapped_column(default=False)
    # Ultimo intervallo TOTP accettato: impedisce di riusare lo stesso codice.
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger)
    # Login locale (temporaneo, BC_LOCAL_LOGIN): hash scrypt, mai la password in chiaro.
    password_hash: Mapped[str | None] = mapped_column(String(300))
    failed_logins: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]

    roles: Mapped[list["RoleAssignment"]] = relationship(back_populates="user", lazy="selectin")


class RoleAssignment(UUIDPk, Base):
    __tablename__ = "role_assignments"
    __table_args__ = (UniqueConstraint("user_id", "role", "tenant_id", postgresql_nulls_not_distinct=True),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    # NULL = ruolo globale (tutti i tenant).
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))

    user: Mapped[User] = relationship(back_populates="roles")


class Elevation(UUIDPk, Base):
    """Sessione elevata: ottenuta con TOTP + motivo, necessaria per i comandi remoti."""

    __tablename__ = "elevations"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    reason: Mapped[str] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]


class LocalSession(UUIDPk, Base):
    """Sessione del login locale: il token (solo hash nel DB) va nell'header Authorization."""

    __tablename__ = "local_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
