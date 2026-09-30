import { useState } from "react";
import { api, useApi } from "../api";
import { TenantBackupWindow } from "../BackupWindow";
import { CopyButton, ErrorBox } from "../components";
import { useConsole } from "../context";
import { dateTime } from "../format";
import type { EnrollmentToken, Site, Tenant, TokenInfo } from "../types";

export default function Tenants() {
  const { tenants, isSuperadmin, reloadTenants } = useConsole();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<Error | null>(null);

  async function createTenant(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api("/tenants", { method: "POST", body: { name: name.trim(), slug: slug.trim() } });
      setName("");
      setSlug("");
      await reloadTenants();
    } catch (err) {
      setError(err as Error);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tenant e siti</h1>
          <div className="sub">Clienti, sedi e token per installare nuovi agenti</div>
        </div>
      </div>
      <ErrorBox error={error} />
      {isSuperadmin && (
        <form className="card form-row" onSubmit={createTenant} style={{ marginBottom: 16 }}>
          <input
            type="text" required placeholder="Nome cliente" value={name} aria-label="Nome tenant"
            onChange={(e) => {
              setName(e.target.value);
              setSlug(e.target.value.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 63));
            }}
          />
          <input
            type="text" required placeholder="identificativo" value={slug} aria-label="Slug"
            pattern="[a-z0-9][a-z0-9\-]{1,62}" onChange={(e) => setSlug(e.target.value)}
          />
          <button className="btn primary" type="submit">Nuovo tenant</button>
        </form>
      )}
      {tenants.map((t) => <TenantCard key={t.id} tenant={t} />)}
    </>
  );
}

function TenantCard({ tenant }: { tenant: Tenant }) {
  const { canWrite } = useConsole();
  const sites = useApi<Site[]>(`/tenants/${tenant.id}/sites`);
  const [siteName, setSiteName] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const writable = canWrite(tenant.id);

  async function createSite(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api(`/tenants/${tenant.id}/sites`, { method: "POST", body: { name: siteName.trim() } });
      setSiteName("");
      await sites.reload();
    } catch (err) {
      setError(err as Error);
    }
  }

  return (
    <section className="card">
      <h2>{tenant.name} <span className="chip mono">{tenant.slug}</span></h2>
      <ErrorBox error={sites.error ?? error} />
      {(sites.data ?? []).map((s) => <SiteRow key={s.id} site={s} writable={writable} />)}
      <TenantBackupWindow tenantId={tenant.id} writable={writable} />
      {sites.data?.length === 0 && <div className="muted" style={{ marginBottom: 12 }}>Nessun sito.</div>}
      {writable && (
        <form className="form-row" onSubmit={createSite} style={{ marginTop: 12 }}>
          <input type="text" required placeholder="Nuovo sito (es. Sede Bologna)" value={siteName} onChange={(e) => setSiteName(e.target.value)} aria-label="Nome sito" />
          <button className="btn" type="submit">Aggiungi sito</button>
        </form>
      )}
    </section>
  );
}

