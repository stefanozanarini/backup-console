import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api";
import { Empty, ErrorBox, UsageBar } from "../components";
import { useConsole } from "../context";
import { ago, dateTime } from "../format";
import { exportTable, today } from "../exportTable";

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

/** Istanze e/o socket usati su licenziati, con la barra di utilizzo. */
function Usage({ r }: { r: LicenseRow }) {
  const rows: [string, number | null, number | null][] = [];
  if (r.instances_total || r.instances_used) rows.push(["istanze", r.instances_used, r.instances_total]);
  if (r.sockets_total || r.sockets_used) rows.push(["socket", r.sockets_used, r.sockets_total]);
  if (rows.length === 0) return <span className="muted" title="Dato non ancora riportato (serve l'agente 0.2.10 o successivo)">—</span>;
  return (
    <div className="stack" style={{ gap: 4, minWidth: 190 }}>
      {rows.map(([label, used, total]) => (
        <div key={label}>
          <div className="small">
            <strong>{used ?? "?"}</strong> / {total ?? "?"} {label}
            {used != null && total != null && total > 0 && used <= total && (
              <span className="muted"> · {total - used} disponibili</span>
            )}
            {used != null && total != null && used > total && <span className="badge critical" style={{ marginLeft: 4 }}>oltre la licenza</span>}
          </div>
          {used != null && total ? <UsageBar pct={(used * 100) / total} warn={90} crit={100} /> : null}
        </div>
      ))}
    </div>
  );
}

export default function Licenses() {
  const { tenants } = useConsole();
  const [params, setParams] = useSearchParams();
  const tenant = params.get("tenant") ?? "";
  const { data, error } = useApi<LicenseRow[]>(`/veeam/licenses${tenant ? `?tenant_id=${tenant}` : ""}`, 300000);

  // Le licenze in abbonamento includono il supporto: la colonna solo se serve.
  const showSupport = (data ?? []).some((r) => r.support_expiration && r.support_expiration !== r.expiration);
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
        <button className="btn" type="button" disabled={!data?.length} onClick={() => exportLicenses(data ?? [])}>
          Esporta in Excel
        </button>
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
              <th>Stato</th><th>Server</th><th>Tenant / sede</th><th>Licenza</th>
              <th>Scadenza</th><th>Utilizzo</th>{showSupport && <th>Supporto</th>}<th>Versione</th><th>Dati</th>
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
                  <td className="nowrap">{r.tenant_name}<div className="muted small">{r.site_name}</div></td>
                  <td className="nowrap">
                    {r.edition || "—"}
                    <div className="muted small">{[r.type, r.status && r.status !== "Valid" ? r.status : null].filter(Boolean).join(" · ") || "—"}</div>
                  </td>
                  <td className="nowrap"><Remaining iso={r.expiration} /></td>
                  <td><Usage r={r} /></td>
                  {showSupport && <td className="nowrap"><Remaining iso={r.support_expiration} /></td>}
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

function exportLicenses(rows: LicenseRow[]) {
  const d = (iso: string | null) => (iso ? new Date(iso) : null);
  exportTable(`licenze-veeam-${today()}`, [
    "Stato", "Server", "Intestatario", "Tenant", "Sede", "Edizione", "Tipo", "Stato licenza",
    "Scadenza licenza", "Giorni alla scadenza", "Scadenza supporto", "Istanze usate", "Istanze licenziate",
    "Socket usati", "Socket licenziati", "Versione Veeam", "Ultimo report",
  ], rows.map((r) => [
    LEVEL_LABEL[level(daysLeft(r.expiration))][0], r.device_name, r.licensed_to, r.tenant_name, r.site_name,
    r.edition, r.type, r.status, d(r.expiration), daysLeft(r.expiration), d(r.support_expiration),
    r.instances_used, r.instances_total, r.sockets_used, r.sockets_total, r.veeam_version, d(r.reported_at),
  ]));
}
