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
  totp_enabled: boolean;
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
  enrollment_note: string | null;
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
  site_name?: string | null;
  supremo_id?: string | null;
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

export interface VeeamRepository {
  name: string;
  type?: string | null;
  total_bytes?: number | null;
  free_bytes?: number | null;
}

export interface VeeamStatus {
  product?: "vbr" | "vaw";
  version?: string | null;
  repositories?: VeeamRepository[];
  license?: { edition?: string | null; status?: string | null; expiration?: string | null } | null;
  errors?: string[];
  reported_at?: string;
}

export interface DeviceDetail extends Device {
  inventory: Inventory | null;
  inventory_at: string | null;
  veeam: VeeamStatus;
}

export type VeeamResult = "Success" | "Warning" | "Failed" | "None" | string;

export interface VeeamSession {
  start: string | null;
  end: string | null;
  result: string | null;
  state?: string | null;
  retry?: boolean;
  mode?: "full" | "incremental" | null;
  transferred_bytes?: number | null;
  backup_bytes?: number | null;
  reason?: string | null;
}

export interface VeeamJob {
  id: string;
  tenant_id: string;
  device_id: string;
  product: "vbr" | "vaw";
  job_key: string;
  name: string;
  job_type: string | null;
  enabled: boolean;
  last_result: VeeamResult | null;
  last_state: string | null;
  last_start: string | null;
  last_end: string | null;
  last_success: string | null;
  transferred_bytes: number | null;
  next_run: string | null;
  updated_at: string;
  details?: {
    running?: boolean; running_since?: string | null; progress_pct?: number | null; retries_24h?: number;
    history?: VeeamSession[];
  };
  device_name: string;
  tenant_name: string;
  site_name: string;
}

export type CommandStatus = "queued" | "dispatched" | "running" | "succeeded" | "failed" | "timeout" | "expired";

export interface Command {
  id: string;
  tenant_id: string;
  device_id: string;
  type: "exec" | "upgrade" | "veeam_scan" | "veeam_cleanup" | "veeam_job_start";
  payload: {
    command?: string; shell?: string; version?: string; from?: string;
    older_than_days?: number; backup_ids?: string[]; total_bytes?: number;
    job_name?: string; mode?: string; product?: string; job_key?: string;
  };
  status: CommandStatus;
  timeout_s: number;
  reason: string | null;
  created_by: string | null;
  created_at: string;
  dispatched_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  error: string | null;
  output_truncated: boolean;
}

export interface CommandDetail extends Command {
  output: string;
  result: Record<string, unknown> | null;
}

export interface VeeamBackupItem {
  id: string;
  name: string;
  job: string | null;
  state: "active" | "disabled" | "orphan" | "unknown";
  repository: string | null;
  restore_points: number;
  size_bytes: number;
  first_point: string | null;
  last_point: string | null;
  candidate: boolean;
  beyond_retention?: {
    retention?: { type: "points" | "days"; value: number } | null;
    points?: number;
    size_bytes?: number;
    chains?: { first_point: string | null; last_point: string | null; points: number; size_bytes: number }[];
    note?: string | null;
  } | null;
}

export interface VeeamScanResult {
  older_than_days: number;
  scanned_at?: string;
  items: VeeamBackupItem[];
  repositories: VeeamRepository[];
  errors: string[];
}

export interface VeeamCleanupResult {
  results: { id: string; name?: string; status: "deleted" | "skipped" | "missing" | "error"; message?: string; size_bytes?: number }[];
  repositories: VeeamRepository[];
  errors: string[];
}

export interface Release {
  version: string;
  notes: string | null;
  created_at: string;
  assets: { platform: string; sha256: string; size: number }[];
  agents: number;
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
  id: string;
  token: string;
  expires_at: string;
  max_uses: number;
}

export interface TokenInfo {
  id: string;
  site_id: string;
  note: string | null;
  uses: number;
  max_uses: number;
  expires_at: string;
  used_at: string | null;
  created_at: string;
}

export type AuthConfig =
  | { mode: "dev"; agents_url: string }
  | { mode: "oidc"; agents_url: string; client_id: string; authority: string; scopes: string[]; local_login?: boolean };
