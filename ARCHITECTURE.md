# Architettura – Backup Console

Console centrale, esposta su Internet, per il monitoring e la gestione di:

- ~30 **NAS QNAP** (QTS 4.x/5.x, QuTS hero; x86_64, ARM64, ARMv7)
- ~20 **server Veeam Backup & Replication** (v11, v12, v13)
- ~30 **PC con Veeam Agent for Windows** standalone

Organizzati per **tenant** (cliente) → **sito** → **dispositivo**.

## Principio di base: solo connessioni in uscita

La console non può raggiungere i dispositivi, loro sì. Ogni connessione parte
dall'agente (HTTPS 443 in uscita); i comandi scendono come risposta sulla stessa
connessione. Nei siti non si apre nessuna porta in ingresso.

```
 SITI (nessuna porta in ingresso)                AZURE VM (docker compose)
┌──────────────────────────┐                 ┌───────────────────────────────┐
│ QNAP  → bc-agent (QPKG)  │── HTTPS mTLS ──▶│ caddy :443                    │
│ VBR   → bc-agent (MSI)   │────────────────▶│  agents.<dominio> → mTLS      │
│ PC    → bc-agent (MSI)   │────────────────▶│  console.<dominio> → Entra ID │
└──────────────────────────┘                 │        │                      │
                                             │   api (FastAPI) ── worker     │
                                             │        │                      │
                                             │   postgres                    │
                                             └───────────────────────────────┘
```

## Componenti

| Componente | Tecnologia | Note |
|---|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic | `console/backend` |
| Worker | stesso codice, `python -m app.worker` | offline check; poi notifiche, retention |
| DB | PostgreSQL 16 | metriche in tabella semplice, partizionamento quando serve |
| Reverse proxy | Caddy 2.8 | Let's Encrypt, mTLS agenti, header di sicurezza |
| Frontend | *(da fare)* React/Vite, build statica servita da Caddy | `console/frontend` |
| Agente | Go, binario statico senza dipendenze | `agent/` |
| Pacchetti | QPKG (QDK) per QNAP, MSI (WiX v5) per Windows | `packaging/` |
| CI/CD | GitHub Actions, immagini su GHCR | `.github/workflows` |

Perché l'agente è in Go anche se il backend è Python: sui QNAP (specie ARM
più vecchi) Python manca o varia tra versioni, mentre un binario statico gira
ovunque ed è facile da impacchettare come QPKG e MSI.

## Sicurezza

### Identità degli agenti (mTLS)

1. In console si genera un **token di enrollment** monouso (24h) legato a tenant e sito.
   Nel DB si salva solo l'hash.
2. L'agente genera la propria chiave EC **sul dispositivo** e invia token + CSR a `/agent/v1/enroll`.
3. La CA agenti interna firma il certificato con **CN = agent_id** (il subject richiesto viene ignorato).
4. L'agente resta `pending` finché un operatore non lo **approva**. Il passo manuale evita
   che un token trapelato permetta di registrare macchine estranee.
5. Caddy verifica il certificato contro `agent-ca.crt` e passa al backend il fingerprint
   SHA-256 nell'header `X-Client-Cert-Fingerprint`, sovrascrivendo sempre quello inviato
   dal client. Il backend cerca l'agente per fingerprint, quindi la **revoca** ha effetto
   immediato senza bisogno di CRL.

Altre misure:
- il container `api` non pubblica porte;
- l'host `console.` risponde 404 su `/agent/*` e scarta l'header del fingerprint;
- la chiave della CA sta solo nel volume `agent_ca` (Caddy lo monta in sola lettura e legge solo il `.crt`).

### Utenti e tenant

| Ruolo | Ambito | Può |
|---|---|---|
| `superadmin` | globale | tutto, su tutti i tenant; utenti, rilasci, shell |
| `operator` | globale o per tenant | monitoring, task del catalogo, presa in carico |
| `tenant_admin` | un tenant *(futuro, clienti)* | come viewer + notifiche del proprio tenant |
| `tenant_viewer` | un tenant *(futuro, clienti)* | sola lettura |

- Login con **Entra ID** (OIDC, MFA via accesso condizionale). Gli utenti sono pre-registrati:
  un login valido senza utente corrispondente riceve 403.
- Ogni tabella operativa ha `tenant_id`. Il filtro è applicato in un solo punto
  (`CurrentUser.tenant_scope()`). Gli oggetti di altri tenant restituiscono 404, non 403.
- **Da fare**: Row-Level Security di PostgreSQL come seconda protezione.
- Tutte le azioni vanno nella tabella `audit_log` (append-only).

### Esecuzione remota *(fase 3)*

| Livello | Cosa | Protezione |
|---|---|---|
| Task del catalogo | script/rsync salvati, schedulati **sull'agente** | ruolo `operator` |
| Comando custom | comando singolo con output | sessione elevata |
| Shell interattiva | xterm.js ↔ PTY via WebSocket | sessione elevata + registrazione |

