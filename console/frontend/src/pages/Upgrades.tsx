import { useMemo, useState } from "react";
import { api, downloadFile, useApi } from "../api";
import { Empty, ErrorBox } from "../components";
import { useConsole } from "../context";
import { bytes, dateTime } from "../format";
import type { Device, Release } from "../types";

export default function Upgrades() {
  const { isSuperadmin, tenants } = useConsole();
  const releases = useApi<Release[]>("/releases", 30000);
  const devices = useApi<Device[]>("/devices?agent_status=approved", 30000);
  const [scope, setScope] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const versions = useMemo(() => {
    const m = new Map<string, number>();
    for (const d of devices.data ?? []) {
      const v = d.agent?.version ?? "?";
      m.set(v, (m.get(v) ?? 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[0].localeCompare(a[0], undefined, { numeric: true }));
  }, [devices.data]);

  async function rollout(version: string) {
    const target = scope ? tenants.find((t) => t.id === scope)?.name : "tutti i tenant";
    if (!confirm(`Aggiornare a ${version} gli agenti di ${target}?`)) return;
    setError(null);
    setMessage(null);
    try {
      const r = await api<{ queued: number; skipped: Record<string, number> }>("/upgrades", {
        method: "POST",
        body: { version, tenant_id: scope || null },
      });
      const skipped = Object.entries(r.skipped).map(([k, v]) => `${v} ${k}`).join(", ");
      setMessage(`${r.queued} aggiornamenti in coda${skipped ? ` (esclusi: ${skipped})` : ""}. L'esito compare nello storico comandi di ogni dispositivo.`);
    } catch (e) {
      setError(e as Error);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Aggiornamenti agente</h1>
          <div className="sub">Rilasci firmati caricati in console e versioni installate sui dispositivi</div>
        </div>
      </div>
      <ErrorBox error={error ?? releases.error} />
      {message && <div className="notice" style={{ marginBottom: 12 }}>{message}</div>}

      <div className="grid-2">
        <section className="card">
          <h2>Versioni in uso</h2>
          {versions.length === 0 ? <div className="muted">Nessun agente approvato.</div> : (
            <table>
              <tbody>
                {versions.map(([v, n]) => (
                  <tr key={v}><td className="mono">{v}</td><td className="num">{n} agenti</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        {isSuperadmin && <UploadRelease onDone={() => void releases.reload()} />}
      </div>

      {isSuperadmin && (
        <div className="filters">
          <span className="muted">Perimetro dell'aggiornamento:</span>
          <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Perimetro">
            <option value="">Tutti i tenant</option>
            {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Versione</th><th>Piattaforme</th><th>Caricato</th><th className="num">Agenti</th><th>Note</th><th /></tr>
          </thead>
          <tbody>
            {(releases.data ?? []).map((r) => (
              <tr key={r.version}>
                <td className="mono">{r.version}</td>
                <td>
                  {r.assets.map((a) => (
                    <button key={a.platform} className="chip chip-btn" title={`Scarica · ${bytes(a.size)} · sha256 ${a.sha256}`}
                      onClick={() => downloadFile(`/releases/${r.version}/assets/${a.platform}`, a.platform).catch((e: Error) => setError(e))}>
                      ⭳ {a.platform}
                    </button>
                  ))}
                </td>
                <td className="nowrap muted">{dateTime(r.created_at)}</td>
                <td className="num">{r.agents}</td>
                <td className="muted">{r.notes}</td>
                <td className="num">
                  {isSuperadmin && <button className="btn small primary" onClick={() => void rollout(r.version)}>Aggiorna</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {releases.data?.length === 0 && <Empty>Nessun rilascio caricato</Empty>}
      </div>
      {isSuperadmin && <CollectorPacks />}
    </>
  );
}

function UploadRelease({ onDone }: { onDone: () => void }) {
  const [manifest, setManifest] = useState<File | null>(null);
  const [files, setFiles] = useState<FileList | null>(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!manifest || !files?.length) return;
    const form = new FormData();
    form.append("manifest", manifest);
    Array.from(files).forEach((f) => form.append("files", f));
    if (notes.trim()) form.append("notes", notes.trim());
    setBusy(true);
    setError(null);
    try {
      await api("/releases", { form });
      setManifest(null);
      setFiles(null);
      setNotes("");
      (e.target as HTMLFormElement).reset();
      onDone();
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Carica rilascio</h2>
      <form className="stack" onSubmit={submit}>
        <div className="notice">
          File della GitHub Release (binari, QPKG e <code>manifest.json</code> prodotti da <code>bc-release sign</code>).
          L'agente installa solo binari firmati con la chiave incorporata al momento della build; l'ultima versione
          caricata è quella scaricata dai comandi di installazione.
        </div>
        <label className="field">manifest.json
          <input type="file" accept=".json,application/json" required onChange={(e) => setManifest(e.target.files?.[0] ?? null)} />
        </label>
        <label className="field">Binari e QPKG (bc-agent-linux-amd64, …-windows-amd64.exe, BackupConsoleAgent_*.qpkg)
          <input type="file" multiple required onChange={(e) => setFiles(e.target.files)} />
        </label>
        <label className="field">Note
          <input type="text" maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
        <ErrorBox error={error} />
        <div className="btn-row" style={{ justifyContent: "flex-end" }}>
          <button className="btn primary" type="submit" disabled={busy}>{busy ? "Caricamento…" : "Carica"}</button>
        </div>
      </form>
    </section>
  );
}

interface CollectorPack {
  version: string;
  sha256: string;
  probes: string[];
  notes: string | null;
  active: boolean;
  created_at: string;
  activated_at: string | null;
  size: number;
}

/** Pacchetti di raccolta: la logica di raccolta, aggiornata senza aggiornare gli agenti. */
function CollectorPacks() {
  const packs = useApi<CollectorPack[]>("/collector-packs", 60000);
  const [error, setError] = useState<Error | null>(null);

  async function activate(v: string) {
    if (!window.confirm(`Rendere attivo il pacchetto di raccolta ${v}? Gli agenti lo useranno entro qualche minuto.`)) return;
    setError(null);
    try {
      await api(`/collector-packs/${v}/activate`, { method: "POST" });
      await packs.reload();
    } catch (e) {
      setError(e as Error);
    }
  }

  return (
    <section className="card" style={{ marginTop: 16 }}>
      <h2>Pacchetto di raccolta</h2>
      <p className="muted small" style={{ marginTop: 0 }}>
        Script e probe con cui gli agenti raccolgono i dati (Veeam, Uranium…). Si pubblicano dalla CI con un tag
        <code> pack-vX.Y.Z</code>, firmati con la chiave dei rilasci: gli agenti li scaricano da soli e li eseguono
        solo se la firma è valida. Qui si vede quale è attivo e si può tornare a una versione precedente.
      </p>
      <ErrorBox error={packs.error ?? error} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>Versione</th><th>Probe</th><th>Caricato</th><th>Note</th><th className="num">Dimensione</th><th /></tr></thead>
          <tbody>
            {(packs.data ?? []).map((p) => (
              <tr key={p.version}>
                <td className="nowrap">
                  <span className="mono">{p.version}</span>
                  {p.active && <span className="badge good" style={{ marginLeft: 6 }}>attivo</span>}
                </td>
                <td className="small">{p.probes.join(", ")}</td>
                <td className="nowrap" title={dateTime(p.created_at)}>{dateTime(p.created_at)}</td>
                <td className="muted small">{p.notes}</td>
                <td className="num">{bytes(p.size)}</td>
                <td className="num">
                  {!p.active && <button className="btn small" onClick={() => void activate(p.version)}>Attiva</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {packs.data?.length === 0 && <Empty>Nessun pacchetto caricato: gli agenti usano la raccolta integrata</Empty>}
      </div>
    </section>
  );
}
