# Backup Console

Console centrale di monitoring e gestione per NAS QNAP, server Veeam B&R e PC con
Veeam Agent, con agenti che si collegano solo in uscita. Architettura e decisioni:
[ARCHITECTURE.md](ARCHITECTURE.md).

```
console/backend   FastAPI + SQLAlchemy + Alembic
console/deploy    docker-compose dello stack della console
console/frontend  React + Vite (servito da nginx nel container frontend)
edge/             nginx di bordo + certbot, condiviso con altre app della VM
docs/             guida di installazione su VM Azure
agent/            bc-agent in Go (QNAP, Windows)
packaging/        QPKG (QNAP); MSI da fare
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

Stessa struttura della VM (stack `edge` con nginx più stack della console), sulle porte
18080/18443 e con un certificato di test per `backup-console.localhost`.

```bash
docker network create proxy-dev
mkdir -p ~/bcdata && docker run --rm -v ~/bcdata:/d alpine sh -c "mkdir -p /d/ca && chown 10001:10001 /d/ca"
```

`console/deploy/.env` (partendo da `.env.example`):

```
BC_PUBLIC_URL=https://backup-console.localhost:18443
BC_AUTH_MODE=dev
BC_DATA_DIR=/home/<utente>/bcdata
PROXY_NETWORK=proxy-dev
BACKEND_IMAGE=backup-console-backend:dev
FRONTEND_IMAGE=backup-console-frontend:dev
POSTGRES_PASSWORD=…
BC_PROXY_SECRET=…
```

`edge/.env`:

```
BC_HOSTNAMES=backup-console.localhost
BC_DATA_DIR=/home/<utente>/bcdata
PROXY_NETWORK=proxy-dev
HTTP_PORT=18080
HTTPS_PORT=18443
BC_PROXY_SECRET=…
```

`BC_PROXY_SECRET` deve avere lo stesso valore nei due file.

```bash
(cd console/deploy && docker compose up -d --build)
(cd edge && ./certs.sh selfsigned backup-console backup-console.localhost && docker compose up -d)
(cd console/deploy && docker compose exec api python -m app.cli create-user tu@azienda.it)
console/backend/.venv/bin/python console/deploy/dev/seed_demo.py --ca edge/dev-ca.crt   # dati di esempio
```

La console è su `https://backup-console.localhost:18443`: la CA di test è in `edge/dev-ca.crt`.
Con `BC_AUTH_MODE=dev` l'API accetta l'header `X-Dev-User: <email>` al posto del token
Entra ID. Per il frontend con ricarica automatica vedi
[console/frontend/README.md](console/frontend/README.md).

## Agente

Build (serve Go 1.22+, oppure `docker run --rm -v $PWD:/src -w /src golang:1.22 ...`):

```bash
cd agent
go test ./...
GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -o bc-agent ./cmd/bc-agent
```

Enrollment e avvio:

```bash
bc-agent enroll -config ./agent.json -server https://backup-console.localhost:18443 -token <TOKEN> -server-ca edge/dev-ca.crt
```

Approvare l'agente da console (`POST /api/v1/agents/{id}/approve`), poi:

```bash
bc-agent run -config ./agent.json
```

Pacchetti QNAP: vedi [packaging/README.md](packaging/README.md).

Windows (server Veeam e PC), da un prompt amministratore:

```bash
bc-agent.exe install -server https://agents.<dominio> -token <TOKEN>
```

Copia l'eseguibile in `C:\Program Files\BackupConsoleAgent`, registra l'agente (configurazione,
chiave e log in `C:\ProgramData\BackupConsoleAgent`, accessibile solo a SYSTEM e Administrators)
e crea il servizio **Backup Console Agent**, con avvio automatico e riavvio in caso di uscita.
L'esecuzione remota è attiva: per disattivarla aggiungere `-disable-remote-exec`. Per rimuovere
l'agente: `bc-agent.exe uninstall [-purge]`. Rilanciare `install` su una macchina già registrata
aggiorna binario e servizio ma mantiene la registrazione (`-reenroll` per forzarne una nuova).

## Rilasci firmati

Una volta sola, su una postazione sicura:

```bash
go run ./agent/cmd/bc-release keygen -out release-signing.key
```

Il comando stampa la chiave pubblica. Da impostare in tre posti:
- **GitHub → Settings → Variables**: `AGENT_RELEASE_PUBLIC_KEY` = chiave pubblica (incorporata nei binari);
- **GitHub → Environment `agent-release` → Secrets**: `AGENT_RELEASE_SIGNING_KEY` = contenuto di `release-signing.key`;
- **console** (facoltativo, controllo anticipato): `BC_RELEASE_PUBLIC_KEY` nel `.env`.

Un rilascio parte con un tag `agent-v0.2.0`: la CI compila, crea i QPKG, firma tutto e pubblica
una GitHub Release. Binari, QPKG e `manifest.json` si caricano poi in console da
**Aggiornamenti agente**. Da quel momento sono anche la versione scaricata dai comandi di
installazione.

I binari compilati senza chiave pubblica (build locali) rifiutano gli aggiornamenti remoti. In
sviluppo si può usare `enroll -allow-unsigned-upgrades`.

Con `bc-agent collect` si stampano i dati raccolti senza inviarli: utile per validare
i parser su un QNAP reale.

## Onboarding dei dispositivi

1. **Tenant e siti → Nuovo token di installazione** sul sito del dispositivo. Scegliere:
   - "Un dispositivo", oppure "Rollout su più dispositivi" con il numero massimo;
   - la validità;
   - una nota, che poi compare in Approvazioni.
2. La console mostra i comandi da copiare. Scaricano l'ultima versione caricata in
   **Aggiornamenti agente** usando il token (nessun file da copiare a mano) e registrano il
   dispositivo:
   - **Windows** (server Veeam, PC): script PowerShell da eseguire come amministratore.
     È idempotente: se il servizio esiste già non fa nulla, quindi va bene anche come script di
     avvio GPO o come script Intune, insieme a un token multiuso;
   - **QNAP**: comando SSH che sceglie il QPKG in base a `uname -m`, lo installa e registra
     l'agente. Per ARMv7 viene usato `arm-x41` (Annapurna AL-212/314).
3. **Approvazioni**: controllare nome, tenant, IP e nota del token, poi approvare (anche più
   dispositivi insieme).
4. Il token si revoca quando il rollout è finito. Gli agenti già registrati non sono toccati.

Da CLI: `python -m app.cli create-token <tenant> "<sito>" --note "Rollout GPO" --uses 50 --hours 72`.

Per un'installazione manuale, i file si scaricano da **Aggiornamenti agente** (clic sulla
piattaforma).

## Produzione (VM Azure)

Guida completa, dalla VM appena creata alla console online: [docs/deploy-azure.md](docs/deploy-azure.md).

- **Rilascio della console**: tag `console-vX.Y.Z`, la CI pubblica le immagini `:X.Y.Z` su GHCR.
- **Deploy sulla VM**: `./deploy.sh --version X.Y.Z`, con backup del DB, healthcheck, controllo
  della versione e istruzioni per tornare indietro.
