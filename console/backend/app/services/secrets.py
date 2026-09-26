"""Cifratura simmetrica dei segreti salvati nel DB (es. seed TOTP) con una chiave locale.

La chiave sta in un file nel volume della CA, generato al primo avvio: un dump del
database da solo non basta per ricavare i segreti.
"""

import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import settings


def key_path() -> Path:
    return Path(settings.secret_key_file or Path(settings.ca_dir) / "secret.key")


def ensure_key() -> bool:
    p = key_path()
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(Fernet.generate_key())
    return True


@lru_cache
def _fernet() -> Fernet:
    ensure_key()
    return Fernet(key_path().read_bytes().strip())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
