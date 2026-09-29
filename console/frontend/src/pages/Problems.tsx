import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, notifyChange, useApi } from "../api";
import { Empty, ErrorBox, SeverityBadge, StateBadge } from "../components";
import { useConsole } from "../context";
import { ago, dateTime } from "../format";
import type { Problem, TenantSummary } from "../types";

const STATE_FILTERS = {
  active: "state=open&state=acknowledged",
  open: "state=open",
  acknowledged: "state=acknowledged",
  resolved: "state=resolved",
} as const;
type StateFilter = keyof typeof STATE_FILTERS;

export default function Problems() {
  const { tenants, canWrite } = useConsole();
  const [params, setParams] = useSearchParams();
  const tenant = params.get("tenant") ?? "";
  const severity = params.get("severity") ?? "";
  const state = (params.get("state") as StateFilter) || "active";
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [actionError, setActionError] = useState<Error | null>(null);

  const q = [STATE_FILTERS[state], tenant && `tenant_id=${tenant}`, severity && `severity=${severity}`]
    .filter(Boolean)
    .join("&");
  const problems = useApi<Problem[]>(`/problems?${q}`, 30000);
  const summary = useApi<TenantSummary[]>("/problems/summary", 30000);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  async function ack(p: Problem) {
    setBusy(p.id);
    setActionError(null);
    try {
      await api(`/problems/${p.id}/ack`, { method: "POST" });
      await Promise.all([problems.reload(), summary.reload()]);
      notifyChange();
    } catch (e) {
      setActionError(e as Error);
    } finally {
      setBusy(null);
    }
  }

  const totals = (summary.data ?? []).reduce(
    (a, s) => ({ critical: a.critical + s.critical, warning: a.warning + s.warning }),
    { critical: 0, warning: 0 },
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Problemi</h1>
          <div className="sub">Tutti i tenant · aggiornamento automatico ogni 30 secondi</div>
        </div>
      </div>

      <div className="tiles">
        <SummaryTile
          name="Tutti i tenant" critical={totals.critical} warning={totals.warning}
          selected={!tenant} onClick={() => setFilter("tenant", "")}
        />
        {(summary.data ?? []).map((s) => (
          <SummaryTile
            key={s.tenant_id} name={s.tenant_name} critical={s.critical} warning={s.warning}
            selected={tenant === s.tenant_id} onClick={() => setFilter("tenant", s.tenant_id)}
          />
        ))}
      </div>

      <div className="filters">
        <select value={tenant} onChange={(e) => setFilter("tenant", e.target.value)} aria-label="Tenant">
          <option value="">Tutti i tenant</option>
          {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select value={severity} onChange={(e) => setFilter("severity", e.target.value)} aria-label="Gravità">
          <option value="">Tutte le gravità</option>
          <option value="critical">Critico</option>
          <option value="warning">Attenzione</option>
        </select>
        <select value={state} onChange={(e) => setFilter("state", e.target.value === "active" ? "" : e.target.value)} aria-label="Stato">
          <option value="active">Aperti e presi in carico</option>
          <option value="open">Solo aperti</option>
          <option value="acknowledged">Solo presi in carico</option>
          <option value="resolved">Risolti</option>
        </select>
      </div>

      <ErrorBox error={problems.error ?? actionError} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Gravità</th>
              <th>Tenant</th>
              <th>Dispositivo</th>
              <th>Problema</th>
              <th>Dal</th>
              <th>Ultimo rilevamento</th>
              <th>Stato</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(problems.data ?? []).map((p) => (
              <Fragment key={p.id}>
              <tr
                className={`clickable ${open === p.id ? "expanded" : ""}`}
                onClick={(e) => {
                  // I link e i pulsanti della riga mantengono il loro comportamento.
                  if ((e.target as HTMLElement).closest("a, button")) return;
                  setOpen(open === p.id ? null : p.id);
                }}
                aria-expanded={open === p.id}
              >
                <td><SeverityBadge severity={p.severity} /></td>
                <td className="nowrap">{p.tenant_name}</td>
                <td className="nowrap"><Link to={`/dispositivi/${p.device_id}`}>{p.device_name}</Link></td>
                <td>
                  <span className="chevron" aria-hidden="true">{open === p.id ? "▾" : "▸"}</span> {p.title}
                </td>
                <td className="nowrap" title={dateTime(p.first_seen)}>{ago(p.first_seen)}</td>
                <td className="nowrap muted" title={dateTime(p.last_seen)}>{ago(p.last_seen)}</td>
                <td><StateBadge state={p.state} /></td>
                <td className="num">
                  {p.state === "open" && canWrite(p.tenant_id) && (
                    <button className="btn small" disabled={busy === p.id} onClick={() => void ack(p)}>
                      Prendi in carico
                    </button>
                  )}
                </td>
              </tr>
              {open === p.id && (
                <tr className="detail-row">
                  <td colSpan={8}><ProblemDetails problem={p} /></td>
                </tr>
              )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {problems.data?.length === 0 && (
          <Empty>{state === "resolved" ? "Nessun problema risolto" : "Nessun problema aperto"}</Empty>
        )}
      </div>
    </>
  );
}

interface Message { object?: string | null; status?: string | null; text: string }
interface SessionInfo { start?: string | null; end?: string | null; result?: string | null; retry?: boolean; reason?: string | null }

const DETAIL_LABELS: Record<string, string> = {
  job: "Job", result: "Esito", last_end: "Fine ultima sessione", last_success: "Ultimo backup riuscito",
  expiration: "Scadenza", value: "Valore", warn: "Soglia attenzione", crit: "Soglia critica",
  retries: "Retry", repo: "Repository", mount: "Volume", disk: "Disco",
  error: "Errore", collector: "Raccolta", at: "Quando", reported_at: "Ultimo report ricevuto",
  agent_version: "Versione agente", last_seen_at: "Ultimo contatto",
};
const HIDDEN = new Set(["messages", "sessions"]);

function ProblemDetails({ problem }: { problem: Problem }) {
  const d = problem.details ?? {};
  const messages = Array.isArray(d.messages) ? (d.messages as Message[]) : [];
  const sessions = Array.isArray(d.sessions) ? (d.sessions as SessionInfo[]) : [];
  const fields = Object.entries(d).filter(([k, v]) => !HIDDEN.has(k) && v !== null && v !== "" && typeof v !== "object");

  return (
    <div className="problem-details">
      {messages.length > 0 && (
        <ul className="messages">
          {messages.map((m, i) => (
            <li key={i} className={m.status ?? ""}>
              <span className="msg-icon" aria-label={m.status ?? ""}>
                {m.status === "error" ? "✖" : m.status === "warning" ? "⚠" : "•"}
              </span>
              <span>
                {m.object && <strong>{m.object}: </strong>}
                {m.text}
              </span>
            </li>
          ))}
        </ul>
      )}
      {sessions.length > 0 && (
        <table className="compact">
          <thead>
            <tr><th>Avvio</th><th>Fine</th><th>Esito</th><th>Tipo</th><th>Motivo</th></tr>
          </thead>
          <tbody>
            {sessions.map((s, i) => (
              <tr key={i}>
                <td className="nowrap">{s.start ? dateTime(s.start) : "—"}</td>
                <td className="nowrap">{s.end ? dateTime(s.end) : "—"}</td>
                <td>{s.result || "—"}</td>
                <td>{s.retry ? "Retry" : "Esecuzione"}</td>
                <td>{s.reason || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {fields.length > 0 && (
        <dl className="kv">
          {fields.map(([k, v]) => (
            <Fragment key={k}>
              <dt>{DETAIL_LABELS[k] ?? k}</dt>
              <dd>{typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v) ? dateTime(v) : String(v)}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      {messages.length === 0 && sessions.length === 0 && problem.check === "veeam.job_result" && (
        <div className="muted">
          Nessun dettaglio disponibile: l'agente lo riporterà dalla prossima raccolta (serve la versione aggiornata).
        </div>
      )}
      <div className="muted small">
        Controllo <code>{problem.check}</code> · primo rilevamento {dateTime(problem.first_seen)}
      </div>
    </div>
  );
}

function SummaryTile(props: { name: string; critical: number; warning: number; selected: boolean; onClick: () => void }) {
  const ok = props.critical + props.warning === 0;
  return (
    <button className={`tile ${props.selected ? "selected" : ""} ${ok ? "ok" : ""}`} onClick={props.onClick}>
      <div className="name">{props.name}</div>
      <div className="counts">
        <div>
          <div className="num">{props.critical}</div>
          <div className="num-label"><span className="dot offline" aria-hidden="true" />critici</div>
        </div>
        <div>
          <div className="num">{props.warning}</div>
          <div className="num-label"><span className="dot pending" aria-hidden="true" />attenzione</div>
        </div>
      </div>
    </button>
  );
}
