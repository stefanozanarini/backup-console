"""Rilasci dell'agente: archiviazione dei binari e verifica delle firme Ed25519."""

import base64
import hashlib
import re
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.config import settings

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$")


QPKG_RE = re.compile(r"^BackupConsoleAgent_(?P<version>[^_]+)_(?P<arch>x86_64|arm_64|arm-x41|arm-x31)\.qpkg$")
BIN_RE = re.compile(r"^bc-agent-(?P<platform>(linux|windows)-(amd64|arm64|armv7))(\.exe)?$")


def asset_key(filename: str) -> str | None:
    """Nome file caricato → chiave dell'asset (es. windows-amd64, qpkg-x86_64)."""
    if m := BIN_RE.match(filename):
        return m.group("platform")
    if m := QPKG_RE.match(filename):
        return f"qpkg-{m.group('arch')}"
    return None


def download_name(version: str, key: str) -> str:
    if key.startswith("qpkg-"):
        return f"BackupConsoleAgent_{version}_{key.removeprefix('qpkg-')}.qpkg"
    return "bc-agent.exe" if key.startswith("windows-") else f"bc-agent-{key}"


def asset_path(version: str, platform: str) -> Path:
    return Path(settings.releases_dir) / version / f"bc-agent-{platform}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_signature(data: bytes, signature_b64: str, public_key_b64: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64), data)
        return True
    except (InvalidSignature, ValueError):
        return False
