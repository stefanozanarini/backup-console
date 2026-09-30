import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api";
import { Empty, ErrorBox, SeverityBadge, StateBadge } from "../components";
import { useConsole } from "../context";
import { ago, dateTime } from "../format";
import type { Problem, TenantSummary } from "../types";
import { JobStartButton } from "../veeam";

const STATE_FILTERS = {
  open: "state=open",
  resolved: "state=resolved",
} as const;
type StateFilter = keyof typeof STATE_FILTERS;

export default function Problems() {
  const { tenants, canWrite } = useConsole();
  const [params, setParams] = useSearchParams();
  const tenant = params.get("tenant") ?? "";
  const severity = params.get("severity") ?? "";
  const state: StateFilter = params.get("state") === "resolved" ? "resolved" : "open";
  const [open, setOpen] = useState<string | null>(null);

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
        <select value={state} onChange={(e) => setFilter("state", e.target.value === "open" ? "" : e.target.value)} aria-label="Stato">
          <option value="open">Aperti</option>
          <option value="resolved">Risolti</option>
        </select>
      </div>

      <ErrorBox error={problems.error} />
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
              <th>{state === "resolved" ? "Risolto" : "Stato"}</th>
            </tr>
          </thead>
          <tbody>
            {(problems.data ?? []).map((p) => (
              <Fragment key={p.id}>
              <tr
                className={`clickable ${open === p.id ? "expanded" : ""}`}
                onClick={(e) => {
                  // I link della riga mantengono il loro comportamento.
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
                <td className="nowrap">
                  {p.state === "resolved" && p.resolved_at
                    ? <span title={dateTime(p.resolved_at)}>{ago(p.resolved_at)}</span>
                    : <StateBadge state={p.state} />}
                </td>
              </tr>
              {open === p.id && (
                <tr className="detail-row">
                  <td colSpan={7}><ProblemDetails problem={p} writable={canWrite(p.tenant_id)} /></td>
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
  agent_version: "Versione agente", last_seen_at: "Ultimo contatto", until: "Fine della finestra",
};
const HIDDEN = new Set(["messages", "sessions", "job_key"]);

const JOB_CHECKS = new Set(["veeam.job_result", "veeam.job_retry", "veeam.last_success_age_h"]);

function ProblemDetails({ problem, writable }: { problem: Problem; writable: boolean }) {
  const d = problem.details ?? {};
  const messages = Array.isArray(d.messages) ? (d.messages as Message[]) : [];
  const sessions = Array.isArray(d.sessions) ? (d.sessions as SessionInfo[]) : [];
  const fields = Object.entries(d).filter(([k, v]) => !HIDDEN.has(k) && v !== null && v !== "" && typeof v !== "object");

  const jobKey = typeof d.job_key === "string" ? d.job_key : null;
  return (
    <div className="problem-details">
      {writable && jobKey && !jobKey.startsWith("uranium:") && JOB_CHECKS.has(problem.check) && problem.state === "open" && (
        <JobStartButton deviceId={problem.device_id} jobKey={jobKey} jobName={String(d.job ?? "")}
          product={jobKey.startsWith("vaw:") ? "vaw" : "vbr"} lastResult={typeof d.result === "string" ? d.result : null} />
      )}
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
