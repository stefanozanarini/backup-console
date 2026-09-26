# Packaging (da fare)

## QPKG (QNAP) – `qpkg/`

Un pacchetto per architettura, costruito con il QDK ufficiale QNAP in un container:

| File | NAS |
|---|---|
| `BackupConsoleAgent_<ver>_x86_64.qpkg` | Intel/AMD (TS-x53, x64, x73, h-series...) |
| `BackupConsoleAgent_<ver>_arm_64.qpkg` | ARM 64 bit (AL-324, Cortex-A55: TS-x32X, TS-x33...) |
| `BackupConsoleAgent_<ver>_arm-x41.qpkg` | Annapurna AL-314/AL-212 (TS-x31P/X, x31+) |
| `BackupConsoleAgent_<ver>_arm-x31.qpkg` | ARMv7 meno recenti (TS-x31) |

I vecchi ARMv5 (`arm-x19`, TS-x19/x20/x21) non sono supportati. Se non sai quale
scegliere, via SSH `uname -m` (`x86_64`, `aarch64`, `armv7l`) e `getcfg System Platform
-f /etc/config/uLinux.conf`.

Build locale (i binari li produce la CI, oppure `go build` in `agent/`):

```bash
packaging/qpkg/build.sh 0.1.0 agent/dist dist/qpkg
```

### Installazione su un NAS

1. **App Center → Impostazioni → Generale**: abilitare "Consenti l'installazione di
   applicazioni senza firma digitale valida". Il pacchetto non è firmato da QNAP; per
   firmarlo serve aderire al programma di code signing QNAP.
2. **App Center → Installa manualmente** e scegliere il `.qpkg` dell'architettura giusta.
   In alternativa, via SSH:
   ```bash
   sh BackupConsoleAgent_0.1.0_x86_64.qpkg
   ```
3. In console: **Tenant e siti → Genera token di installazione**. Poi, via SSH come admin,
   incollare il comando mostrato:
   ```bash
   QPKG_DIR=$(/sbin/getcfg BackupConsoleAgent Install_Path -f /etc/config/qpkg.conf)
   sh "$QPKG_DIR/bc-agent.sh" enroll -server https://agents.<dominio> -token <TOKEN>
   ```
   L'agente parte subito e compare in **Approvazioni**.
4. Approvare il dispositivo in console.

### Uso

- `bc-agent.sh start|stop|restart|status`: lo stesso che fa App Center con avvio/arresto.
- `bc-agent.sh collect`: stampa i dati raccolti senza inviarli. Serve a verificare i
  parser su un modello nuovo.
- Log: `$QPKG_DIR/log/agent.log`, ruotato a 5 MB.
- Configurazione, chiave e certificato: `$QPKG_DIR/etc/`. Non fanno parte del pacchetto,
  quindi restano durante gli aggiornamenti (l'installer QDK estrae sopra la directory
  esistente).
- Un supervisore riavvia l'agente dopo 15 secondi se termina.
- Senza registrazione lo start non fallisce: segnala solo che manca l'enroll.
- Disinstallazione da App Center: ferma il servizio e rimuove la directory, compresa
  l'identità dell'agente. Poi va revocato il dispositivo in console.

## MSI (Windows) – `msi/`

- WiX Toolset v5, *MajorUpgrade* per gli aggiornamenti.
- Proprietà per l'installazione silenziosa:
  ```bash
  msiexec /i bc-agent.msi SERVER_URL=https://agents.<dominio> ENROLL_TOKEN=<token> ALLOW_SHELL=0 /qn
  ```
- Registra `bc-agent` come servizio Windows (LocalSystem, riavvio automatico) ed esegue
  l'enrollment durante l'installazione.
- Firma Authenticode tramite Azure Trusted Signing.
