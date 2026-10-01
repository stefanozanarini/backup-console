import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, notifyChange, useApi } from "../api";
import { ConnectivityBadge, CopyButton, Empty, ErrorBox, SeverityBadge, StateBadge, UsageBar } from "../components";
import { useConsole } from "../context";
import { AGENT_STATUS_LABEL, ago, bytes, connectivity, dateTime, KIND_LABEL } from "../format";
import CommandPanel from "../CommandPanel";
import LineChart, { type Series } from "../LineChart";
import type { DeviceDetail, MetricSeries, NasNeighbors, Problem, Release } from "../types";
import { DeviceBackupWindow } from "../BackupWindow";
import { BackupAppsSections, VeeamSection } from "../veeam";
import VeeamCleanup from "../VeeamCleanup";

const RANGES = [
  { hours: 24, label: "24 ore" },
  { hours: 24 * 7, label: "7 giorni" },
  { hours: 24 * 30, label: "30 giorni" },
];

export default function DeviceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { tenantName, canWrite, isSuperadmin } = useConsole();
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
  // NAS: QNAP e Synology riportano dischi e RAID nello stesso formato.
  const nas = d.inventory?.data.qnap ?? d.inventory?.data.synology;
  const qnap = nas;
  const writable = canWrite(d.tenant_id);

  async function setStatus(action: "approve" | "revoke") {
    if (!d?.agent) return;
    if (action === "revoke" && !confirm(`Revocare l'agente di ${d.name}? Servirà un nuovo enrollment.`)) return;
    setActionError(null);
    try {
      await api(`/agents/${d.agent.id}/${action}`, { method: "POST" });
      await device.reload();
      notifyChange();
    } catch (e) {
      setActionError(e as Error);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <div className="sub"><Link to="/dispositivi">Dispositivi</Link> / {tenantName(d.tenant_id)}</div>
          <DeviceName id={d.id} name={d.name} writable={writable} onSaved={() => { void device.reload(); notifyChange(); }} />
        </div>
        <ConnectivityBadge value={connectivity(d)} />
      </div>
      <ErrorBox error={actionError} />
      <UnmanagedNasAlert neighbors={d.nas_neighbors} />

      <div className="grid-2">
        <section className="card">
          <h2>Dispositivo</h2>
          <dl className="dl">
            <dt>Tipo</dt><dd>{KIND_LABEL[d.kind]} {d.roles.map((r) => <span key={r} className="chip">{r}</span>)}</dd>
            <dt>Hostname</dt><dd>{d.hostname}</dd>
            <dt>Modello</dt><dd>{d.model ?? "—"}</dd>
            <dt>Seriale</dt><dd className="mono">{d.serial ?? "—"}</dd>
            {d.supremo_id && (
              <>
                <dt>ID Supremo</dt>
                <dd className="form-row" style={{ gap: 8 }}>
                  <span className="mono">{d.supremo_id.replace(/(\d{3})(?=\d)/g, "$1 ")}</span>
                  <CopyButton text={d.supremo_id} />
                </dd>
              </>
            )}
            <dt>Indirizzi IP</dt>
            <dd>
              {(d.ip_addresses ?? []).length === 0 ? "—" : (d.ip_addresses ?? []).map((n) => (
                <div key={n.name + (n.mac ?? "")}>
                  <span className="mono">{n.ipv4.join(", ")}</span>{" "}
                  <span className="muted small">{n.name}{n.mac ? ` · ${n.mac}` : ""}</span>
                </div>
              ))}
            </dd>
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
                {Object.keys(d.probe_status ?? {}).length > 0 && (
                  <>
                    <dt>Raccolta</dt>
                    <dd>
                      {Object.entries(d.probe_status ?? {}).map(([id, st]) => (
                        <div key={id} className="small" title={st.error ?? st.note ?? undefined}>
                          <span className={`badge ${st.ok ? "good" : "critical"}`}>{st.ok ? "OK" : "Errore"}</span>{" "}
                          <span className="mono">{id}</span> <span className="muted">{ago(st.at)} · pacchetto {st.pack}</span>
                          {st.error && <div className="muted">{st.error}</div>}
                        </div>
                      ))}
                    </dd>
                  </>
                )}
              </dl>
              {writable && d.agent.status !== "revoked" && (
                <div className="btn-row" style={{ marginTop: 14 }}>
                  {d.agent.status === "pending" && (
                    <button className="btn primary" onClick={() => void setStatus("approve")}>Approva</button>
                  )}
                  <button className="btn danger" onClick={() => void setStatus("revoke")}>Revoca</button>
                </div>
              )}
              {isSuperadmin && d.agent.status === "approved" && (
                <UpgradeControl deviceId={d.id} current={d.agent.version} onError={setActionError} />
              )}
            </>
          ) : (
            <Empty>Nessun agente</Empty>
          )}
        </section>
      </div>

      {d.nas_neighbors && <NasNeighborsSection neighbors={d.nas_neighbors} />}

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

      <VeeamSection device={d} writable={writable} />
      <BackupAppsSections device={d} />
      <DeviceBackupWindow deviceId={d.id} writable={writable} />
      <VeeamCleanup device={d} writable={writable} />

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
              {d.inventory?.data.synology ? qnap.firmware ?? "—" : <>Firmware {qnap.firmware ?? "—"} build {qnap.build ?? "—"}</>}
            </div>
          </section>
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <CommandPanel device={d} writable={writable} />
      </div>
    </>
  );
}

