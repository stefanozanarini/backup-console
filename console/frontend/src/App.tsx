import { useCallback, useEffect, useMemo, useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError, useApi } from "./api";
import { initAuth, logout, type Session } from "./auth";
import { Ctx, makeCanWrite, type ConsoleCtx } from "./context";
import { ElevationBadge, ElevationProvider } from "./elevation";
import Approvals from "./pages/Approvals";
import Backup from "./pages/Backup";
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
        <img src="/favicon.svg" alt="" />
        Backup Console
      </div>
      <NavLink to="/problemi" className="nav-link">
        Problemi
        {total > 0 && (
          <span className={`nav-count ${critical > 0 ? "critical" : ""}`} title={`${critical} critici`}>{total}</span>
        )}
      </NavLink>
      <NavLink to="/dispositivi" className="nav-link">Dispositivi</NavLink>
      <NavLink to="/backup" className="nav-link">Backup Veeam</NavLink>
      <NavLink to="/approvazioni" className="nav-link">
        Approvazioni
        {pending && pending.length > 0 && <span className="nav-count">{pending.length}</span>}
      </NavLink>
      <NavLink to="/tenant" className="nav-link">Tenant e siti</NavLink>
      <NavLink to="/aggiornamenti" className="nav-link">Aggiornamenti agente</NavLink>
      <SidebarFoot />
    </nav>
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
