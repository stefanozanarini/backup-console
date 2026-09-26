import type { AgentStatus, Device, DeviceKind } from "./types";

const rtf = new Intl.RelativeTimeFormat("it", { numeric: "auto" });
const dtf = new Intl.DateTimeFormat("it-IT", { dateStyle: "short", timeStyle: "short" });

export function ago(iso: string | null | undefined): string {
  if (!iso) return "mai";
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(s);
  if (abs < 60) return "adesso";
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}

export function dateTime(iso: string | null | undefined): string {
  return iso ? dtf.format(new Date(iso)) : "—";
}

export function bytes(n: number | undefined): string {
  if (n === undefined || !Number.isFinite(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export const KIND_LABEL: Record<DeviceKind, string> = { nas: "NAS", server: "Server", pc: "PC" };

// Allineato a BC_AGENT_OFFLINE_AFTER_S (default 300s) più un margine per il ciclo del worker.
const OFFLINE_AFTER_MS = 6 * 60 * 1000;

export type Connectivity = "online" | "offline" | "pending" | "revoked";

export function connectivity(d: Device): Connectivity {
  if (!d.agent || d.agent.status === "revoked") return "revoked";
  if (d.agent.status === "pending") return "pending";
  const seen = d.agent.last_seen_at ? new Date(d.agent.last_seen_at).getTime() : 0;
  return Date.now() - seen < OFFLINE_AFTER_MS ? "online" : "offline";
}

export const CONNECTIVITY_LABEL: Record<Connectivity, string> = {
  online: "Online",
  offline: "Offline",
  pending: "Da approvare",
  revoked: "Revocato",
};

export const AGENT_STATUS_LABEL: Record<AgentStatus, string> = {
  pending: "In attesa di approvazione",
  approved: "Approvato",
  revoked: "Revocato",
};
