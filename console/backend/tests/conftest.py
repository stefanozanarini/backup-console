import os
import tempfile

import pytest

TEST_DB = os.environ.get("BC_TEST_DATABASE_URL")
if not TEST_DB:
    pytest.exit("imposta BC_TEST_DATABASE_URL (Postgres dedicato ai test, viene svuotato)", returncode=1)

# Prima di importare app.*: la configurazione viene letta all'import.
os.environ.update(
    BC_DATABASE_URL=TEST_DB,
    BC_DB_POOL="false",
    BC_AUTH_MODE="dev",
    BC_CA_DIR=tempfile.mkdtemp(prefix="bc-ca-"),
    BC_RELEASES_DIR=tempfile.mkdtemp(prefix="bc-rel-"),
)

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Role, RoleAssignment, User  # noqa: E402
from app.services.ca import AgentCA, fingerprint  # noqa: E402

ADMIN = "admin@example.com"


@pytest_asyncio.fixture(autouse=True)
async def db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        admin = User(email=ADMIN)
        admin.roles = [RoleAssignment(role=Role.SUPERADMIN)]
        session.add(admin)
        await session.commit()
    yield


@pytest_asyncio.fixture
async def client():
    ca = AgentCA(settings.ca_dir)
    ca.ensure()
    app.state.ca = ca
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def as_user(email: str = ADMIN) -> dict[str, str]:
    return {"X-Dev-User": email}


def make_csr(cn: str = "ignored") -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def cert_fingerprint(cert_pem: str) -> str:
    return fingerprint(x509.load_pem_x509_certificate(cert_pem.encode()))


def agent_headers(cert_pem: str) -> dict[str, str]:
    """Gli header che nginx aggiunge dopo aver verificato il certificato client."""
    from urllib.parse import quote

    return {"X-Client-Verify": "SUCCESS", "X-Client-Cert": quote(cert_pem, safe="")}
