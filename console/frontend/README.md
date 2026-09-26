# Frontend

SPA React + Vite + TypeScript. Login Entra ID con MSAL (oppure utente di sviluppo con
`BC_AUTH_MODE=dev`). La configurazione di login arriva a runtime da
`GET /api/v1/auth-config`, quindi la stessa build va bene per ogni ambiente.

In produzione la build è servita da un nginx non privilegiato nel container `frontend`
(`Dockerfile`, `nginx.conf`). L'nginx di bordo (`edge/`) ci instrada `/`.

## Pagine

- **Problemi**: vista unica trasversale ai tenant, riepilogo per tenant, presa in carico
- **Dispositivi**: elenco filtrabile; dettaglio con agente, volumi, dischi/RAID QNAP e
  grafico dello spazio occupato (24h / 7 / 30 giorni)
- **Approvazioni**: agenti enrollati in attesa di conferma
- **Tenant e siti**: creazione, token di installazione con i comandi già pronti

## Sviluppo

Con lo stack compose di sviluppo avviato (vedi README principale):

```bash
npm install
BC_API_URL=https://backup-console.localhost:18443 npm run dev
```

Dati di esempio: `console/backend/.venv/bin/python console/deploy/dev/seed_demo.py --ca edge/dev-ca.crt`.
