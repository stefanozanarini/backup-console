import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, useApi } from "../api";
import { ConnectivityBadge, Empty, ErrorBox, SeverityBadge, StateBadge, UsageBar } from "../components";
import { useConsole } from "../context";
import { AGENT_STATUS_LABEL, ago, bytes, connectivity, dateTime, KIND_LABEL } from "../format";
import LineChart, { type Series } from "../LineChart";
import type { DeviceDetail, MetricSeries, Problem } from "../types";

const RANGES = [
  { hours: 24, label: "24 ore" },
  { hours: 24 * 7, label: "7 giorni" },
  { hours: 24 * 30, label: "30 giorni" },
];

export default function DeviceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { tenantName, canWrite } = useConsole();
  const [hours, setHours] = useState(24 * 7);
  const [actionError, setActionError] = useState<Error | null>(null);

  const device = useApi<DeviceDetail>(`/devices/${id}`, 60000);
  const problems = useApi<Problem[]>(`/problems?device_id=${id}`, 60000);
  const disk = useApi<MetricSeries[]>(`/devices/${id}/metrics?name=disk.used_pct&hours=${hours}`, 300000);

  const series: Series[] = useMemo(
    () =>
      (disk.data ?? [])
        .map((s) => ({
          label: s.labels.mount ?? "volume",
          points: s.points.map(([t, v]) => ({ t: new Date(t).getTime(), v })),
        }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [disk.data],
  );

  const d = device.data;
  if (device.error) return <ErrorBox error={device.error} />;
  if (!d) return null;

  const volumes = d.inventory?.data.system?.volumes ?? [];
  const qnap = d.inventory?.data.qnap;
  const writable = canWrite(d.tenant_id);

  async function setStatus(action: "approve" | "revoke") {
    if (!d?.agent) return;
    if (action === "revoke" && !confirm(`Revocare l'agente di ${d.name}? Servirà un nuovo enrollment.`)) return;
    setActionError(null);
    try {
      await api(`/agents/${d.agent.id}/${action}`, { method: "POST" });
      await device.reload();
    } catch (e) {
      setActionError(e as Error);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <div className="sub"><Link to="/dispositivi">Dispositivi</Link> / {tenantName(d.tenant_id)}</div>
          <h1>{d.name}</h1>
        </div>
        <ConnectivityBadge value={connectivity(d)} />
      </div>
      <ErrorBox error={actionError} />

      <div className="grid-2">
        <section className="card">
          <h2>Dispositivo</h2>
          <dl className="dl">
            <dt>Tipo</dt><dd>{KIND_LABEL[d.kind]} {d.roles.map((r) => <span key={r} className="chip">{r}</span>)}</dd>
            <dt>Hostname</dt><dd>{d.hostname}</dd>
            <dt>Modello</dt><dd>{d.model ?? "—"}</dd>
            <dt>Seriale</dt><dd className="mono">{d.serial ?? "—"}</dd>
            <dt>Sistema</dt><dd>{d.os_version ?? "—"}</dd>
            <dt>Piattaforma</dt><dd>{d.platform} / {d.arch}</dd>
            <dt>Inventario</dt><dd>{ago(d.inventory_at)}</dd>
          </dl>
        </section>
        <section className="card">
          <h2>Agente</h2>
          {d.agent ? (
            <>
              <dl className="dl">
                <dt>Stato</dt><dd>{AGENT_STATUS_LABEL[d.agent.status]}</dd>
                <dt>Versione</dt><dd className="mono">{d.agent.version ?? "—"}</dd>
                <dt>Ultimo contatto</dt><dd title={dateTime(d.agent.last_seen_at)}>{ago(d.agent.last_seen_at)} {d.agent.last_ip && <span className="muted">da {d.agent.last_ip}</span>}</dd>
                <dt>Enrollment</dt><dd>{dateTime(d.agent.enrolled_at)}</dd>
                <dt>Certificato</dt><dd>scade {dateTime(d.agent.cert_not_after)}</dd>
                <dt>Shell remota</dt><dd>{d.agent.allow_shell ? "consentita dal dispositivo" : "disabilitata sul dispositivo"}</dd>
              </dl>
              {writable && d.agent.status !== "revoked" && (
                <div className="btn-row" style={{ marginTop: 14 }}>
                  {d.agent.status === "pending" && (
                    <button className="btn primary" onClick={() => void setStatus("approve")}>Approva</button>
                  )}
                  <button className="btn danger" onClick={() => void setStatus("revoke")}>Revoca</button>
                </div>
              )}
            </>
          ) : (
            <Empty>Nessun agente</Empty>
          )}
        </section>
      </div>

      <section className="card">
        <h2>Problemi</h2>
        {(problems.data ?? []).length === 0 ? (
          <div className="muted">Nessun problema aperto.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <tbody>
                {problems.data!.map((p) => (
                  <tr key={p.id}>
                    <td style={{ width: 1 }}><SeverityBadge severity={p.severity} /></td>
                    <td>{p.title}</td>
                    <td className="nowrap muted">dal {dateTime(p.first_seen)}</td>
                    <td style={{ width: 1 }}><StateBadge state={p.state} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <div className="page-head" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Spazio occupato</h2>
          <div className="btn-row" role="group" aria-label="Periodo">
            {RANGES.map((r) => (
              <button
                key={r.hours} className={`btn small ${hours === r.hours ? "primary" : ""}`}
                onClick={() => setHours(r.hours)} aria-pressed={hours === r.hours}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
        <LineChart
          series={series}
          thresholds={[{ value: 85, label: "attenzione" }, { value: 95, label: "critico" }]}
        />
      </section>

      <section className="card">
        <h2>Volumi</h2>
        {volumes.length === 0 ? (
          <div className="muted">Nessun volume nell'ultimo inventario.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Mount</th><th>Filesystem</th><th className="num">Totale</th><th className="num">Libero</th><th>Occupato</th></tr>
              </thead>
              <tbody>
                {volumes.map((v) => (
                  <tr key={v.mount}>
                    <td className="mono">{v.mount}</td>
                    <td className="muted">{v.fs_type ?? "—"}</td>
                    <td className="num">{bytes(v.total_bytes)}</td>
                    <td className="num">{bytes(v.free_bytes)}</td>
                    <td><UsageBar pct={v.used_pct} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {qnap && (
        <div className="grid-2" style={{ marginTop: 16 }}>
          <section className="card">
            <h2>Dischi</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Slot</th><th>Modello</th><th>SMART</th><th className="num">Temp.</th></tr></thead>
                <tbody>
                  {(qnap.disks ?? []).map((k) => (
                    <tr key={k.slot}>
                      <td>{k.slot}</td>
                      <td>{k.model || "—"}</td>
                      <td>{k.smart || "—"}</td>
                      <td className="num">{k.temp_c != null ? `${k.temp_c}°C` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card">
            <h2>RAID / pool</h2>
            {(qnap.arrays ?? []).length === 0 ? (
              <div className="muted">Nessun array rilevato.</div>
            ) : (
              <div className="table-wrap">
                <table>
                  <tbody>
                    {qnap.arrays!.map((a) => (
                      <tr key={a.name}>
                        <td className="mono">{a.name}</td>
                        <td>{a.healthy ? <span className="badge good">Integro</span> : <span className="badge critical">Degradato</span>}</td>
                        <td className="mono muted">{a.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="muted" style={{ marginTop: 10, fontSize: 12.5 }}>
              Firmware {qnap.firmware ?? "—"} build {qnap.build ?? "—"}
            </div>
          </section>
        </div>
      )}
    </>
  );
}
