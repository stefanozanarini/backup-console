import { useCallback, useEffect, useRef, useState } from "react";
import { authHeaders } from "./auth";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

interface ApiInit {
  method?: string;
  body?: unknown;
  form?: FormData;
  headers?: Record<string, string>;
}

export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(await authHeaders()), ...(init.headers ?? {}) };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(`/api/v1${path}`, {
    method: init.method ?? (init.form ? "POST" : "GET"),
    headers,
    body: init.form ?? (init.body === undefined ? undefined : JSON.stringify(init.body)),
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* corpo non JSON */
    }
    throw new ApiError(r.status, detail);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

/** Scarica un file dall'API (serve l'header di autenticazione, quindi niente link diretto). */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const r = await fetch(`/api/v1${path}`, { headers: await authHeaders() });
  if (!r.ok) throw new ApiError(r.status, r.statusText);
  const name = /filename="?([^";]+)"?/.exec(r.headers.get("content-disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Chiede ai contatori globali (sidebar) di ricaricarsi subito. */
export function notifyChange(): void {
  window.dispatchEvent(new Event("bc:changed"));
}

/** GET con ricarica manuale e, se indicato, aggiornamento periodico. */
export function useApi<T>(path: string | null, refreshMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const current = useRef(path);
  current.current = path;

  const load = useCallback(async () => {
    if (path === null) return;
    try {
      const d = await api<T>(path);
      if (current.current === path) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (current.current === path) setError(e as Error);
    } finally {
      if (current.current === path) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setLoading(path !== null);
    void load();
    if (!refreshMs || path === null) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, refreshMs);
    const onChange = () => void load();
    window.addEventListener("bc:changed", onChange);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("bc:changed", onChange);
    };
  }, [load, path, refreshMs]);

  return { data, error, loading, reload: load };
}
