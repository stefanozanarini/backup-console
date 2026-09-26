# Installazione su VM Azure (Ubuntu 24.04)

Risultato: `https://backup-console.agsmedia.it` con console, API ed endpoint degli
agenti, dietro un nginx in container che potrà servire anche altre applicazioni.

```
/dati/containers/
├─ backup-console/
│   ├─ app/          clone del repository (compose, deploy.sh, guida)
│   └─ data/
│       ├─ ca/       CA degli agenti + chiave dei segreti (da salvare!)
│       ├─ postgres/ database
│       ├─ releases/ binari dei rilasci dell'agente
│       └─ backup/   dump pre-deploy e notturni
└─ common/
    └─ nginx/        stack edge: nginx + certbot, porte 80/443, certificati in letsencrypt/

stack "edge" (common/nginx) ── rete Docker "proxy" ── stack "backup-console" (nessuna porta esposta)
```

Ogni progetto ha la sua cartella in `/dati/containers/<progetto>`; i servizi generici,
condivisi tra progetti, stanno in `/dati/containers/common/<servizio>`.

## 1. VM e rete

- **Immagine**: Ubuntu Server 24.04 LTS.
- **Dimensione**: B2s (2 vCPU, 4 GB) basta per un centinaio di agenti; B2ms se ci andranno
  altre applicazioni.
- **Disco**: SSD Premium da 64 GB.
- **IP pubblico**: statico.
- **NSG in ingresso**:

  | Porta | Sorgente | Perché |
  |---|---|---|
  | 443/tcp | Any | console e agenti |
  | 80/tcp | Any | challenge Let's Encrypt e redirect a HTTPS |
  | 22/tcp | solo IP dell'ufficio (oppure niente, usando Azure Bastion) | amministrazione |

