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
    # Per il frontend (MSAL): app registration SPA e scope dell'API.
    oidc_spa_client_id: str = ""
    oidc_authority: str = ""  # es. https://login.microsoftonline.com/<tenant-id>
    oidc_scope: str = ""  # es. api://backup-console/access
    # URL pubblico dell'endpoint agenti, mostrato nei comandi di installazione.
    agents_url: str = ""

    # Chiave locale per cifrare i segreti (TOTP). Default: file generato accanto alla CA.
    secret_key_file: str = ""
    # Binari dei rilasci dell'agente.
    releases_dir: str = "/data/releases"
    # Chiave pubblica Ed25519 (base64) dei rilasci: se impostata, gli upload con firma
    # non valida vengono rifiutati. La verifica vincolante resta comunque nell'agente.
    release_public_key: str = ""
    elevation_minutes: int = 15
    command_poll_wait_s: int = 25

    enroll_token_ttl_hours: int = 24
    agent_offline_after_s: int = 300
    worker_interval_s: int = 60


settings = Settings()
