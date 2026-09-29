// Finestra di backup dei PC: intervalli settimanali in cui l'agente tiene il PC
// disponibile per il backup (niente sospensione automatica) e avvisa l'utente.
import { useEffect, useState } from "react";
import { api, useApi } from "./api";
import { ErrorBox } from "./components";
import { dateTime } from "./format";

interface Point { day: number; time: string }
interface Interval { start: Point; end: Point }
interface WindowCfg { enabled: boolean; intervals: Interval[] }
interface WindowState {
  tenant: WindowCfg | null;
  device: WindowCfg | null;
  effective: WindowCfg | null;
  source: "device" | "tenant" | "none";
  applies: boolean;
  active: boolean;
  until: string | null;
}

const DAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"];
const DEFAULT: Interval = { start: { day: 4, time: "18:00" }, end: { day: 1, time: "09:00" } };

export function describe(w: WindowCfg | null): string {
  if (!w || !w.enabled || w.intervals.length === 0) return "nessuna";
  return w.intervals
    .map((i) => `dal ${DAYS[i.start.day - 1]} alle ${i.start.time} al ${DAYS[i.end.day - 1]} alle ${i.end.time}`)
    .join("; ");
}

function PointInput({ value, onChange, label }: { value: Point; onChange: (p: Point) => void; label: string }) {
  return (
    <>
      <select value={value.day} onChange={(e) => onChange({ ...value, day: Number(e.target.value) })} aria-label={`${label}: giorno`}>
        {DAYS.map((d, i) => <option key={d} value={i + 1}>{d}</option>)}
      </select>
      <input type="time" value={value.time} required onChange={(e) => onChange({ ...value, time: e.target.value })}
        aria-label={`${label}: ora`} style={{ width: 110 }} />
    </>
  );
}

function IntervalsEditor({ value, onChange }: { value: Interval[]; onChange: (v: Interval[]) => void }) {
  const set = (i: number, iv: Interval) => onChange(value.map((x, j) => (j === i ? iv : x)));
  return (
    <div className="stack" style={{ gap: 8 }}>
      {value.map((iv, i) => (
        <div key={i} className="form-row">
          <span className="muted">dal</span>
          <PointInput label="Inizio" value={iv.start} onChange={(p) => set(i, { ...iv, start: p })} />
          <span className="muted">al</span>
          <PointInput label="Fine" value={iv.end} onChange={(p) => set(i, { ...iv, end: p })} />
          {value.length > 1 && (
            <button type="button" className="btn small" onClick={() => onChange(value.filter((_, j) => j !== i))}>Rimuovi</button>
          )}
        </div>
      ))}
      {value.length < 10 && (
        <div>
          <button type="button" className="btn small" onClick={() => onChange([...value, DEFAULT])}>Aggiungi intervallo</button>
        </div>
      )}
    </div>
  );
}

function ActiveLine({ st }: { st: WindowState }) {
  if (!st.effective) return null;
  return st.active
    ? <div className="notice">In corso adesso, fino a {dateTime(st.until)}.</div>
    : <div className="muted small">Non attiva in questo momento.</div>;
}

const NOTE = "Durante la finestra l'agente disattiva la sospensione automatica e avvisa l'utente dall'icona " +
  "nella tray; l'utente può comunque spegnere. Se il PC risulta spento la console apre un problema. " +
  "Vale solo per i PC Windows con Veeam Agent.";

