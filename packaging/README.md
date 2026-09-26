# Packaging (da fare)

## QPKG (QNAP) – `qpkg/`

- Costruito con QDK, un pacchetto per architettura: `x86_64`, `arm_64`, `arm-x41`/`arm-x31`.
- Lo script di servizio avvia `bc-agent run -config $QPKG_ROOT/etc/agent.json`.
  Le configurazioni stanno nella directory del QPKG, perché la root del QNAP è un ramdisk.
- Installazione: App Center → "Installa manualmente", oppure `sh bc-agent_<arch>.qpkg` via SSH.
- Enrollment: `bc-agent enroll` via SSH, oppure QPKG già configurato scaricato dalla console.
- QTS 5 blocca i pacchetti non firmati: abilitare "consenti app senza firma digitale"
  o firmare tramite il programma QNAP.

## MSI (Windows) – `msi/`

- WiX Toolset v5, *MajorUpgrade* per gli aggiornamenti.
- Proprietà per l'installazione silenziosa:
  ```bash
  msiexec /i bc-agent.msi SERVER_URL=https://agents.<dominio> ENROLL_TOKEN=<token> ALLOW_SHELL=0 /qn
  ```
- Registra `bc-agent` come servizio Windows (LocalSystem, riavvio automatico) ed esegue
  l'enrollment durante l'installazione.
- Firma Authenticode tramite Azure Trusted Signing.