/** Comandi di installazione: scaricano l'ultima versione con il token (header) e registrano. */
function installCommands(server: string, token: string) {
  const windows =
    `# PowerShell come amministratore. Idempotente: se l'agente è già installato non fa nulla.\n` +
    `if (-not (Get-Service BackupConsoleAgent -ErrorAction SilentlyContinue)) {\n` +
    `  $ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'\n` +
    `  [Net.ServicePointManager]::SecurityProtocol = 'Tls12'\n` +
    `  $t = '${token}'; $f = "$env:TEMP\\bc-agent.exe"\n` +
    `  Invoke-WebRequest -UseBasicParsing -Headers @{ 'X-Enroll-Token' = $t } -Uri '${server}/agent/v1/download/windows-amd64' -OutFile $f\n` +
    `  & $f install -server '${server}' -token $t\n` +
    `  if ($LASTEXITCODE -ne 0) { throw "installazione non riuscita (codice $LASTEXITCODE)" }\n` +
    // L'antivirus tiene aperto per qualche secondo l'eseguibile appena usato: pulizia
    // con qualche tentativo, senza errori se non riesce (è solo un file temporaneo).
    `  foreach ($i in 1..10) { try { Remove-Item $f -ErrorAction Stop; break } catch { Start-Sleep 2 } }\n` +
    `}`;
  const qnap =
    `# SSH come admin sul NAS (anche "sudo bash"): scarica il QPKG giusto, lo installa e registra l'agente.\n` +
    `T='${token}'; S='${server}'\n` +
    `case "$(uname -m)" in x86_64) A=x86_64;; aarch64) A=arm_64;; *) A=arm-x41;; esac\n` +
    `curl -fsS -H "X-Enroll-Token: $T" -o /tmp/bc-agent.qpkg "$S/agent/v1/download/qpkg-$A" || echo "download non riuscito"\n` +
    // L'installer dei QPKG può uscire con un codice diverso da 0 anche quando va a buon
    // fine: non si concatena con &&, si controlla dopo che il pacchetto risulti installato.
    `sh /tmp/bc-agent.qpkg; rm -f /tmp/bc-agent.qpkg\n` +
    `D="$(/sbin/getcfg BackupConsoleAgent Install_Path -f /etc/config/qpkg.conf)"\n` +
    `if [ -n "$D" ]; then sh "$D/bc-agent.sh" enroll -server "$S" -token "$T"; else echo "pacchetto non installato"; fi`;
  const synology =
    `# SSH sul NAS con un amministratore, poi "sudo -i". Installa in /usr/local/bc-agent con un servizio systemd.\n` +
    `T='${token}'; S='${server}'\n` +
    `case "$(uname -m)" in x86_64) A=amd64;; aarch64) A=arm64;; armv7l) A=armv7;; *) A=unsupported;; esac\n` +
    // /tmp su DSM è montata noexec: si scarica direttamente nella cartella di installazione.
    `mkdir -p /usr/local/bc-agent && curl -fsS -H "X-Enroll-Token: $T" -o /usr/local/bc-agent/bc-agent.new "$S/agent/v1/download/linux-$A" \\\n` +
    `  && chmod 755 /usr/local/bc-agent/bc-agent.new && /usr/local/bc-agent/bc-agent.new install -server "$S" -token "$T"\n` +
    `rm -f /usr/local/bc-agent/bc-agent.new`;
  return { windows, qnap, synology };
}