function UpgradeControl(props: { deviceId: string; current: string | null; onError: (e: Error | null) => void }) {
  const releases = useApi<Release[]>("/releases");
  const [version, setVersion] = useState("");
  const [queued, setQueued] = useState<string | null>(null);
  const options = (releases.data ?? []).filter((r) => r.version !== props.current);
  if (options.length === 0) return null;

  async function upgrade() {
    const v = version || options[0].version;
    props.onError(null);
    try {
      const r = await api<{ queued: number; skipped: Record<string, number> }>("/upgrades", {
        method: "POST",
        body: { version: v, device_ids: [props.deviceId] },
      });
      setQueued(r.queued ? `Aggiornamento a ${v} in coda` : `Non accodato: ${Object.keys(r.skipped).join(", ")}`);
    } catch (e) {
      props.onError(e as Error);
    }
  }

  return (
    <div className="form-row" style={{ marginTop: 12 }}>
      <select value={version} onChange={(e) => setVersion(e.target.value)} aria-label="Versione">
        {options.map((r) => <option key={r.version} value={r.version}>{r.version}</option>)}
      </select>
      <button className="btn" onClick={() => void upgrade()}>Aggiorna agente</button>
      {queued && <span className="muted">{queued}</span>}
    </div>
  );
}

/** Nome del dispositivo in console, modificabile (l'hostname resta nei dettagli). */
function DeviceName(props: { id: string; name: string; writable: boolean; onSaved: () => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (editing === null) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/devices/${props.id}`, { method: "PATCH", body: { name: editing } });
      setEditing(null);
      props.onSaved();
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  }

  if (editing === null) {
    return (
      <div className="form-row" style={{ gap: 10 }}>
        <h1>{props.name}</h1>
        {props.writable && <button className="btn small" onClick={() => setEditing(props.name)}>Rinomina</button>}
      </div>
    );
  }
  return (
    <form className="stack" style={{ gap: 6 }} onSubmit={save}>
      <div className="form-row">
        <input type="text" value={editing} maxLength={200} required autoFocus aria-label="Nome del dispositivo"
          style={{ fontSize: 18, minWidth: 320 }} onChange={(e) => setEditing(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); }} />
        <button className="btn primary" type="submit" disabled={busy || !editing.trim()}>Salva</button>
        <button className="btn" type="button" onClick={() => setEditing(null)}>Annulla</button>
      </div>
      <ErrorBox error={error} />
    </form>
  );
}

const NAS_KIND: Record<string, string> = { qnap: "QNAP", synology: "Synology" };

function unmanaged(n: NasNeighbors | null | undefined) {
  return (n?.found ?? []).filter((f) => !f.device_id);
}

function UnmanagedNasAlert({ neighbors }: { neighbors: NasNeighbors | null | undefined }) {
  const missing = unmanaged(neighbors);
  if (missing.length === 0) return null;
  return (
    <div className="notice warn" role="status" style={{ marginBottom: 12 }}>
      ⚠ {missing.length === 1 ? "Un NAS nella stessa rete non è" : `${missing.length} NAS nella stessa rete non sono`} in
      console: {missing.map((f) => f.hostname ?? f.ip).join(", ")}. <a href="#nas-rete">Dettagli</a>
    </div>
  );
}

function NasNeighborsSection({ neighbors }: { neighbors: NasNeighbors }) {
  const nets = neighbors.networks.map((n) => `${n.ip}/${n.prefix ?? "?"}`).join(", ");
  return (
    <section className="card" id="nas-rete">
      <h2>NAS nella stessa rete</h2>
      <div className="muted small" style={{ marginBottom: 8 }}>
        Ricerca giornaliera su {nets || "—"} · ultima {ago(neighbors.at)}
      </div>
      {neighbors.found.length === 0 ? (
        <Empty>Nessun altro NAS trovato</Empty>
      ) : (
        <div className="table-wrap">
          <table className="compact">
            <thead>
              <tr><th>IP</th><th>Tipo</th><th>Modello</th><th>Nome</th><th>Firmware</th><th>MAC</th><th>In console</th></tr>
            </thead>
            <tbody>
              {neighbors.found.map((f) => (
                <tr key={f.ip}>
                  <td className="mono">{f.ip}</td>
                  <td>{NAS_KIND[f.kind] ?? f.kind}</td>
                  <td>{f.model ?? "—"}</td>
                  <td>{f.hostname ?? "—"}</td>
                  <td>{f.firmware ?? "—"}</td>
                  <td className="mono small">{f.mac ?? "—"}</td>
                  <td>
                    {f.device_id
                      ? <Link to={`/dispositivi/${f.device_id}`}>{f.device_name}</Link>
                      : <span className="badge warning">Non aggiunto</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