- **Uscita**: libera (Let's Encrypt, GHCR, Entra ID).
- **DNS**: record **A** `backup-console.agsmedia.it` → IP pubblico della VM, da creare
  **prima** del passo 6.

## 2. Sistema di base

```bash
sudo apt update && sudo apt -y full-upgrade
sudo apt -y install ca-certificates curl git unattended-upgrades
sudo timedatectl set-timezone Europe/Rome
```

Docker Engine dal repository ufficiale:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update && sudo apt -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
```

Poi uscire e rientrare, così il gruppo `docker` diventa attivo.

## 3. Directory, repository e rete condivisa

**Facoltativo, ma consigliato se `/dati` è un disco dati separato**: si spostano anche le
immagini Docker, così il disco del sistema operativo non si riempie. Va fatto **prima** di
scaricare qualsiasi immagine:

```bash
sudo install -d /dati/docker
echo '{ "data-root": "/dati/docker" }' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
```

Struttura delle cartelle. Le cartelle `ca` e `releases` le scrive il container della console
(utente 10001):

```bash
sudo install -d -o $USER -g $USER /dati/containers /dati/containers/backup-console /dati/containers/common
sudo install -d -o $USER -g $USER /dati/containers/backup-console/data /dati/containers/common/nginx
sudo install -d -o 10001 -g 10001 -m 755 /dati/containers/backup-console/data/ca /dati/containers/backup-console/data/releases
sudo install -d -m 700 -o $USER /dati/containers/backup-console/data/backup
```

Il repository è privato: la VM lo legge con una **deploy key in sola lettura**.

```bash
ssh-keygen -t ed25519 -N "" -C "vm-backup-console" -f ~/.ssh/backup-console-deploy
cat ~/.ssh/backup-console-deploy.pub
```

La chiave pubblica stampata va inserita in GitHub: **repository → Settings → Deploy keys →
Add deploy key**, senza "Allow write access". Poi:

```bash
printf 'Host github-backup-console\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/backup-console-deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
chmod 600 ~/.ssh/config
git clone github-backup-console:stefanozanarini/backup-console.git /dati/containers/backup-console/app
```

La rete condivisa tra nginx e le applicazioni si crea una volta sola:

```bash
docker network create proxy
```

Le immagini su GHCR sono private: serve un login con un token GitHub (classic) con il solo
permesso `read:packages`:

```bash
docker login ghcr.io -u stefanozanarini
```

## 4. Segreti

```bash
openssl rand -hex 32
openssl rand -base64 24
```

- Il primo comando genera **BC_PROXY_SECRET**. Va messo nel `.env` della console e in quello
  di edge, con lo stesso valore in entrambi.
- Il secondo genera **POSTGRES_PASSWORD**.

## 5. Stack della console

```bash
cd /dati/containers/backup-console/app/console/deploy
cp .env.example .env
nano .env
```

In `.env` impostare:
- `BC_VERSION`: la versione da installare, cioè il numero del tag `console-backup-vX.Y.Z` su GitHub;
- `BC_PUBLIC_URL`;
- `POSTGRES_PASSWORD` e `BC_PROXY_SECRET`;
- `BC_DATA_DIR=/dati/containers/backup-console/data` ed `EDGE_DIR=/dati/containers/common/nginx`
  (sono già i valori predefiniti di `.env.example`);
- le variabili Entra ID (vedi il passo 8).

Poi avviare:

```bash
cd /dati/containers/backup-console/app/console/deploy
docker compose pull
docker compose up -d --no-build
docker compose logs init
```

Nel log di `init` deve comparire "CA agenti creata" e il file
`/dati/containers/backup-console/data/ca/agent-ca.crt` deve esistere. La console va avviata **prima** di
nginx, che senza quel file non parte. Il controllo sull'URL pubblico che fa `deploy.sh`
riuscirà solo dopo il passo 6: per questo la prima volta si avvia a mano.

## 6. nginx e certificato

nginx è un servizio comune: i suoi file stanno in `/dati/containers/common/nginx`, non dentro il
repository della console. La prima volta si copiano dal repository:

```bash
cd /dati/containers/backup-console/app/edge
cp -r docker-compose.yml templates snippets certs.sh reload-loop.sh .env.example .gitignore /dati/containers/common/nginx/
cd /dati/containers/common/nginx
cp .env.example .env
nano .env
```

In `.env` impostare `BC_HOSTNAMES`, `BC_DATA_DIR=/dati/containers/backup-console/data` e lo
stesso `BC_PROXY_SECRET` della console. Poi:

```bash
./certs.sh issue backup-console it@agsmedia.it backup-console.agsmedia.it
docker compose up -d
```

`certs.sh issue` fa tre cose:
1. crea un certificato temporaneo, così nginx può partire;
2. ottiene quello vero da Let's Encrypt tramite la porta 80;
3. ricarica nginx.

I certificati finiscono in `/dati/containers/common/nginx/letsencrypt/`. I rinnovi sono
automatici: il container `certbot` controlla ogni 12 ore e nginx si ricarica ogni 6.
Registrandosi presso Let's Encrypt con l'email indicata si accettano i suoi termini di servizio.

Da qui in poi i due file di nginx che appartengono alla console
(`templates/backup-console.conf.template` e `snippets/backup-console-agent.conf`) li aggiorna
`./deploy.sh --edge`. Lo script prova la nuova configurazione in un container usa e getta e
riavvia nginx solo se è valida: se non lo è, rimette i file precedenti e nginx continua a
servire tutte le app. Gli altri file (comuni) si aggiornano a mano, se cambiano nel repository.

## 7. Primo amministratore

```bash
cd /dati/containers/backup-console/app/console/deploy
docker compose exec api python -m app.cli create-user stefano.zanarini@agsmedia.it
```

## 8. Login con Entra ID

In **Entra ID → Registrazioni app** servono due registrazioni.

**API "Backup Console API"**
1. *Esporre un'API*: Application ID URI `api://backup-console`, con lo scope `access`
   (consenso: amministratori).
2. *Manifesto*: `"requestedAccessTokenVersion": 2`.
3. Il suo **client ID** va in `BC_OIDC_AUDIENCE`.

**SPA "Backup Console"**
1. *Autenticazione*: piattaforma **Applicazione a pagina singola**, redirect URI
   `https://backup-console.agsmedia.it`.
2. *Autorizzazioni API*: aggiungere `api://backup-console/access` e concedere il consenso.
3. Il suo **client ID** va in `BC_OIDC_SPA_CLIENT_ID`.

Il resto di `.env`:

```
BC_AUTH_MODE=oidc
BC_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
BC_OIDC_JWKS_URL=https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys
BC_OIDC_AUTHORITY=https://login.microsoftonline.com/<tenant-id>
BC_OIDC_SCOPE=api://backup-console/access
```

Poi `docker compose up -d` per applicare. Il login funziona solo per gli utenti creati in
console (passo 7): un account Entra valido ma non registrato riceve "utente non abilitato".
L'MFA si impone con l'accesso condizionale di Entra.

> `BC_AUTH_MODE=dev` è solo per lo sviluppo: accetta un'email qualsiasi senza password.
> Non usarlo mai sulla VM.

## 9. Verifica

```bash
curl -sI https://backup-console.agsmedia.it | head -1
curl -s https://backup-console.agsmedia.it/api/v1/auth-config
curl -s -X POST https://backup-console.agsmedia.it/agent/v1/heartbeat
```

Risposte attese:
1. `HTTP/2 200`;
2. `{"mode":"oidc",...}`;
3. `{"detail":"certificato client mancante"}`.

Poi:
1. entrare nella console;
2. caricare un rilascio da **Aggiornamenti agente** (file della GitHub Release);
3. generare un token in **Tenant e siti**;
4. installare il primo agente.

## 10. Aggiornamenti

**Rilascio**, da GitHub: si crea un tag `console-backup-vX.Y.Z` sul commit da rilasciare, per esempio
`git tag console-backup-v0.3.0 && git push origin console-backup-v0.3.0`. La CI esegue i test e pubblica le
immagini `:X.Y.Z` e `:latest` su GHCR. I push su `main` producono solo `:main`, che non va usato
in produzione.

**Deploy**, sulla VM:

```bash
cd /dati/containers/backup-console/app
./deploy.sh --version 0.3.0
```

Lo script:
1. aggiorna i file di configurazione (`git pull`);
2. valida la configurazione;
3. scarica le immagini;
4. fa il **backup del database** in `/dati/containers/backup-console/data/backup/` (sono le migrazioni a
   modificarlo);
5. riavvia i container;
6. attende la fine delle migrazioni e gli healthcheck;
7. verifica che sia in esecuzione proprio la versione richiesta e che la console risponda
   attraverso nginx.

Se qualcosa fallisce prima del riavvio, `.env` torna com'era e non cambia niente.

- **Tornare indietro**: `./deploy.sh --no-git --version <precedente>`. Se la versione nuova
  aveva migrazioni del database, ripristinare anche il backup indicato dallo script
  (`pg_restore`).
- **Aggiornare anche nginx**: `./deploy.sh --edge`: sincronizza il template della console, lo
  prova e ricrea nginx.
- **Solo riavvio con la stessa versione** (per esempio dopo aver modificato `.env`): `./deploy.sh`.

Lo storico dei deploy è in `/dati/containers/backup-console/app/deploy.log`.

## 11. Backup

Cosa salvare:

| Cosa | Dove | Se si perde |
|---|---|---|
| Database (dump, non i file) | `data/postgres` | storico, configurazione, dispositivi |
| `ca/agent-ca.crt` + `agent-ca.key` | `data/ca` | senza la CA gli agenti vanno tutti registrati di nuovo |
| `ca/secret.key` | `data/ca` | i TOTP non si decifrano più: ogni utente va resettato (`reset-totp`) |
| `.env` della console e di nginx | `app/console/deploy/.env`, `common/nginx/.env` | segreti e configurazione |
| certificati Let's Encrypt | `common/nginx/letsencrypt` | si riottengono con `certs.sh issue` |

I percorsi sono relativi a `/dati/containers/backup-console/` e `/dati/containers/`.

Dump notturno dal crontab di **root** (`sudo crontab -e`), perché la chiave della CA è
leggibile solo dal suo proprietario:

```
30 2 * * * cd /dati/containers/backup-console/app/console/deploy && docker compose exec -T db pg_dump -U bc -Fc bc > /dati/containers/backup-console/data/backup/bc-$(date +\%a).dump && tar czf /dati/containers/backup-console/data/backup/ca-$(date +\%a).tgz -C /dati/containers/backup-console/data ca
```

Il comando conserva gli ultimi 7 giorni, per nome del giorno. Poi va copiata fuori dalla VM, per
esempio con `azcopy sync` verso uno storage account con immutabilità, oppure con Azure Backup
della VM.

## 12. Altre applicazioni sulla stessa VM

1. Il container della nuova app si collega alla rete `proxy` con un alias univoco e **senza**
   porte pubblicate.
2. In `/dati/containers/common/nginx/templates/` si copia `_esempio-app.conf.template.txt` in
   `<app>.conf.template` e lo si adatta.
3. La variabile del nome host va aggiunta in `/dati/containers/common/nginx/docker-compose.yml`.
4. `./certs.sh issue <app> <email> <nome-host>`
5. `docker compose up -d --force-recreate nginx`

nginx risolve i nomi dei container a ogni richiesta: un'app ferma dà 502 solo sul suo nome host
e non blocca le altre.

## Problemi comuni

- **nginx non parte, "agent-ca.crt: No such file"**: la console non è ancora stata avviata
  (passo 5), oppure `BC_DATA_DIR` è diverso tra il `.env` della console e quello di nginx.
- **502 Bad Gateway**: `api` o `frontend` fermi. Controllare con
  `docker compose ps` e `docker compose logs api` in `app/console/deploy`.
- **Gli agenti ricevono sempre "certificato client mancante"**: `BC_PROXY_SECRET` è diverso
  tra i due `.env`. Dopo averlo modificato: `docker compose up -d` in entrambi.
- **Stato dei certificati**: `docker compose run --rm certbot certificates` in
  `/dati/containers/common/nginx`.
