import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api";
import { ConnectivityBadge, Empty, ErrorBox } from "../components";
import { useConsole } from "../context";
import { ago, connectivity, dateTime, KIND_LABEL } from "../format";
import type { Device } from "../types";

export default function Devices() {
  const { tenants, tenantName } = useConsole();
  const [params, setParams] = useSearchParams();
  const tenant = params.get("tenant") ?? "";
  const kind = params.get("kind") ?? "";
  const search = params.get("q") ?? "";

  const q = [tenant && `tenant_id=${tenant}`, kind && `kind=${kind}`].filter(Boolean).join("&");
  const { data, error } = useApi<Device[]>(`/devices${q ? `?${q}` : ""}`, 60000);

  const rows = useMemo(() => {
    const s = search.toLowerCase();
    return (data ?? []).filter(
      (d) => !s || [d.name, d.hostname, d.model, d.serial, d.supremo_id, ...(d.ip_addresses ?? []).flatMap((n) => n.ipv4)].some((v) => v?.toLowerCase().includes(s)),
    );
  }, [data, search]);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dispositivi</h1>
          <div className="sub">{data ? `${rows.length} di ${data.length}` : "…"}</div>
        </div>
      </div>
      <div className="filters">
        <input
          type="search" placeholder="Cerca nome, IP, seriale, ID Supremo" value={search}
          onChange={(e) => setFilter("q", e.target.value)} aria-label="Cerca"
        />
        <select value={tenant} onChange={(e) => setFilter("tenant", e.target.value)} aria-label="Tenant">
          <option value="">Tutti i tenant</option>
          {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <select value={kind} onChange={(e) => setFilter("kind", e.target.value)} aria-label="Tipo">
          <option value="">Tutti i tipi</option>
          <option value="nas">NAS</option>
          <option value="server">Server</option>
          <option value="pc">PC</option>
        </select>
      </div>
      <ErrorBox error={error} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Stato</th>
              <th>Nome</th>
              <th>Tipo</th>
              <th>Tenant</th>
              <th>Sito</th>
              <th>IP</th>
              <th>ID Supremo</th>
              <th>Modello</th>
              <th>Sistema</th>
              <th>Agente</th>
              <th>Ultimo contatto</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id}>
                <td><ConnectivityBadge value={connectivity(d)} /></td>
                <td className="nowrap"><Link to={`/dispositivi/${d.id}`}>{d.name}</Link></td>
                <td>{KIND_LABEL[d.kind]}</td>
                <td className="nowrap">{tenantName(d.tenant_id)}</td>
                <td className="nowrap">{d.site_name ?? <span className="muted">—</span>}</td>
                <td className="mono nowrap">{ipSummary(d) ?? <span className="muted">—</span>}</td>
                <td className="mono nowrap">{d.supremo_id ?? <span className="muted">—</span>}</td>
                <td>{d.model ?? <span className="muted">—</span>}</td>
                <td>{d.os_version ?? d.platform}</td>
                <td className="mono">{d.agent?.version ?? "—"}</td>
                <td className="nowrap muted" title={dateTime(d.last_seen_at)}>{ago(d.last_seen_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && rows.length === 0 && <Empty>Nessun dispositivo</Empty>}
      </div>
    </>
  );
}

// Primo indirizzo IPv4 (+N se ce ne sono altri: l'elenco completo è nella scheda).
function ipSummary(d: Device): string | null {
  const ips = (d.ip_addresses ?? []).flatMap((n) => n.ipv4);
  if (ips.length === 0) return null;
  return ips.length > 1 ? `${ips[0]} +${ips.length - 1}` : ips[0];
}
