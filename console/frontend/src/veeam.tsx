import { Fragment, useState } from "react";
import { useApi } from "./api";
import { UsageBar } from "./components";
import { ago, bytes, dateTime } from "./format";
import type { DeviceDetail, VeeamJob, VeeamResult, VeeamSession } from "./types";

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

const HISTORY_DAYS = 10;
type DayLevel = "failed" | "warning" | "success" | "none" | "empty";
const LEVEL_RANK: Record<DayLevel, number> = { empty: 0, none: 1, success: 2, warning: 3, failed: 4 };

function level(s: VeeamSession): DayLevel {
  const r = (s.result ?? "").toLowerCase();
  return r === "success" || r === "warning" || r === "failed" ? r : "none";
}

const sessionTime = (s: VeeamSession) => s.start ?? s.end;

/** Ultimi 10 giorni, uno per casella (oggi a destra): vale l'esito peggiore del giorno. */
export function HistoryStrip({ job }: { job: VeeamJob }) {
  const history = job.details?.history;
  if (!history) return <span className="muted small">—</span>;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Array.from({ length: HISTORY_DAYS }, (_, i) => {
    const d = new Date(today);
    d.setDate(d.getDate() - (HISTORY_DAYS - 1 - i));
    return { date: d, level: "empty" as DayLevel, count: 0, retries: 0 };
  });
  for (const s of history) {
    const t = sessionTime(s);
    if (!t) continue;
    const d = new Date(t);
    d.setHours(0, 0, 0, 0);
    const day = days.find((x) => x.date.getTime() === d.getTime());
    if (!day) continue;
    day.count++;
    if (s.retry) day.retries++;
    const l = level(s);
    if (LEVEL_RANK[l] > LEVEL_RANK[day.level]) day.level = l;
  }
  const label: Record<DayLevel, string> = {
    failed: "fallito", warning: "avvisi", success: "riuscito", none: "in corso / senza esito", empty: "nessuna esecuzione",
  };
  return (
    <span className="history-strip" role="img"
      aria-label={days.map((d) => `${d.date.toLocaleDateString("it-IT")}: ${label[d.level]}`).join("; ")}>
      {days.map((d) => (
        <span key={d.date.getTime()} className={`hday ${d.level}`}
          title={`${d.date.toLocaleDateString("it-IT", { weekday: "short", day: "numeric", month: "short" })}: ${label[d.level]}` +
            (d.count > 1 ? ` (${d.count} sessioni${d.retries ? `, ${d.retries} retry` : ""})` : "")} />
      ))}
    </span>
  );
}

function duration(s: VeeamSession): string {
  if (!s.start || !s.end) return "—";
  const m = Math.round((new Date(s.end).getTime() - new Date(s.start).getTime()) / 60000);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}

function HistoryTable({ job }: { job: VeeamJob }) {
  const history = job.details?.history ?? [];
  if (history.length === 0) {
    return <div className="muted small">Nessuna esecuzione negli ultimi {HISTORY_DAYS} giorni{job.details?.history ? "" : " (serve l'agente 0.2.2 o successivo)"}.</div>;
  }
  return (
    <table className="compact">
      <thead>
        <tr><th>Avvio</th><th>Fine</th><th>Durata</th><th>Esito</th><th className="num">Trasferiti</th><th>Note</th></tr>
      </thead>
      <tbody>
        {history.map((s, i) => (
          <tr key={i}>
            <td className="nowrap">{dateTime(s.start)}</td>
            <td className="nowrap">{dateTime(s.end)}</td>
            <td className="nowrap">{duration(s)}</td>
            <td className="nowrap">
              <VeeamResultBadge result={s.result} running={level(s) === "none" && !!s.state && s.state !== "Stopped"} />
              {level(s) === "none" && s.state && s.state !== "Stopped" && <span className="badge">In corso</span>}
              {s.retry && <span className="chip" style={{ marginLeft: 4 }}>retry</span>}
            </td>
            <td className="num nowrap">{s.transferred_bytes != null ? bytes(s.transferred_bytes) : "—"}</td>
            <td className="small">{s.reason ?? ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function VeeamSection({ device }: { device: DeviceDetail }) {
  const v = device.veeam;
  const jobs = useApi<VeeamJob[]>(v.product ? `/veeam/jobs?device_id=${device.id}` : null, 60000);
  const [open, setOpen] = useState<string | null>(null);
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
            <tr><th>Job</th><th>Tipo</th><th>Esito</th><th>Ultimi {HISTORY_DAYS} giorni</th><th>Ultimo avvio</th><th>Ultimo successo</th><th className="num">Trasferiti</th></tr>
          </thead>
          <tbody>
            {(jobs.data ?? []).map((j) => (
              <Fragment key={j.id}>
              <tr className={`clickable ${open === j.id ? "expanded" : ""}`} aria-expanded={open === j.id}
                onClick={() => setOpen(open === j.id ? null : j.id)}>
                <td><span className="chevron" aria-hidden="true">{open === j.id ? "▾" : "▸"}</span> {j.name}</td>
                <td className="muted">{j.job_type ?? "—"}</td>
                <td className="nowrap"><VeeamResultBadge result={j.last_result} enabled={j.enabled} running={j.details?.running} /><VeeamRunningBadge job={j} /></td>
                <td><HistoryStrip job={j} /></td>
                <td className="nowrap" title={dateTime(j.last_start ?? j.last_end)}>{ago(j.last_start ?? j.last_end)}</td>
                <td className="nowrap muted" title={dateTime(j.last_success)}>{ago(j.last_success)}</td>
                <td className="num">{j.transferred_bytes != null ? bytes(j.transferred_bytes) : "—"}</td>
              </tr>
              {open === j.id && (
                <tr className="detail-row">
                  <td colSpan={7}><HistoryTable job={j} /></td>
                </tr>
              )}
              </Fragment>
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
