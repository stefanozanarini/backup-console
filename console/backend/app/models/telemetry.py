import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class AgentMessage(Base):
    """msg_id già ricevuti: rende idempotente il reinvio dei dati bufferizzati."""

    __tablename__ = "agent_messages"

    msg_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    collected_at: Mapped[datetime]
    data: Mapped[dict[str, Any]]

    __table_args__ = (Index("ix_inventory_device_ts", "device_id", "collected_at"),)


class Metric(Base):
    # TODO: partizionare per mese (o TimescaleDB) quando i volumi lo richiederanno.
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    name: Mapped[str] = mapped_column(String(100))  # es. disk.used_pct
    labels: Mapped[dict[str, Any]] = mapped_column(default=dict)  # es. {"mount": "/share/CACHEDEV1_DATA"}
    value: Mapped[float]

    __table_args__ = (Index("ix_metrics_device_name_ts", "device_id", "name", "ts"),)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    type: Mapped[str] = mapped_column(String(100))  # es. veeam.session, hw.disk
    severity: Mapped[str] = mapped_column(String(16), default="info")
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)

    __table_args__ = (Index("ix_events_device_ts", "device_id", "ts"),)
