import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { Empty, ErrorBox } from "../components";
import { useConsole } from "../context";
import { dateTime, KIND_LABEL } from "../format";
import type { Device } from "../types";

export default function Approvals() {
  const { tenantName, canWrite } = useConsole();
  const { data, error, reload } = useApi<Device[]>("/devices?agent_status=pending", 30000);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<Error | null>(null);

  async function act(d: Device, action: "approve" | "revoke") {
    if (!d.agent) return;
    if (action === "revoke" && !confirm(`Rifiutare ${d.name}? L'agente dovrà rifare l'enrollment.`)) return;
    setBusy(d.id);
    setActionError(null);
    try {
      await api(`/agents/${d.agent.id}/${action}`, { method: "POST" });
      await reload();
    } catch (e) {
      setActionError(e as Error);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Approvazioni</h1>
          <div className="sub">Agenti che hanno completato l'enrollment e attendono conferma</div>
        </div>
      </div>
      <ErrorBox error={error ?? actionError} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Nome</th><th>Tenant</th><th>Tipo</th><th>Piattaforma</th><th>Ruoli</th><th>Da IP</th><th>Enrollment</th><th /></tr>
          </thead>
          <tbody>
            {(data ?? []).map((d) => (
              <tr key={d.id}>
                <td className="nowrap"><Link to={`/dispositivi/${d.id}`}>{d.name}</Link></td>
                <td className="nowrap">{tenantName(d.tenant_id)}</td>
                <td>{KIND_LABEL[d.kind]}</td>
                <td>{d.platform} / {d.arch}</td>
                <td>{d.roles.map((r) => <span key={r} className="chip">{r}</span>)}</td>
                <td className="mono">{d.agent?.last_ip ?? "—"}</td>
                <td className="nowrap">{dateTime(d.agent?.enrolled_at)}</td>
                <td className="num">
                  {canWrite(d.tenant_id) && (
                    <div className="btn-row" style={{ justifyContent: "flex-end" }}>
                      <button className="btn small primary" disabled={busy === d.id} onClick={() => void act(d, "approve")}>Approva</button>
                      <button className="btn small danger" disabled={busy === d.id} onClick={() => void act(d, "revoke")}>Rifiuta</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data?.length === 0 && <Empty>Nessun agente in attesa</Empty>}
      </div>
    </>
  );
}
