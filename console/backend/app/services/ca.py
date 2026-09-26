"""CA interna che firma i certificati client (mTLS) degli agenti.

nginx verifica la catena con agent-ca.crt; il backend identifica poi l'agente
tramite il fingerprint del certificato, per cui la revoca ha effetto immediato
senza bisogno di CRL.
"""

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_CERT = "agent-ca.crt"
CA_KEY = "agent-ca.key"


class CSRError(ValueError):
    pass


def fingerprint(cert: x509.Certificate) -> str:
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


class AgentCA:
    def __init__(self, ca_dir: str | Path):
        self.dir = Path(ca_dir)
        self._cert: x509.Certificate | None = None
        self._key: ec.EllipticCurvePrivateKey | None = None

    @property
    def cert_path(self) -> Path:
        return self.dir / CA_CERT

    @property
    def key_path(self) -> Path:
        return self.dir / CA_KEY

    def ensure(self) -> bool:
        """Crea la CA se non esiste. Ritorna True se l'ha appena creata."""
        if self.cert_path.exists() and self.key_path.exists():
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP384R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Backup Console Agent CA")])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=True,
                    crl_sign=True, encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA384())
        )
        key_bytes = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key_bytes)
        self.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return True

    def _load(self) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
        if self._cert is None or self._key is None:
            self._cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
            key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
            assert isinstance(key, ec.EllipticCurvePrivateKey)
            self._key = key
        return self._cert, self._key

    def ca_pem(self) -> str:
        return self.cert_path.read_text()

    def sign_csr(self, csr_pem: str, agent_id: uuid.UUID, days: int) -> tuple[str, str, datetime]:
        """Firma la CSR dell'agente. Il subject viene imposto dalla CA (CN = agent_id),
        quello richiesto nella CSR è ignorato."""
        try:
            csr = x509.load_pem_x509_csr(csr_pem.encode())
        except ValueError as e:
            raise CSRError("CSR non valida") from e
        if not csr.is_signature_valid:
            raise CSRError("firma della CSR non valida")
        pub = csr.public_key()
        if not isinstance(pub, ec.EllipticCurvePublicKey):
            raise CSRError("è richiesta una chiave EC")

        ca_cert, ca_key = self._load()
        now = datetime.now(UTC)
        not_after = now + timedelta(days=days)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(agent_id))]))
            .issuer_name(ca_cert.subject)
            .public_key(pub)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=False,
                    crl_sign=False, encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
            )
            .sign(ca_key, hashes.SHA384())
        )
        return cert.public_bytes(serialization.Encoding.PEM).decode(), fingerprint(cert), not_after
