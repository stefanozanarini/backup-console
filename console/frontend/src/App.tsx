import { useCallback, useEffect, useMemo, useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError, useApi } from "./api";
import { initAuth, logout, type Session } from "./auth";
import { Ctx, makeCanWrite, type ConsoleCtx } from "./context";
import { ElevationBadge, ElevationProvider } from "./elevation";
import Approvals from "./pages/Approvals";
import Backup from "./pages/Backup";
import Licenses from "./pages/Licenses";
import DeviceDetailPage from "./pages/DeviceDetail";
import Devices from "./pages/Devices";
import Login from "./pages/Login";
import Problems from "./pages/Problems";
import Tenants from "./pages/Tenants";
import Upgrades from "./pages/Upgrades";
import type { Device, Me, Tenant, TenantSummary } from "./types";

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [fatal, setFatal] = useState<string | null>(null);
  const [denied, setDenied] = useState<string | null>(null);

  useEffect(() => {
    initAuth()
      .then(async (s) => {
        setSession(s);
        if (!s.method) return;
        try {
          const [m, t] = await Promise.all([api<Me>("/me"), api<Tenant[]>("/tenants")]);
          setMe(m);
          setTenants(t);
        } catch (e) {
          if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
            setDenied(`${s.email}: ${e.message}`);
          } else {
            setFatal((e as Error).message);
          }
        }
      })
      .catch((e: Error) => setFatal(e.message));
  }, []);

  const reloadTenants = useCallback(async () => setTenants(await api<Tenant[]>("/tenants")), []);
  const reloadMe = useCallback(async () => setMe(await api<Me>("/me")), []);

  const ctx = useMemo<ConsoleCtx | null>(() => {
    if (!session || !me) return null;
    const names = new Map(tenants.map((t) => [t.id, t.name]));
    return {
      session,
      me,
      tenants,
      tenantName: (id) => names.get(id) ?? "—",
      reloadTenants,
      reloadMe,
      isSuperadmin: me.roles.some((r) => r.role === "superadmin" && r.tenant_id === null),
      canWrite: makeCanWrite(me),
    };
  }, [session, me, tenants, reloadTenants, reloadMe]);

  if (fatal) return <div className="login"><div className="card"><div className="alert">{fatal}</div></div></div>;
  if (!session) return null;
  if (!session.method || denied) return <Login session={session} denied={denied} />;
  if (!ctx) return null;

  return (
    <Ctx.Provider value={ctx}>
      <ElevationProvider>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <div className="app">
          <Sidebar />
          <main className="main">
            <Routes>
              <Route path="/" element={<Navigate to="/problemi" replace />} />
              <Route path="/problemi" element={<Problems />} />
              <Route path="/dispositivi" element={<Devices />} />
              <Route path="/dispositivi/:id" element={<DeviceDetailPage />} />
              <Route path="/backup" element={<Backup />} />
              <Route path="/licenze" element={<Licenses />} />
              <Route path="/approvazioni" element={<Approvals />} />
              <Route path="/aggiornamenti" element={<Upgrades />} />
              <Route path="/tenant" element={<Tenants />} />
              <Route path="*" element={<Navigate to="/problemi" replace />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
      </ElevationProvider>
    </Ctx.Provider>
  );
}

function Sidebar() {
  const { data: summary } = useApi<TenantSummary[]>("/problems/summary", 30000);
  const { data: pending } = useApi<Device[]>("/devices?agent_status=pending", 30000);
  const critical = summary?.reduce((n, s) => n + s.critical, 0) ?? 0;
  const total = summary?.reduce((n, s) => n + s.critical + s.warning, 0) ?? 0;

  return (
    <nav className="sidebar" aria-label="Navigazione principale">
      <div className="brand">
        <img src="/logo.svg" alt="AGS Backup Console" />
      </div>
      <div className="nav-group">Monitoraggio</div>
      <NavLink to="/problemi" className="nav-link">
        <span className="nav-label"><Icon name="alert" />Problemi</span>
        {total > 0 && (
          <span className={`nav-count ${critical > 0 ? "critical" : ""}`} title={`${critical} critici`}>{total}</span>
        )}
      </NavLink>
      <NavLink to="/dispositivi" className="nav-link"><span className="nav-label"><Icon name="server" />Dispositivi</span></NavLink>
      <NavLink to="/backup" className="nav-link"><span className="nav-label"><Icon name="backup" />Backup</span></NavLink>
      <NavLink to="/licenze" className="nav-link"><span className="nav-label"><Icon name="key" />Licenze Veeam</span></NavLink>
      <div className="nav-group">Gestione</div>
      <NavLink to="/approvazioni" className="nav-link">
        <span className="nav-label"><Icon name="check" />Approvazioni</span>
        {pending && pending.length > 0 && <span className="nav-count">{pending.length}</span>}
      </NavLink>
      <NavLink to="/tenant" className="nav-link"><span className="nav-label"><Icon name="building" />Tenant e siti</span></NavLink>
      <NavLink to="/aggiornamenti" className="nav-link"><span className="nav-label"><Icon name="upgrade" />Aggiornamenti agente</span></NavLink>
      <SidebarFoot />
    </nav>
  );
}

// Icone della navigazione (tratto 1.8, 24×24, colore del testo).
const ICONS: Record<string, string> = {
  alert: "M12 3 2.5 20h19L12 3Zm0 6v5m0 3.2v.3",
  server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  backup: "M4 12a8 8 0 0 1 13.7-5.6L20 9M20 4v5h-5M20 12a8 8 0 0 1-13.7 5.6L4 15M4 20v-5h5",
  key: "M15 7a4 4 0 1 1-3.9 4.9L4 19v2h3v-2h2v-2h2l1.1-1.1A4 4 0 0 1 15 7Zm1.5 2.5h.01",
  check: "M20 6 9 17l-5-5",
  building: "M4 21V5l8-2v18M12 8h8v13M8 8v.01M8 12v.01M8 16v.01M16 12v.01M16 16v.01M2 21h20",
  upgrade: "M12 3v12m0-12-4.5 4.5M12 3l4.5 4.5M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4",
};

function Icon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"
      fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={ICONS[name]} />
    </svg>
  );
}

function SidebarFoot() {
  return (
    <Ctx.Consumer>
      {(c) => (
        <div className="sidebar-foot">
          <ElevationBadge />
          <span className="email" title={c?.me.email}>{c?.me.email}</span>
          <button className="btn small" onClick={() => void logout()}>Esci</button>
        </div>
      )}
    </Ctx.Consumer>
  );
}
