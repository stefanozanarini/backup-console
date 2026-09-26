// Sessione elevata: codice TOTP personale + motivo, valida 15 minuti, richiesta dai
// comandi remoti. Il token vive solo nel sessionStorage della scheda.
import QRCode from "qrcode";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import { ErrorBox } from "./components";
import { useConsole } from "./context";

interface Elevation {
  token: string;
  expiresAt: number;
  reason: string;
}

interface ElevationCtx {
  elevation: Elevation | null;
  /** Ritorna il token valido, aprendo la richiesta del codice se serve (null se annullata). */
  require: () => Promise<string | null>;
  end: () => void;
}

const Ctx = createContext<ElevationCtx | null>(null);
const KEY = "bc.elevation";

function load(): Elevation | null {
  try {
    const e = JSON.parse(sessionStorage.getItem(KEY) ?? "null") as Elevation | null;
    return e && e.expiresAt > Date.now() ? e : null;
  } catch {
    return null;
  }
}

function save(e: Elevation | null) {
  try {
    if (e) sessionStorage.setItem(KEY, JSON.stringify(e));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage non disponibile: la sessione vale finché la pagina resta aperta */
  }
}

export function ElevationProvider({ children }: { children: ReactNode }) {
  const [elevation, setElevation] = useState<Elevation | null>(load);
  const [asking, setAsking] = useState(false);
  const waiters = useRef<((t: string | null) => void)[]>([]);

  useEffect(() => {
    if (!elevation) return;
    const id = window.setTimeout(() => setElevation(null), Math.max(0, elevation.expiresAt - Date.now()));
    return () => window.clearTimeout(id);
  }, [elevation]);

  const settle = useCallback((token: string | null) => {
    setAsking(false);
    waiters.current.splice(0).forEach((w) => w(token));
  }, []);

  const require = useCallback(() => {
    const current = load();
    if (current) return Promise.resolve(current.token);
    setAsking(true);
    return new Promise<string | null>((resolve) => waiters.current.push(resolve));
  }, []);

  const end = useCallback(() => {
    save(null);
    setElevation(null);
  }, []);

  return (
    <Ctx.Provider value={{ elevation, require, end }}>
      {children}
      {asking && (
        <ElevationDialog
          onCancel={() => settle(null)}
          onDone={(e) => {
            save(e);
            setElevation(e);
            settle(e.token);
          }}
        />
      )}
    </Ctx.Provider>
  );
}

export function useElevation(): ElevationCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("ElevationProvider mancante");
  return c;
}

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal card" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn small" onClick={onClose} aria-label="Chiudi">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

function ElevationDialog({ onCancel, onDone }: { onCancel: () => void; onDone: (e: Elevation) => void }) {
  const { me, reloadMe } = useConsole();
  const [code, setCode] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);

  if (!me.totp_enabled) {
    return (
      <Modal title="Configura il codice di sicurezza" onClose={onCancel}>
        <TotpSetup onEnabled={() => void reloadMe()} />
      </Modal>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ token: string; expires_at: string }>("/elevate", {
        method: "POST",
        body: { code: code.trim(), reason: reason.trim() },
      });
      onDone({ token: r.token, expiresAt: new Date(r.expires_at).getTime(), reason: reason.trim() });
    } catch (err) {
      setError(err as Error);
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Sblocca l'esecuzione remota" onClose={onCancel}>
      <form className="stack" onSubmit={submit}>
        <div className="notice">
          I comandi remoti richiedono il codice della tua app di autenticazione. La sessione dura 15 minuti
          ed è registrata con il motivo indicato.
        </div>
        <ErrorBox error={error} />
        <label className="field">
          Motivo
          <input type="text" required minLength={3} maxLength={300} value={reason} placeholder="es. ticket 1234, pulizia volume"
            onChange={(e) => setReason(e.target.value)} autoFocus />
        </label>
        <label className="field">
          Codice a 6 cifre
          <input type="text" inputMode="numeric" autoComplete="one-time-code" required pattern="[0-9]{6}" maxLength={6}
            value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="mono otp" />
        </label>
        <div className="btn-row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onCancel}>Annulla</button>
          <button type="submit" className="btn primary" disabled={busy}>Sblocca</button>
        </div>
      </form>
    </Modal>
  );
}

function TotpSetup({ onEnabled }: { onEnabled: () => void }) {
  const [setup, setSetup] = useState<{ secret: string; uri: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<Error | null>(null);

  async function start() {
    setError(null);
    try {
      const s = await api<{ secret: string; uri: string }>("/me/totp/setup", { method: "POST" });
      setSetup({ ...s, qr: await QRCode.toDataURL(s.uri, { margin: 1, width: 200 }) });
    } catch (err) {
      setError(err as Error);
    }
  }

  async function confirm(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api("/me/totp/confirm", { method: "POST", body: { code: code.trim() } });
      onEnabled();
    } catch (err) {
      setError(err as Error);
    }
  }

  if (!setup) {
    return (
      <div className="stack">
        <div className="notice">
          Per eseguire comandi sui dispositivi serve un secondo fattore personale, separato dal login:
          un codice TOTP generato da Microsoft Authenticator, Google Authenticator o simili.
        </div>
        <ErrorBox error={error} />
        <div className="btn-row" style={{ justifyContent: "flex-end" }}>
          <button className="btn primary" onClick={() => void start()}>Genera codice</button>
        </div>
      </div>
    );
  }
  return (
    <form className="stack" onSubmit={confirm}>
      <div>1. Inquadra il QR con l'app di autenticazione (o inserisci la chiave a mano).</div>
      <div className="qr-row">
        <img src={setup.qr} alt="QR code per l'app di autenticazione" width={200} height={200} />
        <div>
          <div className="muted" style={{ fontSize: 12.5 }}>Chiave</div>
          <code className="secret">{setup.secret.replace(/(.{4})/g, "$1 ").trim()}</code>
        </div>
      </div>
      <label className="field">
        2. Inserisci il codice mostrato dall'app per confermare
        <input type="text" inputMode="numeric" required pattern="[0-9]{6}" maxLength={6} value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="mono otp" autoFocus />
      </label>
      <ErrorBox error={error} />
      <div className="btn-row" style={{ justifyContent: "flex-end" }}>
        <button type="submit" className="btn primary">Attiva</button>
      </div>
    </form>
  );
}

export function ElevationBadge() {
  const { elevation, end } = useElevation();
  const [, tick] = useState(0);
  useEffect(() => {
    if (!elevation) return;
    const id = window.setInterval(() => tick((n) => n + 1), 15000);
    return () => window.clearInterval(id);
  }, [elevation]);
  if (!elevation) return null;
  const min = Math.max(1, Math.round((elevation.expiresAt - Date.now()) / 60000));
  return (
    <div className="elevation-badge" title={`Motivo: ${elevation.reason}`}>
      <span><span className="dot pending" aria-hidden="true" /> Sessione elevata · {min} min</span>
      <button className="btn small" onClick={end}>Termina</button>
    </div>
  );
}
