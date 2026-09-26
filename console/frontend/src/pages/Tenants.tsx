import { useState } from "react";
import { api, useApi } from "../api";
import { ErrorBox } from "../components";
import { useConsole } from "../context";
import { dateTime } from "../format";
import type { EnrollmentToken, Site, Tenant } from "../types";

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

function SiteRow({ site, writable }: { site: Site; writable: boolean }) {
  const { session } = useConsole();
  const [token, setToken] = useState<EnrollmentToken | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const server = session.agentsUrl || "https://agents.<dominio>";

  async function generate() {
    setError(null);
    try {
      setToken(await api<EnrollmentToken>(`/sites/${site.id}/enrollment-tokens`, { method: "POST", body: {} }));
    } catch (err) {
      setError(err as Error);
    }
  }

  const qnapCmd = token &&
    `QPKG_DIR=$(/sbin/getcfg BackupConsoleAgent Install_Path -f /etc/config/qpkg.conf)\n` +
    `sh "$QPKG_DIR/bc-agent.sh" enroll -server ${server} -token ${token.token}`;
  const winCmd = token && `"C:\\Program Files\\BackupConsoleAgent\\bc-agent.exe" enroll -server ${server} -token ${token.token}`;

  return (
    <div style={{ padding: "8px 0", borderTop: "1px solid var(--border)" }}>
      <div className="form-row" style={{ justifyContent: "space-between" }}>
        <span>{site.name}</span>
        {writable && <button className="btn small" onClick={() => void generate()}>Genera token di installazione</button>}
      </div>
      <ErrorBox error={error} />
      {token && (
        <div className="token-box">
          <div className="notice">
            Token monouso, valido fino al {dateTime(token.expires_at)}. Viene mostrato una sola volta.
          </div>
          <div className="label" style={{ marginTop: 10 }}>NAS QNAP (SSH, dopo aver installato il QPKG) <CopyButton text={qnapCmd!} /></div>
          <pre className="mono">{qnapCmd}</pre>
          <div className="label">Windows (prompt amministratore) <CopyButton text={winCmd!} /></div>
          <pre className="mono">{winCmd}</pre>
          <div className="label">Solo token <CopyButton text={token.token} /></div>
          <pre className="mono">{token.token}</pre>
        </div>
      )}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn small" type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          window.setTimeout(() => setDone(false), 1500);
        });
      }}
    >
      {done ? "Copiato" : "Copia"}
    </button>
  );
}
