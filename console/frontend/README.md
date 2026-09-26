# Frontend (da fare)

SPA React + Vite, login Entra ID con MSAL. La build statica è servita da Caddy
sull'host `console.<dominio>`.

Pagine MVP:
- **Problemi**: vista unica trasversale ai tenant (`GET /api/v1/problems`, `/problems/summary`)
- **Dispositivi** e dettaglio (`GET /api/v1/devices`, `/devices/{id}`)
- **Agenti in attesa** di approvazione
- **Tenant e siti**, generazione dei token di enrollment
