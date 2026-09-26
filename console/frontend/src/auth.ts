// Login: Entra ID via MSAL (produzione) oppure utente dichiarato a mano (BC_AUTH_MODE=dev).
import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
} from "@azure/msal-browser";
import type { AuthConfig } from "./types";

const DEV_USER_KEY = "bc.devUser";

let config: AuthConfig | null = null;
let msal: PublicClientApplication | null = null;

export interface Session {
  mode: AuthConfig["mode"];
  email: string | null;
  agentsUrl: string;
}

function readDevUser(): string | null {
  try {
    return localStorage.getItem(DEV_USER_KEY);
  } catch {
    return null;
  }
}

export async function initAuth(): Promise<Session> {
  const r = await fetch("/api/v1/auth-config");
  if (!r.ok) throw new Error(`configurazione non disponibile (HTTP ${r.status})`);
  config = (await r.json()) as AuthConfig;

  if (config.mode === "dev") {
    return { mode: "dev", email: readDevUser(), agentsUrl: config.agents_url };
  }

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
  return { mode: "oidc", email: account?.username ?? null, agentsUrl: config.agents_url };
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
  await msal?.logoutRedirect();
}

export async function authHeaders(): Promise<Record<string, string>> {
  if (config?.mode === "dev") {
    const user = readDevUser();
    return user ? { "X-Dev-User": user } : {};
  }
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
