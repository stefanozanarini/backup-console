import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow

# Piattaforme riconosciute: devono coincidere con agent/internal/upgrade.Platform().
PLATFORMS = ("linux-amd64", "linux-arm64", "linux-armv7", "windows-amd64")
# Pacchetti di installazione (non usati dagli aggiornamenti, solo per il download).
PACKAGES = ("qpkg-x86_64", "qpkg-arm_64", "qpkg-arm-x41", "qpkg-arm-x31")


class Release(Base):
    __tablename__ = "releases"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    notes: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ReleaseAsset(Base):
    __tablename__ = "release_assets"

    version: Mapped[str] = mapped_column(ForeignKey("releases.version", ondelete="CASCADE"), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    # Firma Ed25519 (base64) dei byte del binario, prodotta da `bc-release sign`.
    signature: Mapped[str] = mapped_column(String(200))
