// Pulizia guidata dei repository Veeam: analisi sul server VBR, scelta dei backup da
// eliminare (solo job eliminati o disabilitati), eliminazione tramite Veeam.
import { useMemo, useState } from "react";
import { api, ApiError } from "./api";
import { CommandStatusBadge, FINAL, useCommand } from "./CommandPanel";
import { ErrorBox } from "./components";
import { useElevation } from "./elevation";
import { bytes, dateTime } from "./format";
import type { Command, DeviceDetail, VeeamBackupItem, VeeamCleanupResult, VeeamScanResult } from "./types";

const STATE_LABEL: Record<VeeamBackupItem["state"], string> = {
  active: "Job attivo",
  disabled: "Job disabilitato",
  orphan: "Job eliminato",
  unknown: "Stato non determinabile",
};

const RESULT_LABEL: Record<VeeamCleanupResult["results"][number]["status"], [string, string]> = {
  deleted: ["Eliminato", "good"],
  skipped: ["Saltato", "warning"],
  missing: ["Non trovato", ""],
  error: ["Errore", "critical"],
};

export default function VeeamCleanup({ device, writable }: { device: DeviceDetail; writable: boolean }) {
  const { require, end } = useElevation();
  const [days, setDays] = useState(30);
  const [scanId, setScanId] = useState<string | null>(null);
  const [cleanupId, setCleanupId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const scan = useCommand(scanId);
  const cleanup = useCommand(cleanupId);

  const result = scan?.status === "succeeded" ? (scan.result as unknown as VeeamScanResult | null) : null;
  const items = useMemo(() => {
    const list = [...(result?.items ?? [])];
    // Prima gli eliminabili, poi i più grandi.
    return list.sort((a, b) => Number(b.candidate) - Number(a.candidate) || b.size_bytes - a.size_bytes);
  }, [result]);
  const candidates = items.filter((i) => i.candidate);
  const beyond = items.filter((i) => (i.beyond_retention?.points ?? 0) > 0);
  const selectedBytes = candidates.filter((i) => selected.has(i.id)).reduce((a, i) => a + i.size_bytes, 0);
  const cleanupResult = cleanup?.status === "succeeded" || cleanup?.status === "failed"
    ? (cleanup.result as unknown as VeeamCleanupResult | null) : null;

  if (device.veeam.product !== "vbr") return null;
  const agent = device.agent;
  const disabledReason =
    !agent || agent.status !== "approved" ? "L'agente non è approvato."
      : !writable ? "Non hai i permessi di scrittura su questo tenant." : null;

  async function analyze(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const c = await api<Command>(`/devices/${device.id}/veeam/cleanup-scan`, { method: "POST", body: { older_than_days: days } });
      setSelected(new Set());
      setCleanupId(null);
      setScanId(c.id);
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!scanId || selected.size === 0) return;
    const n = selected.size;
    if (!window.confirm(
      `Eliminare dal disco ${n} backup (${bytes(selectedBytes)})?\n\n` +
      "L'operazione è eseguita da Veeam e non è reversibile. Se il repository è su un NAS con " +
      "cestino di rete attivo, lo spazio si libera solo svuotando il cestino.",
    )) return;
    setError(null);
    const token = await require();
    if (!token) return;
    setBusy(true);
    try {
      const c = await api<Command>(`/devices/${device.id}/veeam/cleanup`, {
        method: "POST",
        headers: { "X-Elevation": token },
        body: { scan_id: scanId, backup_ids: [...selected] },
      });
      setCleanupId(c.id);
    } catch (err) {
      if (err instanceof ApiError && err.message === "elevation_required") {
        end();
        setError(new Error("Sessione elevata scaduta: premi di nuovo Elimina."));
      } else {
        setError(err as Error);
      }
    } finally {
      setBusy(false);
    }
  }

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  const scanning = scan && !FINAL.includes(scan.status);
  const cleaning = cleanup && !FINAL.includes(cleanup.status);

  return (
    <section className="card">
      <h2>Pulizia guidata dei repository</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Propone l'eliminazione dei backup di job <strong>eliminati o disabilitati</strong> il cui restore point più
        recente è più vecchio della soglia. L'eliminazione avviene tramite Veeam, che aggiorna il proprio
        database e non lascia catene rotte. Per i job attivi l'analisi mostra i restore point oltre la retention,
        a titolo informativo.
      </p>
      {disabledReason ? (
        <div className="notice">{disabledReason}</div>
      ) : (
        <form className="form-row" onSubmit={analyze}>
          <label className="field" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            Più vecchi di
            <input type="number" min={7} max={3650} value={days} style={{ width: 90 }}
              onChange={(e) => setDays(Number(e.target.value))} aria-label="Giorni" />
            giorni
          </label>
          <button className="btn primary" type="submit" disabled={busy || !!scanning || !!cleaning}>
            {scanning ? "Analisi in corso…" : "Analizza"}
          </button>
        </form>
      )}
      <ErrorBox error={error} />

      {scan && !result && (
        <div className="cmd-status">
          <CommandStatusBadge status={scan.status} />
          {scan.error && <span className="badge critical">{scan.error}</span>}
          {scan.status === "failed" && scan.output && <span className="muted">{scan.output.slice(-300)}</span>}
        </div>
      )}

      {result && (
        <div className="stack" style={{ marginTop: 14 }}>
          <div className="muted small">
            Analisi del {dateTime(scan!.finished_at)} · {items.length} backup · {candidates.length} eliminabili
            ({bytes(candidates.reduce((a, i) => a + i.size_bytes, 0))})
          </div>
          {(result.errors ?? []).length > 0 && (
            <div className="notice">Avvisi dell'analisi: {result.errors.join(" · ")}</div>
          )}

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 1 }} />
                  <th>Backup</th><th>Stato</th><th>Repository</th>
                  <th className="num">Punti</th><th>Ultimo punto</th><th className="num">Dimensione</th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => (
                  <tr key={i.id} className={i.candidate ? "clickable" : ""} onClick={() => i.candidate && toggle(i.id)}>
                    <td>
                      {i.candidate && (
                        <input type="checkbox" checked={selected.has(i.id)} onChange={() => toggle(i.id)}
                          onClick={(e) => e.stopPropagation()} aria-label={`Seleziona ${i.name}`} />
                      )}
                    </td>
                    <td>
                      {i.name}
                      {i.job && i.job !== i.name && <div className="muted small">job {i.job}</div>}
                    </td>
                    <td className="nowrap">
                      <span className={`badge ${i.candidate ? "warning" : ""}`}>{STATE_LABEL[i.state] ?? i.state}</span>
                    </td>
                    <td className="muted">{i.repository ?? "—"}</td>
                    <td className="num">{i.restore_points}</td>
                    <td className="nowrap">{dateTime(i.last_point)}</td>
                    <td className="num nowrap">{bytes(i.size_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {items.length === 0 && <div className="empty">Nessun backup trovato</div>}
          </div>

          {candidates.length > 0 && !disabledReason && (
            <div className="form-row">
              <button className="btn small" type="button" onClick={() => setSelected(new Set(candidates.map((c) => c.id)))}>
                Seleziona tutti
              </button>
              <button className="btn small" type="button" onClick={() => setSelected(new Set())}>Nessuno</button>
              <span style={{ flex: 1 }} />
              {!agent?.allow_shell ? (
                <span className="muted">Operazioni remote disattivate sul dispositivo.</span>
              ) : (
                <button className="btn primary" type="button" disabled={selected.size === 0 || busy || !!cleaning}
                  onClick={() => void remove()}>
                  Elimina {selected.size > 0 ? `${selected.size} backup (${bytes(selectedBytes)})` : ""}
                </button>
              )}
            </div>
          )}

          {beyond.length > 0 && (
            <div>
              <h2 style={{ marginTop: 8 }}>Job attivi oltre la retention</h2>
              <p className="muted small" style={{ marginTop: 0 }}>
                Restore point più vecchi della retention impostata sul job. Di norma li elimina Veeam al termine del
                job successivo; se restano, di solito c'è un problema (job che fallisce, catena bloccata, GFS). Le
                catene complete oltre la retention non sono ancora eliminabili da qui.
              </p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Backup</th><th>Retention</th><th className="num">Punti oltre</th><th className="num">Spazio</th><th>Catene complete</th></tr>
                  </thead>
                  <tbody>
                    {beyond.map((i) => {
                      const b = i.beyond_retention!;
                      const chains = b.chains ?? [];
                      return (
                        <tr key={i.id}>
                          <td>{i.name}</td>
                          <td className="nowrap">
                            {b.retention ? `${b.retention.value} ${b.retention.type === "days" ? "giorni" : "punti"}` : "—"}
                          </td>
                          <td className="num">{b.points}</td>
                          <td className="num nowrap">{bytes(b.size_bytes ?? 0)}</td>
                          <td className="small">
                            {chains.length > 0
                              ? chains.map((c) => `${dateTime(c.first_point)} → ${dateTime(c.last_point)} (${bytes(c.size_bytes)})`).join("; ")
                              : <span className="muted">{b.note ?? "nessuna"}</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {cleanup && (
        <div className="stack" style={{ marginTop: 14 }}>
          <div className="cmd-status">
            <strong>Eliminazione</strong>
            <CommandStatusBadge status={cleanup.status} />
            {cleanup.reason && <span>motivo: {cleanup.reason}</span>}
            {cleanup.error && <span className="badge critical">{cleanup.error}</span>}
          </div>
          {cleanupResult && (
            <div className="table-wrap">
              <table>
                <tbody>
                  {cleanupResult.results.map((r) => (
                    <tr key={r.id}>
                      <td>{r.name ?? r.id}</td>
                      <td className="nowrap"><span className={`badge ${RESULT_LABEL[r.status][1]}`}>{RESULT_LABEL[r.status][0]}</span></td>
                      <td className="muted">{r.status === "deleted" ? bytes(r.size_bytes ?? 0) : r.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {cleanupResult && (cleanupResult.repositories ?? []).length > 0 && (
            <div className="muted small">
              Spazio libero ora:{" "}
              {cleanupResult.repositories.map((r) => `${r.name} ${bytes(r.free_bytes ?? undefined)}`).join(" · ")}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
