// Login: Entra ID via MSAL (produzione), utente locale con password + TOTP (temporaneo,
// BC_LOCAL_LOGIN) oppure utente dichiarato a mano (BC_AUTH_MODE=dev).
import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
} from "@azure/msal-browser";
import type { AuthConfig } from "./types";

const DEV_USER_KEY = "bc.devUser";
const LOCAL_KEY = "bc.localSession";

let config: AuthConfig | null = null;
let msal: PublicClientApplication | null = null;

export interface Session {
  mode: AuthConfig["mode"];
  /** Come è entrato l'utente corrente (null = non autenticato). */
  method: "oidc" | "local" | "dev" | null;
  email: string | null;
  agentsUrl: string;
  localLogin: boolean;
  /** Errore di configurazione di Entra ID (MSAL non inizializzabile). */
  oidcError: string | null;
}

interface LocalSession {
  token: string;
  email: string;
  expiresAt: number;
}

function readDevUser(): string | null {
  try {
    return localStorage.getItem(DEV_USER_KEY);
  } catch {
    return null;
  }
}

function readLocal(): LocalSession | null {
  try {
    const s = JSON.parse(sessionStorage.getItem(LOCAL_KEY) ?? "null") as LocalSession | null;
    return s && s.expiresAt > Date.now() ? s : null;
  } catch {
    return null;
  }
}

export function clearLocalSession(): void {
  try {
    sessionStorage.removeItem(LOCAL_KEY);
  } catch {
    /* ignora */
  }
}

export function hasLocalSession(): boolean {
  return readLocal() !== null;
}

export async function initAuth(): Promise<Session> {
  const r = await fetch("/api/v1/auth-config");
  if (!r.ok) throw new Error(`configurazione non disponibile (HTTP ${r.status})`);
  config = (await r.json()) as AuthConfig;

  if (config.mode === "dev") {
    const email = readDevUser();
    return { mode: "dev", method: email ? "dev" : null, email, agentsUrl: config.agents_url, localLogin: false, oidcError: null };
  }

  const localLogin = Boolean(config.local_login);
  const local = localLogin ? readLocal() : null;
  const base = { mode: "oidc" as const, agentsUrl: config.agents_url, localLogin };
  if (local) return { ...base, method: "local", email: local.email, oidcError: null };

  try {
    msal = new PublicClientApplication({
      auth: {
        clientId: config.client_id,
        authority: config.authority,
        redirectUri: window.location.origin,
      },
      cache: { cacheLocation: "sessionStorage" },
    });
    await msal.initialize();
    const result = await msal.handleRedirectPromise();
    const account: AccountInfo | null = result?.account ?? msal.getAllAccounts()[0] ?? null;
    if (account) msal.setActiveAccount(account);
    return { ...base, method: account ? "oidc" : null, email: account?.username ?? null, oidcError: null };
  } catch (e) {
    // Con Entra ID non configurato (o in errore) il login locale deve restare usabile.
    msal = null;
    return { ...base, method: null, email: null, oidcError: (e as Error).message };
  }
}

export async function login(devEmail?: string): Promise<void> {
  if (config?.mode === "dev") {
    if (!devEmail) return;
    try {
      localStorage.setItem(DEV_USER_KEY, devEmail);
    } catch {
      /* storage non disponibile: la sessione dura fino al reload */
    }
    window.location.reload();
    return;
  }
  if (msal && config?.mode === "oidc") {
    await msal.loginRedirect({ scopes: config.scopes });
  }
}

/** Login locale: lancia un errore con il messaggio dell'API se fallisce. */
export async function loginLocal(email: string, password: string, code: string): Promise<void> {
  const r = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, code }),
  });
  if (!r.ok) {
    let detail = r.status === 429 ? "troppi tentativi, riprova più tardi" : `errore ${r.status}`;
    try {
      const j = await r.json();
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* corpo non JSON (es. limite di nginx) */
    }
    throw new Error(detail);
  }
  const j = (await r.json()) as { token: string; expires_at: string };
  try {
    sessionStorage.setItem(LOCAL_KEY, JSON.stringify({ token: j.token, email, expiresAt: new Date(j.expires_at).getTime() }));
  } catch {
    throw new Error("il browser non permette di salvare la sessione (sessionStorage)");
  }
  window.location.reload();
}

export async function logout(): Promise<void> {
  if (config?.mode === "dev") {
    try {
      localStorage.removeItem(DEV_USER_KEY);
    } catch {
      /* ignora */
    }
    window.location.reload();
    return;
  }
  const local = readLocal();
  if (local) {
    await fetch("/api/v1/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${local.token}` } }).catch(() => {});
    clearLocalSession();
    window.location.reload();
    return;
  }
  await msal?.logoutRedirect();
}

export async function authHeaders(): Promise<Record<string, string>> {
  if (config?.mode === "dev") {
    const user = readDevUser();
    return user ? { "X-Dev-User": user } : {};
  }
  const local = readLocal();
  if (local) return { Authorization: `Bearer ${local.token}` };
  if (!msal || config?.mode !== "oidc") return {};
  const account = msal.getActiveAccount();
  if (!account) return {};
  try {
    const t = await msal.acquireTokenSilent({ scopes: config.scopes, account });
    return { Authorization: `Bearer ${t.accessToken}` };
  } catch (e) {
    if (e instanceof InteractionRequiredAuthError) {
      await msal.acquireTokenRedirect({ scopes: config.scopes, account });
    }
    throw e;
  }
}
