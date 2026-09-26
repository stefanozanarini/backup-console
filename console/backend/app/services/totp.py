"""TOTP (RFC 6238, SHA-1, 6 cifre, 30 s): compatibile con Microsoft/Google Authenticator."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def code_at(secret: str, ts: float) -> str:
    return _code(secret, int(ts // STEP))


def match(secret: str, code: str, now: float | None = None, window: int = 1) -> int | None:
    """Ritorna il contatore (intervallo di 30 s) a cui corrisponde il codice, o None."""
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    counter = int((now if now is not None else time.time()) // STEP)
    for d in range(-window, window + 1):
        if hmac.compare_digest(_code(secret, counter + d), code):
            return counter + d
    return None


def verify(secret: str, code: str, now: float | None = None, window: int = 1) -> bool:
    return match(secret, code, now, window) is not None


def accept(secret: str, code: str, last_counter: int | None, now: float | None = None) -> int | None:
    """Come match, ma rifiuta un codice già usato (contatore non successivo all'ultimo accettato)."""
    counter = match(secret, code, now)
    if counter is None or (last_counter is not None and counter <= last_counter):
        return None
    return counter


def provisioning_uri(secret: str, account: str, issuer: str = "Backup Console") -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}"
        f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"
    )
