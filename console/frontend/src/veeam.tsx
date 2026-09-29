import { useApi } from "./api";
import { UsageBar } from "./components";
import { ago, bytes, dateTime } from "./format";
import type { DeviceDetail, VeeamJob, VeeamResult } from "./types";

export function VeeamResultBadge({ result, enabled = true, running = false }: { result: VeeamResult | null; enabled?: boolean; running?: boolean }) {
  if (!enabled) return <span className="badge">Disabilitato</span>;
  switch ((result ?? "None").toLowerCase()) {
    case "success":
      return <span className="badge good">Riuscito</span>;
    case "warning":
      return <span className="badge warning">Avvisi</span>;
    case "failed":
      return <span className="badge critical">Fallito</span>;
    default:
      // Primo avvio ancora in corso: basta il badge "In corso".
      return running ? null : <span className="badge">Mai eseguito</span>;
  }
}

/** Sessione in corso: l'esito mostrato accanto resta quello dell'ultima sessione terminata. */
export function VeeamRunningBadge({ job }: { job: VeeamJob }) {
  if (!job.enabled || !job.details?.running) return null;
  const pct = job.details.progress_pct;
  return (
    <span className="badge" style={{ marginLeft: 4 }} title={job.details.running_since ? `avviato ${dateTime(job.details.running_since)}` : undefined}>
      In corso{pct != null ? ` ${pct}%` : ""}
    </span>
  );
}

export function VeeamSection({ device }: { device: DeviceDetail }) {
  const v = device.veeam;
  const jobs = useApi<VeeamJob[]>(v.product ? `/veeam/jobs?device_id=${device.id}` : null, 60000);
  if (!v.product) return null;
  const repos = (v.repositories ?? []).filter((r) => r.total_bytes);

  return (
    <section className="card">
      <div className="page-head" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>
          {v.product === "vbr" ? "Veeam Backup & Replication" : "Veeam Agent for Windows"}
          {v.version && <span className="chip mono" style={{ marginLeft: 8 }}>{v.version}</span>}
        </h2>
        <span className="muted" style={{ fontSize: 12.5 }}>report {ago(v.reported_at)}</span>
      </div>
      {(v.errors ?? []).length > 0 && (
        <div className="notice" style={{ marginBottom: 12 }}>
          Il collector ha segnalato: {v.errors!.join(" · ")}
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Job</th><th>Tipo</th><th>Esito</th><th>Ultimo avvio</th><th>Ultimo successo</th><th className="num">Trasferiti</th></tr>
          </thead>
          <tbody>
            {(jobs.data ?? []).map((j) => (
              <tr key={j.id}>
                <td>{j.name}</td>
                <td className="muted">{j.job_type ?? "—"}</td>
                <td className="nowrap"><VeeamResultBadge result={j.last_result} enabled={j.enabled} running={j.details?.running} /><VeeamRunningBadge job={j} /></td>
                <td className="nowrap" title={dateTime(j.last_start ?? j.last_end)}>{ago(j.last_start ?? j.last_end)}</td>
                <td className="nowrap muted" title={dateTime(j.last_success)}>{ago(j.last_success)}</td>
                <td className="num">{j.transferred_bytes != null ? bytes(j.transferred_bytes) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {jobs.data?.length === 0 && <div className="empty">Nessun job</div>}
      </div>

      {(repos.length > 0 || v.license) && (
        <div className="grid-2" style={{ marginTop: 16, marginBottom: 0 }}>
          {repos.length > 0 && (
            <div>
              <h2>Repository</h2>
              <div className="table-wrap">
                <table>
                  <tbody>
                    {repos.map((r) => {
                      const pct = ((r.total_bytes! - (r.free_bytes ?? 0)) * 100) / r.total_bytes!;
                      return (
                        <tr key={r.name}>
                          <td>{r.name}</td>
                          <td className="num muted nowrap">{bytes(r.free_bytes ?? 0)} liberi</td>
                          <td><UsageBar pct={pct} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {v.license && (
            <div>
              <h2>Licenza</h2>
              <dl className="dl">
                <dt>Edizione</dt><dd>{v.license.edition || "—"}</dd>
                <dt>Stato</dt><dd>{v.license.status || "—"}</dd>
                <dt>Scadenza</dt><dd>{dateTime(v.license.expiration)}</dd>
              </dl>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
