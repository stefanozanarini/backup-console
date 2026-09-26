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
| Frontend | React + Vite + TypeScript, build inclusa nell'immagine Caddy | `console/frontend` |
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

### Esecuzione remota

- **Attiva di default** su ogni agente. Si disattiva solo in installazione
  (`-disable-remote-exec`, sia QPKG sia Windows): la console lo mostra ma non può modificarlo.
- **Sessione elevata** obbligatoria per lanciare un comando:
  - secondo fattore **TOTP personale** (app di autenticazione), separato dal login e configurato
    al primo utilizzo. Il seed è cifrato nel DB con una chiave locale (`secret.key` nel volume
    della CA). Ogni codice vale una sola volta;
  - **motivo** obbligatorio, sessione di **15 minuti** (token solo nella scheda del browser);
  - reset del TOTP di un utente: `python -m app.cli reset-totp <email>`.
- **Consegna**: l'agente fa long-poll su `GET /agent/v1/commands/next` (attesa fino a 25 s),
  quindi il comando parte entro circa un secondo. Il server rilascia la connessione al DB durante
  l'attesa.
- **Esecuzione**:
  - shell: `sh` su Linux/QNAP, PowerShell su Windows (`bash`, `pwsh`, `cmd` a scelta);
  - stdout e stderr inviati a blocchi ogni secondo, fino a 1 MB;
  - al timeout viene terminato l'intero albero di processi;
  - massimo 2 comandi in parallelo per agente.
- **Tracciabilità**: audit in console (utente, motivo, comando, IP) e riga nel log locale del
  dispositivo. Comandi mai ritirati → `expired` dopo 1 ora; senza esito → `timeout`.
- **Fase 2 (hardening)**: firma dei comandi nel browser con la chiave personale dell'operatore,
  così nemmeno un server della console compromesso potrebbe inviare comandi.

### Aggiornamenti dell'agente

1. **Build**: la CI compila i binari con la chiave pubblica di verifica incorporata
   (`-X .../upgrade.PublicKey=`, variabile di repository `AGENT_RELEASE_PUBLIC_KEY`).
2. **Firma**: su un tag `agent-vX.Y.Z` la CI firma i binari con `bc-release sign`. La chiave
   privata è nel secret `AGENT_RELEASE_SIGNING_KEY`, nell'Environment protetto `agent-release`,
   e **non è mai sulla console**. Il risultato è una GitHub Release con binari e `manifest.json`.
3. **Caricamento**: in console, **Aggiornamenti agente → Carica rilascio**. La console controlla
   hash e firma (se `BC_RELEASE_PUBLIC_KEY` è impostata) e salva i binari nel volume `releases`.
4. **Lancio**: "Aggiorna" su un dispositivo, su un tenant o su tutti. Viene messo in coda un
   comando `upgrade` per ogni agente approvato di piattaforma compatibile.
5. **Sull'agente**:
   - download nella stessa cartella;
   - verifica di dimensione, SHA-256 e **firma Ed25519** (la chiave è nel binario: la console da
     sola non può far installare codice arbitrario);
   - **prova** `bc-agent.new version`;
   - scambio `bc-agent` → `.prev`, `.new` → `bc-agent`;
   - uscita con codice 75: il supervisore QPKG o il servizio Windows lo riavvia subito.
6. **Esito**: il comando risulta riuscito quando l'agente torna con la nuova versione.
7. **Rollback automatico**: se la nuova versione non arriva a un heartbeat approvato (watchdog di
   10 minuti; oltre 3 avvii), si ripristina `.prev`. La console apre il problema "Aggiornamento
   fallito".

## Protocollo agente ↔ console (v1)

Schemi: `console/backend/app/schemas/agent.py` (lato Go: `agent/internal/client`).

