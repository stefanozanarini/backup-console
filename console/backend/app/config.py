from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://bc:bc@localhost:5432/bc"
    # False nei test: evita di riusare connessioni tra event loop diversi.
    db_pool: bool = True

    # Directory della CA che firma i certificati client degli agenti.
    # Caddy legge solo agent-ca.crt; la chiave resta al backend.
    ca_dir: str = "/data/ca"
    agent_cert_days: int = 365

    # "oidc" in produzione (Entra ID), "dev" accetta l'header X-Dev-User.
    auth_mode: Literal["oidc", "dev"] = "oidc"
    oidc_issuer: str = ""  # es. https://login.microsoftonline.com/<tenant-id>/v2.0
    oidc_audience: str = ""  # client id / Application ID URI dell'API
    oidc_jwks_url: str = ""  # es. https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys

    enroll_token_ttl_hours: int = 24
    agent_offline_after_s: int = 300
    worker_interval_s: int = 60


settings = Settings()
