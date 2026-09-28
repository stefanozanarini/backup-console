"""Hash delle password (scrypt, libreria standard)."""

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**15, 8, 1
MAXMEM = 64 * 1024 * 1024
MIN_LENGTH = 12


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, N, R, P)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${N}${R}${P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt, digest = encoded.split("$")
        if algo != "scrypt":
            return False
        got = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# Hash di riferimento per utenti inesistenti: stessa durata della verifica, così i tempi
# di risposta non rivelano quali email esistono.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
