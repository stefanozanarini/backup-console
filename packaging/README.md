# Packaging

## QPKG (QNAP) – `qpkg/`

Un pacchetto per architettura, costruito con il QDK ufficiale QNAP in un container:

| File | NAS |
|---|---|
| `BackupConsoleAgent_<ver>_x86_64.qpkg` | Intel/AMD (TS-x53, x64, x73, h-series...) |
| `BackupConsoleAgent_<ver>_arm_64.qpkg` | ARM 64 bit (AL-324, Cortex-A55: TS-x32X, TS-x33...) |
| `BackupConsoleAgent_<ver>_arm-x41.qpkg` | Annapurna AL-314/AL-212 (TS-x31P/X, x31+) |
| `BackupConsoleAgent_<ver>_arm-x31.qpkg` | ARMv7 meno recenti (TS-x31) |

La corrispondenza modello → architettura è indicativa: va verificata sul NAS. I vecchi
ARMv5 (`arm-x19`, TS-x19/x20/x21) non sono supportati. Se non sai quale
scegliere, via SSH `uname -m` (`x86_64`, `aarch64`, `armv7l`) e `getcfg System Platform
-f /etc/config/uLinux.conf`.

Build locale (i binari li produce la CI, oppure `go build` in `agent/`):

```bash
packaging/qpkg/build.sh 0.1.0 agent/dist dist/qpkg
```

### Installazione su un NAS

Il modo più semplice: in console, **Tenant e siti → Nuovo token di installazione**, poi incollare
via SSH il comando "NAS QNAP". Scarica il QPKG giusto dalla console, lo installa e registra
l'agente. Serve comunque il passo 1 (app non firmate).

A mano:

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
   L'agente parte subito e compare in **Approvazioni**. L'esecuzione remota di comandi è attiva:
   per un NAS dove non la si vuole, aggiungere `-disable-remote-exec`.
4. Approvare il dispositivo in console.

### Uso

- `bc-agent.sh start|stop|restart|status`: lo stesso che fa App Center con avvio/arresto.
- `bc-agent.sh collect`: stampa i dati raccolti senza inviarli. Serve a verificare i
  parser su un modello nuovo.
- Log: `$QPKG_DIR/log/agent.log`, ruotato a 5 MB.
- Configurazione, chiave e certificato: `$QPKG_DIR/etc/`. Non fanno parte del pacchetto,
  quindi restano durante gli aggiornamenti (l'installer QDK estrae sopra la directory
  esistente).
- Un supervisore riavvia l'agente dopo 15 secondi se termina, oppure subito dopo un aggiornamento
  (codice di uscita 75).
- Aggiornamenti dalla console: il binario viene sostituito in `$QPKG_DIR/bc-agent` e la versione
  precedente resta in `bc-agent.prev`, per il rollback automatico. Per passare a una nuova
  versione dello script di servizio serve invece reinstallare il QPKG.
- Senza registrazione lo start non fallisce: segnala solo che manca l'enroll.
- Disinstallazione da App Center: ferma il servizio e rimuove la directory, compresa
  l'identità dell'agente. Poi va revocato il dispositivo in console.

## Windows

Per ora l'installer è l'eseguibile stesso: `bc-agent.exe install -server … -token …` (vedi il
README principale). Si distribuisce anche via GPO, Intune o script.

Da fare:
- **MSI** (WiX v5, *MajorUpgrade*) con le proprietà `SERVER_URL`, `ENROLL_TOKEN`,
  `DISABLE_REMOTE_EXEC`, che chiami `bc-agent.exe install`;
- **firma Authenticode** (Azure Trusted Signing), per evitare gli avvisi di SmartScreen/Defender.
