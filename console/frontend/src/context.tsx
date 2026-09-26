import { createContext, useContext } from "react";
import type { Session } from "./auth";
import type { Me, Tenant } from "./types";

export interface ConsoleCtx {
  session: Session;
  me: Me;
  tenants: Tenant[];
  tenantName: (id: string) => string;
  reloadTenants: () => Promise<void>;
  reloadMe: () => Promise<void>;
  isSuperadmin: boolean;
  canWrite: (tenantId: string) => boolean;
}

export const Ctx = createContext<ConsoleCtx | null>(null);

export function useConsole(): ConsoleCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("ConsoleCtx mancante");
  return c;
}

const WRITE_ROLES = new Set(["superadmin", "operator", "tenant_admin"]);

// Stessa logica di CurrentUser.can_write nel backend (serve solo a nascondere i pulsanti:
// l'autorizzazione vera la fa sempre l'API).
export function makeCanWrite(me: Me) {
  return (tenantId: string) =>
    me.roles.some(
      (r) => WRITE_ROLES.has(r.role) && (r.tenant_id === tenantId || (r.tenant_id === null && r.role !== "tenant_admin")),
    );
}
