import { useState } from "react";
import type { ReactNode } from "react";
import { CONNECTIVITY_LABEL, type Connectivity } from "./format";
import type { ProblemState, Severity } from "./types";

// Icone di stato: la gravità non è mai affidata al solo colore.
function IconCritical() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M6 .8 11.4 10.6H.6Z" fill="currentColor" />
      <path d="M6 4.3v3M6 8.6v.1" stroke="#fff" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}
function IconWarning() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r="5.4" fill="currentColor" />
      <path d="M6 3.2v3.3M6 8.6v.1" stroke="#1a1a19" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}
function IconOk() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r="5.4" fill="currentColor" />
      <path d="m3.6 6.2 1.6 1.6 3.2-3.4" stroke="#fff" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return severity === "critical" ? (
    <span className="badge critical"><IconCritical />Critico</span>
  ) : (
    <span className="badge warning"><IconWarning />Attenzione</span>
  );
}

const STATE_LABEL: Record<ProblemState, string> = {
  open: "Aperto",
  acknowledged: "Preso in carico",
  resolved: "Risolto",
};

export function StateBadge({ state }: { state: ProblemState }) {
  if (state === "resolved") return <span className="badge good"><IconOk />{STATE_LABEL[state]}</span>;
  return <span className="badge">{STATE_LABEL[state]}</span>;
}

export function ConnectivityBadge({ value }: { value: Connectivity }) {
  return (
    <span className="nowrap" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span className={`dot ${value}`} aria-hidden="true" />
      {CONNECTIVITY_LABEL[value]}
    </span>
  );
}

export function UsageBar({ pct, warn = 85, crit = 95 }: { pct: number; warn?: number; crit?: number }) {
  const cls = pct >= crit ? "critical" : pct >= warn ? "warning" : "";
  return (
    <div className="usage">
      <div className="usage-bar" role="img" aria-label={`${pct.toFixed(0)}% occupato`}>
        <span className={cls} style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
      </div>
      <span className="pct">{pct.toFixed(0)}%</span>
    </div>
  );
}

export function ErrorBox({ error }: { error: Error | null }) {
  if (!error) return null;
  return <div className="alert" role="alert">Errore: {error.message}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn small" type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          window.setTimeout(() => setDone(false), 1500);
        });
      }}
    >
      {done ? "Copiato" : "Copia"}
    </button>
  );
}
