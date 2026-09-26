// Specchio degli schemi in console/backend/app/schemas/ui.py

export type Role = "superadmin" | "operator" | "tenant_admin" | "tenant_viewer";

export interface RoleOut {
  role: Role;
  tenant_id: string | null;
}

export interface Me {
  id: string;
  email: string;
  display_name: string | null;
  roles: RoleOut[];
}

export interface Tenant {
  id: string;
  name: string;
  slug: string;
}

export interface Site {
  id: string;
  tenant_id: string;
  name: string;
}

export type AgentStatus = "pending" | "approved" | "revoked";

export interface Agent {
  id: string;
  status: AgentStatus;
  version: string | null;
  allow_shell: boolean;
  last_seen_at: string | null;
  last_ip: string | null;
  cert_not_after: string;
  enrolled_at: string;
  approved_at: string | null;
}

export type DeviceKind = "nas" | "server" | "pc";

export interface Device {
  id: string;
  tenant_id: string;
  site_id: string;
  kind: DeviceKind;
  name: string;
  hostname: string;
  platform: string;
  arch: string;
  roles: string[];
  model: string | null;
  serial: string | null;
  os_version: string | null;
  last_seen_at: string | null;
  agent: Agent | null;
}

export interface Volume {
  mount: string;
  device?: string;
  fs_type?: string;
  total_bytes: number;
  free_bytes: number;
  used_pct: number;
}

export interface QnapDisk {
  slot: string;
  model?: string;
  smart?: string;
  temp_c?: number | null;
}

export interface RaidArray {
  name: string;
  healthy: boolean;
  detail?: string;
}

export interface Inventory {
  hostname?: string;
  model?: string;
  serial?: string;
  os_version?: string;
  data: {
    system?: { os?: string; arch?: string; volumes?: Volume[] };
    qnap?: { firmware?: string; build?: string; disks?: QnapDisk[]; arrays?: RaidArray[] };
    [collector: string]: unknown;
  };
}

export interface DeviceDetail extends Device {
  inventory: Inventory | null;
  inventory_at: string | null;
}

export type Severity = "warning" | "critical";
export type ProblemState = "open" | "acknowledged" | "resolved";

export interface Problem {
  id: string;
  tenant_id: string;
  device_id: string;
  check: string;
  severity: Severity;
  state: ProblemState;
  title: string;
  details: Record<string, unknown>;
  first_seen: string;
  last_seen: string;
  resolved_at: string | null;
  acked_at: string | null;
  tenant_name: string;
  device_name: string;
}

export interface TenantSummary {
  tenant_id: string;
  tenant_name: string;
  warning: number;
  critical: number;
}

export interface MetricSeries {
  labels: Record<string, string>;
  points: [string, number][];
}

export interface EnrollmentToken {
  token: string;
  expires_at: string;
}

export type AuthConfig =
  | { mode: "dev"; agents_url: string }
  | { mode: "oidc"; agents_url: string; client_id: string; authority: string; scopes: string[] };