/** Impostazione del tenant (predefinita per tutti i suoi PC). */
export function TenantBackupWindow({ tenantId, writable }: { tenantId: string; writable: boolean }) {
  const state = useApi<WindowState>(`/tenants/${tenantId}/backup-window`);
  const [editing, setEditing] = useState<Interval[] | null>(null);
  const [error, setError] = useState<Error | null>(null);

  async function save(body: WindowCfg | null) {
    setError(null);
    try {
      await api(`/tenants/${tenantId}/backup-window`, { method: "PUT", body });
      setEditing(null);
      await state.reload();
    } catch (e) {
      setError(e as Error);
    }
  }

  const st = state.data;
  return (
    <div style={{ padding: "8px 0", borderTop: "1px solid var(--border)" }}>
      <div className="form-row" style={{ justifyContent: "space-between" }}>
        <span>
          Finestra di backup dei PC: <strong>{st ? describe(st.tenant) : "…"}</strong>
        </span>
        {writable && st && !editing && (
          <span className="btn-row">
            <button className="btn small" onClick={() => setEditing(st.tenant?.intervals.length ? st.tenant.intervals : [DEFAULT])}>
              {st.tenant ? "Modifica" : "Imposta"}
            </button>
            {st.tenant && <button className="btn small" onClick={() => void save(null)}>Rimuovi</button>}
          </span>
        )}
      </div>
      <ErrorBox error={state.error ?? error} />
      {st && <ActiveLine st={st} />}
      {editing && (
        <form className="stack" style={{ marginTop: 8 }}
          onSubmit={(e) => { e.preventDefault(); void save({ enabled: true, intervals: editing }); }}>
          <IntervalsEditor value={editing} onChange={setEditing} />
          <div className="muted small">{NOTE} I PC con un'impostazione propria non cambiano.</div>
          <div className="btn-row">
            <button className="btn primary" type="submit">Salva</button>
            <button className="btn" type="button" onClick={() => setEditing(null)}>Annulla</button>
          </div>
        </form>
      )}
    </div>
  );
}

type Mode = "inherit" | "custom" | "off";

/** Impostazione del singolo PC: eredita dal tenant, propria o disattivata. */
export function DeviceBackupWindow({ deviceId, writable }: { deviceId: string; writable: boolean }) {
  const state = useApi<WindowState>(`/devices/${deviceId}/backup-window`);
  const [mode, setMode] = useState<Mode>("inherit");
  const [intervals, setIntervals] = useState<Interval[]>([DEFAULT]);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const st = state.data;

  useEffect(() => {
    if (!st || dirty) return;
    if (!st.device) setMode("inherit");
    else if (!st.device.enabled) setMode("off");
    else {
      setMode("custom");
      setIntervals(st.device.intervals);
    }
  }, [st, dirty]);

  if (!st || !st.applies) return null;

  async function save() {
    setError(null);
    const body = mode === "inherit" ? null : mode === "off" ? { enabled: false, intervals: [] } : { enabled: true, intervals };
    try {
      await api(`/devices/${deviceId}/backup-window`, { method: "PUT", body });
      setDirty(false);
      await state.reload();
    } catch (e) {
      setError(e as Error);
    }
  }

  const change = (m: Mode) => { setMode(m); setDirty(true); };
  return (
    <section className="card">
      <h2>Finestra di backup</h2>
      <div className="stack" style={{ gap: 8 }}>
        <div>
          In vigore: <strong>{describe(st.effective)}</strong>{" "}
          <span className="muted small">
            ({st.source === "device" ? "impostazione del PC" : st.source === "tenant" ? "dal tenant" : "nessuna impostazione"})
          </span>
        </div>
        <ActiveLine st={st} />
        {writable && (
          <form className="stack" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); void save(); }}>
            <div className="form-row">
              <label><input type="radio" checked={mode === "inherit"} onChange={() => change("inherit")} /> Come il tenant ({describe(st.tenant)})</label>
            </div>
            <div className="form-row">
              <label><input type="radio" checked={mode === "custom"} onChange={() => change("custom")} /> Personalizzata per questo PC</label>
            </div>
            {mode === "custom" && (
              <div style={{ paddingLeft: 24 }}>
                <IntervalsEditor value={intervals} onChange={(v) => { setIntervals(v); setDirty(true); }} />
              </div>
            )}
            <div className="form-row">
              <label><input type="radio" checked={mode === "off"} onChange={() => change("off")} /> Nessuna finestra per questo PC</label>
            </div>
            <div className="muted small">{NOTE}</div>
            <ErrorBox error={state.error ?? error} />
            {dirty && (
              <div className="btn-row">
                <button className="btn primary" type="submit">Salva</button>
                <button className="btn" type="button" onClick={() => setDirty(false)}>Annulla</button>
              </div>
            )}
          </form>
        )}
      </div>
    </section>
  );
}
