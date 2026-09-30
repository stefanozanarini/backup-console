import { Fragment, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api";
import { Empty, ErrorBox } from "../components";
import { useConsole } from "../context";
import { ago, bytes, dateTime } from "../format";
import type { VeeamJob } from "../types";
import { HistoryStrip, HistoryTable, VeeamResultBadge, VeeamRunningBadge } from "../veeam";

const PRODUCT_CHIP: Record<string, string> = { vbr: "B&R", vaw: "Agent", uranium: "Uranium", hyperbackup: "Hyper Backup" };

type Bucket = "success" | "warning" | "failed" | "none";

function bucket(j: VeeamJob): Bucket {
  const r = (j.last_result ?? "").toLowerCase();
  return r === "success" || r === "warning" || r === "failed" ? r : "none";
}

export default function Backup() {
  const { tenants, canWrite } = useConsole();
  const [params, setParams] = useSearchParams();
  const tenant = params.get("tenant") ?? "";
  const result = (params.get("result") ?? "") as Bucket | "";
  const [open, setOpen] = useState<string | null>(null);
  const { data, error } = useApi<VeeamJob[]>(`/veeam/jobs${tenant ? `?tenant_id=${tenant}` : ""}`, 60000);

  const counts = useMemo(() => {
    const c: Record<Bucket, number> = { success: 0, warning: 0, failed: 0, none: 0 };
    for (const j of data ?? []) if (j.enabled) c[bucket(j)]++;
    return c;
  }, [data]);
  // Prima quello che richiede attenzione.
  const order: Record<Bucket, number> = { failed: 0, warning: 1, none: 2, success: 3 };
  const rows = (data ?? [])
    .filter((j) => !result || (j.enabled && bucket(j) === result))
    .sort((a, b) => Number(!a.enabled) - Number(!b.enabled) || order[bucket(a)] - order[bucket(b)]);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  const tiles: { key: Bucket; label: string; dot: string }[] = [
    { key: "failed", label: "falliti", dot: "offline" },
    { key: "warning", label: "con avvisi", dot: "pending" },
    { key: "success", label: "riusciti", dot: "online" },
    { key: "none", label: "senza esito", dot: "" },
  ];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Backup</h1>
          <div className="sub">Ultimo esito di ogni job: Veeam Backup &amp; Replication, Veeam Agent, Uranium Backup e Hyper Backup</div>
        </div>
      </div>
      <div className="tiles">
        {tiles.map((t) => (
          <button key={t.key} className={`tile ${result === t.key ? "selected" : ""}`}
            onClick={() => setFilter("result", result === t.key ? "" : t.key)}>
            <div className="num">{counts[t.key]}</div>
            <div className="num-label"><span className={`dot ${t.dot}`} aria-hidden="true" />job {t.label}</div>
          </button>
        ))}
      </div>
      <div className="filters">
        <select value={tenant} onChange={(e) => setFilter("tenant", e.target.value)} aria-label="Tenant">
          <option value="">Tutti i tenant</option>
          {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </div>
      <ErrorBox error={error} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Esito</th><th>Job</th><th>Dispositivo</th><th>Tenant</th><th>Sede</th><th>Ultimi 10 giorni</th><th>Ultimo avvio</th><th>Ultimo successo</th><th className="num">Trasferiti</th></tr>
          </thead>
          <tbody>
            {rows.map((j) => (
              <Fragment key={j.id}>
              <tr
                className={`clickable ${open === j.id ? "expanded" : ""}`}
                aria-expanded={open === j.id}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest("a, button")) return;
                  setOpen(open === j.id ? null : j.id);
                }}
              >
                <td className="nowrap"><span className="chevron" aria-hidden="true">{open === j.id ? "▾" : "▸"}</span> <VeeamResultBadge result={j.last_result} enabled={j.enabled} running={j.details?.running} /><VeeamRunningBadge job={j} /></td>
                <td>{j.name} <span className="chip">{PRODUCT_CHIP[j.product] ?? j.product}</span></td>
                <td className="nowrap"><Link to={`/dispositivi/${j.device_id}`}>{j.device_name}</Link></td>
                <td className="nowrap">{j.tenant_name}</td>
                <td className="nowrap">{j.site_name}</td>
                <td><HistoryStrip job={j} /></td>
                <td className="nowrap" title={dateTime(j.last_start ?? j.last_end)}>{ago(j.last_start ?? j.last_end)}</td>
                <td className="nowrap muted" title={dateTime(j.last_success)}>{ago(j.last_success)}</td>
                <td className="num">{j.transferred_bytes != null ? bytes(j.transferred_bytes) : "—"}</td>
              </tr>
              {open === j.id && (
                <tr className="detail-row">
                  <td colSpan={9}><HistoryTable job={j} writable={canWrite(j.tenant_id)} /></td>
                </tr>
              )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {data && rows.length === 0 && <Empty>Nessun job</Empty>}
      </div>
    </>
  );
}
