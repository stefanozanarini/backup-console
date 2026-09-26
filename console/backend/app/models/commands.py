import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPk, utcnow


class CommandType(StrEnum):
    EXEC = "exec"  # comando shell/PowerShell
    UPGRADE = "upgrade"  # aggiornamento dell'agente


class CommandStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    EXPIRED = "expired"  # l'agente non l'ha ritirato in tempo


FINAL_STATUSES = {CommandStatus.SUCCEEDED, CommandStatus.FAILED, CommandStatus.TIMEOUT, CommandStatus.EXPIRED}
MAX_OUTPUT = 1_000_000


class Command(UUIDPk, Base):
    __tablename__ = "commands"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=CommandStatus.QUEUED)
    timeout_s: Mapped[int] = mapped_column(Integer, default=300)
    reason: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    dispatched_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    exit_code: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(String(1000))
    output: Mapped[str] = mapped_column(Text, default="")
    output_truncated: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (Index("ix_commands_agent_status", "agent_id", "status", "created_at"),)
