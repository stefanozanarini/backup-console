import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api";
import { Empty, ErrorBox } from "../components";
import { useConsole } from "../context";
import { ago, dateTime } from "../format";

interface LicenseRow {
  device_id: string;
  device_name: string;
  tenant_id: string;
  tenant_name: string;
  site_name: string;
  veeam_version: string | null;
  reported_at: string | null;
  online: boolean;
  edition: string | null;
  status: string | null;
  type: string | null;
  licensed_to: string | null;
  expiration: string | null;
  support_expiration: string | null;
  instances_total: number | null;
  instances_used: number | null;
  sockets_total: number | null;
  sockets_used: number | null;
}

type Level = "expired" | "critical" | "warning" | "ok" | "none";

const DAY = 86_400_000;

function daysLeft(iso: string | null): number | null {
  return iso ? Math.floor((new Date(iso).getTime() - Date.now()) / DAY) : null;
}

// Stesse soglie del problema veeam.license (30 giorni) più un preavviso a 90.
function level(days: number | null): Level {
  if (days === null) return "none";
  if (days < 0) return "expired";
  if (days < 30) return "critical";
  if (days < 90) return "warning";
  return "ok";
}

const LEVEL_LABEL: Record<Level, [string, string]> = {
  expired: ["Scaduta", "critical"],
  critical: ["Meno di 30 giorni", "critical"],
  warning: ["Meno di 90 giorni", "warning"],
  ok: ["Valida", "good"],
  none: ["Nessuna scadenza", ""],
};

function Remaining({ iso }: { iso: string | null }) {
  const d = daysLeft(iso);
  if (d === null) return <span className="muted">—</span>;
  return (
    <span title={dateTime(iso)}>
      {new Date(iso!).toLocaleDateString("it-IT")}
      <span className="muted small"> · {d < 0 ? `scaduta da ${-d} giorni` : d === 0 ? "oggi" : `tra ${d} giorni`}</span>
    </span>
  );
}

function usage(used: number | null, total: number | null): string {
  if (used == null && total == null) return "—";
  return `${used ?? "?"} / ${total ?? "?"}`;
}

export default function Licenses() {
  const { tenants } = useConsole();
  const [params, setParams] = useSearchParams();
  const tenant = params.get("tenant") ?? "";
  const { data, error } = useApi<LicenseRow[]>(`/veeam/licenses${tenant ? `?tenant_id=${tenant}` : ""}`, 300000);

  const counts = useMemo(() => {
    const c: Record<Level, number> = { expired: 0, critical: 0, warning: 0, ok: 0, none: 0 };
    for (const r of data ?? []) c[level(daysLeft(r.expiration))]++;
    return c;
  }, [data]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Licenze Veeam</h1>
          <div className="sub">Server Veeam Backup &amp; Replication, dalla scadenza più vicina</div>
        </div>
      </div>
      <div className="tiles">
        <div className="tile" style={{ cursor: "default" }}><div className="num">{counts.expired + counts.critical}</div>
          <div className="num-label"><span className="dot offline" aria-hidden="true" />scadute o entro 30 giorni</div></div>
        <div className="tile" style={{ cursor: "default" }}><div className="num">{counts.warning}</div>
          <div className="num-label"><span className="dot pending" aria-hidden="true" />entro 90 giorni</div></div>
        <div className="tile" style={{ cursor: "default" }}><div className="num">{counts.ok + counts.none}</div>
          <div className="num-label"><span className="dot online" aria-hidden="true" />valide</div></div>
      </div>
      <div className="filters">
        <select value={tenant} aria-label="Tenant"
          onChange={(e) => setParams(e.target.value ? { tenant: e.target.value } : {}, { replace: true })}>
          <option value="">Tutti i tenant</option>
          {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </div>
      <ErrorBox error={error} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Stato</th><th>Server</th><th>Tenant</th><th>Sede</th><th>Edizione</th><th>Tipo</th>
              <th>Scadenza licenza</th><th>Scadenza supporto</th><th className="num">Istanze</th>
              <th className="num">Socket</th><th>Versione</th><th>Dati</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((r) => {
              const [label, cls] = LEVEL_LABEL[level(daysLeft(r.expiration))];
              return (
                <tr key={r.device_id}>
                  <td className="nowrap"><span className={`badge ${cls}`}>{label}</span></td>
                  <td className="nowrap">
                    <Link to={`/dispositivi/${r.device_id}`}>{r.device_name}</Link>
                    {r.licensed_to && <div className="muted small">{r.licensed_to}</div>}
                  </td>
                  <td className="nowrap">{r.tenant_name}</td>
                  <td className="nowrap">{r.site_name}</td>
                  <td>{r.edition || "—"}{r.status && r.status !== "Valid" && <div className="muted small">{r.status}</div>}</td>
                  <td>{r.type || "—"}</td>
                  <td className="nowrap"><Remaining iso={r.expiration} /></td>
                  <td className="nowrap"><Remaining iso={r.support_expiration} /></td>
                  <td className="num nowrap">{usage(r.instances_used, r.instances_total)}</td>
                  <td className="num nowrap">{usage(r.sockets_used, r.sockets_total)}</td>
                  <td className="mono nowrap">{r.veeam_version ?? "—"}</td>
                  <td className="nowrap muted" title={dateTime(r.reported_at)}>
                    {ago(r.reported_at)}{!r.online && " · offline"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {data && data.length === 0 && <Empty>Nessun server Veeam B&amp;R</Empty>}
      </div>
    </>
  );
}