function SiteRow({ site, writable }: { site: Site; writable: boolean }) {
  const { session } = useConsole();
  const tokens = useApi<TokenInfo[]>(writable ? `/sites/${site.id}/enrollment-tokens` : null);
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState<EnrollmentToken | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const server = session.agentsUrl || "https://agents.<dominio>";

  async function revoke(t: TokenInfo) {
    if (!confirm(`Revocare il token "${t.note ?? "senza nota"}"? Gli agenti già registrati non sono toccati.`)) return;
    setError(null);
    try {
      await api(`/enrollment-tokens/${t.id}/revoke`, { method: "POST" });
      if (token?.id === t.id) setToken(null);
      await tokens.reload();
    } catch (err) {
      setError(err as Error);
    }
  }

  const cmds = token && installCommands(server, token.token);
  const active = tokens.data ?? [];

  return (
    <div style={{ padding: "8px 0", borderTop: "1px solid var(--border)" }}>
      <div className="form-row" style={{ justifyContent: "space-between" }}>
        <span>{site.name}</span>
        {writable && !open && <button className="btn small" onClick={() => setOpen(true)}>Nuovo token di installazione</button>}
      </div>
      <ErrorBox error={error} />
      {open && !token && (
        <NewTokenForm
          siteId={site.id}
          onCancel={() => setOpen(false)}
          onCreated={(t) => {
            setToken(t);
            void tokens.reload();
          }}
        />
      )}
      {token && cmds && (
        <div className="token-box">
          <div className="notice">
            Token per {token.max_uses === 1 ? "un dispositivo" : `${token.max_uses} dispositivi`}, valido fino al{" "}
            {dateTime(token.expires_at)}. Viene mostrato una sola volta: i dispositivi registrati andranno comunque
            approvati in <strong>Approvazioni</strong>.
          </div>
          <div className="label" style={{ marginTop: 10 }}>Windows: server Veeam e PC <CopyButton text={cmds.windows} /></div>
          <pre className="mono">{cmds.windows}</pre>
          <div className="label">NAS QNAP (SSH) <CopyButton text={cmds.qnap} /></div>
          <pre className="mono">{cmds.qnap}</pre>
          <div className="label">NAS Synology, DSM 7 (SSH) <CopyButton text={cmds.synology} /></div>
          <pre className="mono">{cmds.synology}</pre>
          <div className="notice" style={{ marginBottom: 12 }}>
            Synology, una sola volta dopo l'installazione: Pannello di controllo → Pianificazione attività → Crea →
            Attività attivata → Script definito dall'utente, utente <strong>root</strong>, evento <strong>Avvio</strong>,
            comando <code>/usr/local/bc-agent/ensure.sh</code>. Serve a ripristinare il servizio se un aggiornamento di DSM lo rimuove.
          </div>
          <div className="label">Solo token (installazione manuale) <CopyButton text={token.token} /></div>
          <pre className="mono">{token.token}</pre>
          <div className="btn-row" style={{ justifyContent: "flex-end" }}>
            <button className="btn small" onClick={() => { setToken(null); setOpen(false); }}>Chiudi</button>
          </div>
        </div>
      )}
      {active.length > 0 && (
        <div className="table-wrap" style={{ marginTop: 8 }}>
          <table>
            <thead><tr><th>Token attivi</th><th className="num">Utilizzi</th><th>Scadenza</th><th /></tr></thead>
            <tbody>
              {active.map((t) => (
                <tr key={t.id}>
                  <td>{t.note ?? <span className="muted">senza nota</span>}</td>
                  <td className="num">{t.uses} / {t.max_uses}</td>
                  <td className="nowrap muted">{dateTime(t.expires_at)}</td>
                  <td className="num"><button className="btn small danger" onClick={() => void revoke(t)}>Revoca</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function NewTokenForm(props: { siteId: string; onCancel: () => void; onCreated: (t: EnrollmentToken) => void }) {
  const [note, setNote] = useState("");
  const [bulk, setBulk] = useState(false);
  const [uses, setUses] = useState(10);
  const [hours, setHours] = useState(24);
  const [error, setError] = useState<Error | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      props.onCreated(
        await api<EnrollmentToken>(`/sites/${props.siteId}/enrollment-tokens`, {
          method: "POST",
          body: { note: note.trim() || null, max_uses: bulk ? uses : 1, valid_hours: hours },
        }),
      );
    } catch (err) {
      setError(err as Error);
    }
  }

  return (
    <form className="token-box stack" onSubmit={submit}>
      <label className="field">Nota (compare in Approvazioni)
        <input type="text" maxLength={200} value={note} placeholder={bulk ? "es. Rollout GPO PC amministrazione" : "es. VBR-SRV02"}
          onChange={(e) => setNote(e.target.value)} autoFocus />
      </label>
      <div className="form-row">
        <label className="form-row" style={{ gap: 6 }}>
          <input type="radio" checked={!bulk} onChange={() => setBulk(false)} /> Un dispositivo
        </label>
        <label className="form-row" style={{ gap: 6 }}>
          <input type="radio" checked={bulk} onChange={() => setBulk(true)} /> Rollout su più dispositivi
        </label>
      </div>
      <div className="form-row">
        {bulk && (
          <label className="field" style={{ flex: "0 0 auto" }}>Numero massimo di dispositivi
            <input type="number" min={2} max={500} value={uses} onChange={(e) => setUses(Number(e.target.value))} style={{ width: 120 }} />
          </label>
        )}
        <label className="field" style={{ flex: "0 0 auto" }}>Validità
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            <option value={2}>2 ore</option>
            <option value={24}>24 ore</option>
            <option value={72}>3 giorni</option>
            <option value={168}>7 giorni</option>
          </select>
        </label>
      </div>
      <ErrorBox error={error} />
      <div className="btn-row" style={{ justifyContent: "flex-end" }}>
        <button type="button" className="btn" onClick={props.onCancel}>Annulla</button>
        <button type="submit" className="btn primary">Genera token</button>
      </div>
    </form>
  );
}
