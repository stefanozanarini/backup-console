# Backup Console

Console centrale di monitoring e gestione per NAS QNAP, server Veeam B&R e PC con
Veeam Agent, con agenti che si collegano solo in uscita. Architettura e decisioni:
[ARCHITECTURE.md](ARCHITECTURE.md).

```
console/backend   FastAPI + SQLAlchemy + Alembic
console/deploy    docker-compose, Caddyfile (prod e dev)
console/frontend  (da fare)
agent/            bc-agent in Go (QNAP, Windows)
packaging/        QPKG e MSI (da fare)
```

## Sviluppo backend

```bash
cd console/backend
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
docker run -d --name bc-pg -e POSTGRES_USER=bc -e POSTGRES_PASSWORD=bc -e POSTGRES_DB=bc_test -p 127.0.0.1:55432:5432 postgres:16-alpine
BC_TEST_DATABASE_URL=postgresql+asyncpg://bc:bc@127.0.0.1:55432/bc_test .venv/bin/pytest
```

Nuova migrazione dopo aver modificato i modelli:

```bash
BC_DATABASE_URL=postgresql+asyncpg://bc:bc@127.0.0.1:55432/bc .venv/bin/alembic revision --autogenerate -m "descrizione"
```

## Stack completo in locale

```bash
cd console/deploy
cp .env.example .env
```

Poi in `.env` impostare:

```
CONSOLE_HOST=console.localhost
AGENTS_HOST=agents.localhost
BC_AUTH_MODE=dev
CADDYFILE=Caddyfile.dev
BACKEND_IMAGE=backup-console-backend:dev
HTTPS_PORT=18443
HTTP_PORT=18080
```

Quindi:

```bash
docker compose up -d --build
docker compose exec api python -m app.cli create-user tu@azienda.it
docker compose exec api python -m app.cli create-tenant "Cliente Demo" demo
docker compose exec api python -m app.cli create-site demo "Sede principale"
docker compose exec api python -m app.cli create-token demo "Sede principale"
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
```

Con `BC_AUTH_MODE=dev` l'API accetta l'header `X-Dev-User: <email>` al posto del token
Entra ID. Documentazione OpenAPI su `https://console.localhost:18443/docs`.

## Agente

Build (serve Go 1.22+, oppure `docker run --rm -v $PWD:/src -w /src golang:1.22 ...`):

```bash
cd agent
go test ./...
GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -o bc-agent ./cmd/bc-agent
```

Enrollment e avvio:

```bash
bc-agent enroll -config ./agent.json -server https://agents.localhost:18443 -token <TOKEN> -server-ca caddy-root.crt
```

Approvare l'agente da console (`POST /api/v1/agents/{id}/approve`), poi:

```bash
bc-agent run -config ./agent.json
```

Con `bc-agent collect` si stampano i dati raccolti senza inviarli: utile per validare
i parser su un QNAP reale.

## Produzione (VM Azure)

1. VM Ubuntu 24.04 con Docker. Nell'NSG aprire solo la 443 (più la 80 per la challenge ACME).
2. Creare i record DNS `console.<dominio>` e `agents.<dominio>` verso l'IP della VM.
3. In `console/deploy/.env` impostare host, password DB, parametri Entra ID e `BACKEND_IMAGE` da GHCR.
4. Avviare lo stack:
   ```bash
   docker compose up -d
   ```
5. Creare il primo superadmin:
   ```bash
   docker compose exec api python -m app.cli create-user <email>
   ```
