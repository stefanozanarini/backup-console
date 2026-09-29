from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BC_", env_file=".env", extra="ignore")

    # Versione dell'immagine (build-arg VERSION della CI): /healthz la riporta.
    build_version: str = "dev"

    database_url: str = "postgresql+asyncpg://bc:bc@localhost:5432/bc"
    # False nei test: evita di riusare connessioni tra event loop diversi.
    db_pool: bool = True

    # Directory della CA che firma i certificati client degli agenti.
    # nginx legge solo agent-ca.crt; la chiave resta al backend.
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

    # Segreto condiviso con l'nginx di bordo (header X-Proxy-Secret sulle location
    # /agent/v1/): senza, gli header del certificato client vengono ignorati. Protegge
    # da altri container sulla rete "proxy" che chiamino l'api direttamente.
    proxy_secret: str = ""

    # Login con utente locale (password + TOTP) in aggiunta a Entra ID. Temporaneo:
    # da disattivare quando il login Entra ID è operativo.
    local_login: bool = False
    local_session_hours: int = 12
    login_max_failures: int = 5
    login_lock_minutes: int = 15

    enroll_token_ttl_hours: int = 24
    agent_offline_after_s: int = 300
    worker_interval_s: int = 60

    # Retention dei dati (giorni). 0 = conserva per sempre.
    retention_metrics_raw_days: int = 90  # dopo: un valore medio all'ora
    retention_metrics_days: int = 730
    retention_inventory_full_days: int = 7  # dopo: un'istantanea al giorno
    retention_inventory_days: int = 365  # l'ultima di ogni dispositivo resta comunque
    retention_events_days: int = 90  # eventi degli agenti e id dei messaggi ricevuti
    # Problemi risolti, comandi conclusi, audit, sessioni Veeam.
    retention_history_days: int = 730
    retention_interval_h: int = 6


settings = Settings()