| Endpoint | Auth | Scopo |
|---|---|---|
| `POST /agent/v1/enroll` | token | CSR, restituisce certificato + CA |
| `POST /agent/v1/heartbeat` | mTLS | stato, versione, ruoli, capacità; risposta: stato approvazione, versione desiderata |
| `POST /agent/v1/inventory` | mTLS, approvato | modello, firmware, seriale, volumi, dischi... |
| `POST /agent/v1/metrics` | mTLS, approvato | batch di metriche |
| `POST /agent/v1/events` | mTLS, approvato | eventi (es. esito aggiornamenti, rollback) |
| `POST /agent/v1/veeam` | mTLS, approvato | report Veeam normalizzato (job, repository, licenza) |
| `GET /agent/v1/commands/next` | mTLS, approvato | long-poll: prossimo comando in coda (204 se nessuno) |
| `POST /agent/v1/commands/{id}/output` · `/result` | mTLS, approvato | output a blocchi ed esito |
| `GET /agent/v1/releases/{ver}/{piattaforma}` | mTLS, approvato | download del binario di aggiornamento |

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

## Veeam

Il collector gira sul server VBR, o sul PC per Veeam Agent, ed esegue script PowerShell
incorporati nell'agente, come LocalSystem. Il gruppo Administrators locale è amministratore
Veeam di default, quindi **non servono credenziali**. Il report parte ogni 15 minuti.

| Target | Fonte | Note |
|---|---|---|
| B&R v11 / v12 | modulo `Veeam.Backup.PowerShell` su Windows PowerShell 5.1 | `Get-VBRJob` + `FindLastSession()`, `Get-VBRComputerBackupJob`, `Get-VBRBackupCopyJob`, repository e scale-out, licenza |
| B&R v13 | come sopra; se il modulo non si carica su PS 5.1 si ripiega su `pwsh` (PowerShell 7) | da verificare sulla build installata; l'appliance Linux (VSA) richiederà un collector REST remoto |
| Veeam Agent for Windows (standalone) | Event Log (canale "Veeam Agent", evento 190 = fine job) | ID e testo dei messaggi da verificare sulle versioni installate |

- Gli script restituiscono JSON già normalizzato: date ISO UTC, esiti `Success/Warning/Failed/None`.
- Ogni sezione è isolata: un cmdlet mancante in una versione finisce in `errors`, mostrato in
  console, senza bloccare il resto.
- Problemi generati:
  - `veeam.job_result`: fallito → critico, avvisi → attenzione;
  - `veeam.last_success_age_h`: nessun successo da 36/72 ore (soglie a cascata);
  - `veeam.repo_used_pct`: 85/95%;
  - `veeam.license`: scadenza a meno di 30 giorni o già scaduta.
- I job disabilitati sono ignorati; quelli eliminati in Veeam spariscono dalla console.
- `bc-agent collect` sul server stampa il report senza inviarlo: è il primo test da fare su
  ogni versione di Veeam.

## Roadmap

1. **MVP** *(in corso)*:
   - ✅ enrollment mTLS, heartbeat, inventario, metriche, problemi, multi-tenant;
   - ✅ frontend (problemi, dispositivi con grafico storico, approvazioni, tenant e token);
   - ✅ QPKG per x86_64, arm_64, arm-x41, arm-x31 con supervisore e log;
   - ✅ collector Veeam (B&R v11/v12/v13, Veeam Agent), servizio Windows con `bc-agent install`;
   - ✅ esecuzione remota con sessione elevata TOTP, aggiornamenti firmati con rollback;
   - ⬜ login Entra ID end-to-end;
   - ⬜ notifiche email/Teams;
   - ⬜ buffer locale nell'agente.
2. **Veeam**: collector B&R (v11/v12/v13), collector Veeam Agent for Windows, MSI, servizio Windows.
3. **Esecuzione**: catalogo task, rsync schedulati, comandi custom e shell con sessione elevata.
4. **Aggiornamenti**: update remoto firmato, canary, rollback.
5. **Fase 2**: firma dei comandi lato operatore, accesso clienti, report, RLS.
