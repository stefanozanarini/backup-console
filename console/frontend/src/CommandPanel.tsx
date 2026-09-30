// Esecuzione remota sul dispositivo: editor del comando, output in tempo reale, storico.
import { useEffect, useRef, useState } from "react";
import { api, ApiError, useApi } from "./api";
import { ErrorBox } from "./components";
import { useElevation } from "./elevation";
import { ago, dateTime } from "./format";
import type { Command, CommandDetail, CommandStatus, DeviceDetail } from "./types";

export const FINAL: CommandStatus[] = ["succeeded", "failed", "timeout", "expired"];

export const COMMAND_STATUS_LABEL: Record<CommandStatus, string> = {
  queued: "In coda",
  dispatched: "Inviato",
  running: "In esecuzione",
  succeeded: "Completato",
  failed: "Fallito",
  timeout: "Timeout",
  expired: "Non ritirato",
};

export function CommandStatusBadge({ status }: { status: CommandStatus }) {
  const cls = status === "succeeded" ? "good" : status === "failed" || status === "timeout" || status === "expired" ? "critical" : "";
  return <span className={`badge ${cls}`}>{COMMAND_STATUS_LABEL[status]}</span>;
}

function commandLabel(c: Command): string {
  switch (c.type) {
    case "upgrade":
      return `⟳ aggiornamento ${c.payload.from ?? ""} → ${c.payload.version}`;
    case "veeam_scan":
      return `analisi backup Veeam (più vecchi di ${c.payload.older_than_days} giorni)`;
    case "veeam_cleanup":
      return `pulizia backup Veeam: ${c.payload.backup_ids?.length ?? 0} backup`;
    case "veeam_job_start":
      return `${c.payload.mode === "retry" ? "retry" : "avvio"} del job Veeam ${c.payload.job_name ?? ""}`;
    default:
      return c.payload.command ?? "";
  }
}

/** Segue un comando finché non termina (polling ogni secondo). */
export function useCommand(id: string | null) {
  const [cmd, setCmd] = useState<CommandDetail | null>(null);
  useEffect(() => {
    setCmd(null);
    if (!id) return;
    let stop = false;
    let timer = 0;
    const tick = async () => {
      try {
        const c = await api<CommandDetail>(`/commands/${id}`);
        if (stop) return;
        setCmd(c);
        if (FINAL.includes(c.status)) return;
      } catch {
        /* riproviamo al giro successivo */
      }
      if (!stop) timer = window.setTimeout(tick, 1000);
    };
    void tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [id]);
  return cmd;
}

export default function CommandPanel({ device, writable }: { device: DeviceDetail; writable: boolean }) {
  const { require, end } = useElevation();
  const windows = device.platform === "windows";
  const [command, setCommand] = useState("");
  const [shell, setShell] = useState("default");
  const [timeout, setTimeoutS] = useState(300);
  const [error, setError] = useState<Error | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const history = useApi<Command[]>(`/devices/${device.id}/commands?limit=15`, 10000);
  const current = useCommand(selected);
  const out = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (current && !FINAL.includes(current.status)) void history.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.status]);
  useEffect(() => {
    if (out.current) out.current.scrollTop = out.current.scrollHeight;
  }, [current?.output]);

  const agent = device.agent;
  const disabledReason =
    !agent || agent.status !== "approved"
      ? "L'agente non è approvato."
      : !agent.allow_shell
        ? "Esecuzione remota disattivata sul dispositivo (installato con -disable-remote-exec)."
        : !writable
          ? "Non hai i permessi di scrittura su questo tenant."
          : null;

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const token = await require();
    if (!token) return;
    try {
      const c = await api<Command>(`/devices/${device.id}/commands`, {
        method: "POST",
        headers: { "X-Elevation": token },
        body: { command, shell, timeout_s: timeout },
      });
      setSelected(c.id);
      await history.reload();
    } catch (err) {
      if (err instanceof ApiError && err.message === "elevation_required") {
        end(); // scaduta o revocata lato server: la richiederemo al prossimo invio
        setError(new Error("Sessione elevata scaduta: premi di nuovo Esegui."));
      } else {
        setError(err as Error);
      }
    }
  }

  return (
    <section className="card">
      <h2>Esecuzione remota</h2>
      {disabledReason ? (
        <div className="notice">{disabledReason}</div>
      ) : (
        <form className="stack" onSubmit={run}>
          <textarea
            className="mono" rows={4} required value={command} spellCheck={false}
            placeholder={windows ? "Get-Service Veeam* | Format-Table -Auto" : "df -h; rsync -a --dry-run /share/Dati/ /share/Backup/"}
            onChange={(e) => setCommand(e.target.value)} aria-label="Comando"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) (e.currentTarget.form as HTMLFormElement).requestSubmit();
            }}
          />
          <div className="form-row">
            <select value={shell} onChange={(e) => setShell(e.target.value)} aria-label="Shell">
              <option value="default">{windows ? "PowerShell (predefinita)" : "sh (predefinita)"}</option>
              {windows ? (
                <>
                  <option value="pwsh">PowerShell 7</option>
                  <option value="cmd">cmd.exe</option>
                </>
              ) : (
                <option value="bash">bash</option>
              )}
            </select>
            <select value={timeout} onChange={(e) => setTimeoutS(Number(e.target.value))} aria-label="Timeout">
              <option value={60}>timeout 1 min</option>
              <option value={300}>timeout 5 min</option>
              <option value={1800}>timeout 30 min</option>
              <option value={3600}>timeout 60 min</option>
            </select>
            <span style={{ flex: 1 }} />
            <button className="btn primary" type="submit">Esegui</button>
          </div>
          <ErrorBox error={error} />
        </form>
      )}

      {current && (
        <div style={{ marginTop: 14 }}>
          <div className="cmd-status">
            <CommandStatusBadge status={current.status} />
            {current.exit_code !== null && <span>codice di uscita {current.exit_code}</span>}
            {current.reason && <span>motivo: {current.reason}</span>}
            {current.error && <span className="badge critical">{current.error}</span>}
          </div>
          <pre className="terminal mono" ref={out}>
            <span className="meta">$ {commandLabel(current)}{"\n"}</span>
            {current.output || (FINAL.includes(current.status) ? "" : "in attesa dell'agente…")}
            {current.output_truncated && <span className="meta">{"\n"}[output troncato a 1 MB]</span>}
          </pre>
        </div>
      )}

      {(history.data ?? []).length > 0 && (
        <div className="table-wrap cmd-history" style={{ marginTop: 14 }}>
          <table>
            <thead>
              <tr><th>Quando</th><th>Comando</th><th>Motivo</th><th>Esito</th></tr>
            </thead>
            <tbody>
              {history.data!.map((c) => (
                <tr key={c.id}>
                  <td className="nowrap muted" title={dateTime(c.created_at)}>{ago(c.created_at)}</td>
                  <td>
                    <button className="linklike mono cmdline" onClick={() => setSelected(c.id)} title={c.payload.command}>
                      {commandLabel(c)}
                    </button>
                  </td>
                  <td className="muted">{c.reason}</td>
                  <td className="nowrap">
                    <CommandStatusBadge status={c.status} />
                    {c.exit_code !== null && c.exit_code !== 0 && <span className="muted"> ({c.exit_code})</span>}
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