**Sessione elevata**:
- secondo fattore personale (TOTP o passkey), separato dal login;
- motivo obbligatorio;
- durata di 15–30 minuti, valida su un solo tenant;
- notifica all'amministratore;
- registrazione integrale della sessione.

**Consenso locale**: shell e comandi custom funzionano solo se abilitati **in
installazione** (`bc-agent enroll -allow-shell`, `msiexec ... ALLOW_SHELL=1`). La console
non può modificare questo flag.

**Fase 2 (hardening)**: ogni comando firmato nel browser con la chiave personale
dell'operatore. Così nemmeno chi compromettesse il server della console potrebbe
inviare comandi.

### Aggiornamenti dell'agente *(fase 4)*

1. La CI firma i binari (Ed25519, chiave in un GitHub Environment protetto).
2. L'agente verifica la firma con la chiave pubblica incorporata nel binario.
3. Rollout a gruppi: canary → pilota → tutti.
4. Rollback automatico se il nuovo binario non si registra entro N minuti.
5. Su Windows l'aggiornamento avviene tramite *MajorUpgrade* dell'MSI.

## Protocollo agente ↔ console (v1)

Schemi: `console/backend/app/schemas/agent.py` (lato Go: `agent/internal/client`).

| Endpoint | Auth | Scopo |
|---|---|---|
| `POST /agent/v1/enroll` | token | CSR, restituisce certificato + CA |
| `POST /agent/v1/heartbeat` | mTLS | stato, versione, ruoli, capacità; risposta: stato approvazione, versione desiderata |
| `POST /agent/v1/inventory` | mTLS, approvato | modello, firmware, seriale, volumi, dischi... |
| `POST /agent/v1/metrics` | mTLS, approvato | batch di metriche |
| `POST /agent/v1/events` | mTLS, approvato | sessioni Veeam, eventi hardware, esiti dei task |
| `GET /agent/v1/ws` | mTLS *(fase 3)* | canale di controllo: comandi, flussi shell |

Envelope comune: `{msg_id, seq, ts, payload}`. `msg_id` rende idempotente il reinvio
dei dati bufferizzati.

### Metriche

| Nome | Label | Fonte |
|---|---|---|
| `disk.used_pct`, `disk.total_bytes`, `disk.free_bytes` | `mount` | tutti |
| `hw.sys_temp_c`, `hw.cpu_temp_c` | – | QNAP `getsysinfo` |
| `hw.disk_temp_c`, `hw.disk_smart_ok` | `slot` | QNAP `getsysinfo hdtmp/hdsmart` |
| `hw.fan_rpm` | `fan` | QNAP `getsysinfo sysfan` |
| `raid.healthy` | `array` | `/proc/mdstat` (esclusi md9/13/256/321/322), `zpool list` |

## Problemi (vista unica)

- Gli agenti inviano **stati grezzi**; il backend e il worker valutano le regole e
  aprono, aggiornano o chiudono i `problems`.
- **Chiave di dedup** = dispositivo + check + label: al massimo un problema non
  risolto per chiave (indice unico parziale).
- **Stati**: `open → acknowledged → resolved`. La risoluzione è automatica quando il
  check torna OK; un peggioramento a *critical* riapre il problema.
- **Soglie a cascata** (`check_rules`): dispositivo > tenant > globale > default nel codice.
- I **PC** non generano `agent.offline`. Per loro servirà "nessun backup riuscito da N giorni".
- `GET /api/v1/problems` mostra tutti i tenant accessibili in un'unica lista;
  `GET /api/v1/problems/summary` dà i conteggi per tenant.

## Veeam *(fase 2)*

| Target | Fonte primaria | Note |
|---|---|---|
| B&R v11 | PowerShell (`Veeam.Backup.PowerShell`, PS 5.1) | REST v11 troppo limitata; v11 è fuori supporto |
| B&R v12.x | REST API `https://localhost:9419` | fallback PowerShell |
| B&R v13 | REST API | modulo PS su PowerShell 7 (da verificare); appliance Linux → agente in "collector remoto" su un Windows del sito |
| Veeam Agent for Windows | Event Log Application (sessioni) | `Veeam.Agent.Configurator.exe -export` per la config, `Veeam.EndPoint.Manager.exe /backup` per il backup on demand |

L'output è normalizzato (job, sessione, esito, durata, dimensione, repository), così la
console non deve conoscere le versioni.

## Roadmap

1. **MVP** *(in corso)*:
   - ✅ enrollment mTLS, heartbeat, inventario, metriche, problemi, multi-tenant;
   - ⬜ frontend;
   - ⬜ login Entra ID end-to-end;
   - ⬜ notifiche email/Teams;
   - ⬜ QPKG;
   - ⬜ buffer locale nell'agente.
2. **Veeam**: collector B&R (v11/v12/v13), collector Veeam Agent for Windows, MSI, servizio Windows.
3. **Esecuzione**: catalogo task, rsync schedulati, comandi custom e shell con sessione elevata.
4. **Aggiornamenti**: update remoto firmato, canary, rollback.
5. **Fase 2**: firma dei comandi lato operatore, accesso clienti, report, RLS.
