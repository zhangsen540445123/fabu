export interface JobSpec {
  publication_profile_id?: string | null;
  source_url: string;
  title: string;
  preset_version_id: string;
  target_ids: string[];
  publish_mode: string;
  scheduled_at_utc?: string | null;
}
export interface Job {
  output_manifest?: unknown;
  id: string;
  revision: number;
  content_revision: number;
  state: string;
  pause_reason: string | null;
  content_frozen: boolean;
  spec: JobSpec;
}
export interface Settings {
  data_dir: string;
  state_dir: string;
  tools_dir: string;
  node_path?: string | null;
  bind_host: string;
  test_mode: "local" | "simulation";
  default_preset_version_id: string;
  credentials: { configured: boolean };
  environment: {
    python_version: string;
    platform: string;
    paths: Record<
      string,
      { exists: boolean; free_bytes: number | null; writable: boolean | null }
    >;
  };
}
export interface Capabilities {
  upload: string;
  immediate: string;
  native_schedule: string;
  quota: string;
}
let csrf = "";
let initialization: Promise<void> | null = null;
export class ApiError extends Error {
  constructor(
    message: string,
    public issues: ValidationIssue[] = [],
  ) {
    super(message);
  }
}
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (init.method && !["GET", "HEAD"].includes(init.method))
    headers.set("X-CSRF-Token", csrf);
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    if (response.status === 401)
      throw new Error("会话未连接，请通过本机启动入口重新打开。");
    const body = await response.json().catch(() => null);
    const issues: ValidationIssue[] = body?.detail?.issues ?? [];
    const message =
      typeof body?.detail === "string"
        ? body.detail
        : `操作未完成（${response.status}），请检查配置后重试。`;
    throw new ApiError(message, issues);
  }
  return response.json() as Promise<T>;
}
export function initializeSession(): Promise<void> {
  if (initialization) return initialization;
  initialization = (async () => {
    const params = new URLSearchParams(location.hash.slice(1));
    const token = params.get("bootstrap");
    if (token) {
      history.replaceState(null, "", location.pathname);
      const response = await request<{ csrf_token: string }>("/api/session", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      csrf = response.csrf_token;
    } else
      csrf = (await request<{ csrf_token: string }>("/api/session")).csrf_token;
  })().catch((error) => {
    initialization = null;
    throw error;
  });
  return initialization;
}
export const getJobs = () => request<Job[]>("/api/jobs");
export const getSettings = () => request<Settings>("/api/settings");
export const getCapabilities = () => request<Capabilities>("/api/capabilities");
export const createJob = (spec: JobSpec) =>
  request<Job>("/api/jobs", { method: "POST", body: JSON.stringify(spec) });
export const cancelJob = (job: Job) =>
  request<Job>(`/api/jobs/${job.id}/cancel`, {
    method: "POST",
    body: JSON.stringify({ expected_revision: job.revision }),
  });
export const saveSettings = (
  patch: Partial<
    Pick<Settings, "data_dir" | "tools_dir" | "node_path" | "test_mode">
  >,
) =>
  request<Settings>("/api/settings", {
    method: "PUT",
    body: JSON.stringify(patch),
  });

export interface StepSpec {
  id: string;
  kind: string;
  enabled: boolean;
  scope: "main" | "full";
  params: Record<string, unknown>;
  asset_ids: string[];
}
export interface PresetDocument {
  name: string;
  schema_version: number;
  steps: StepSpec[];
  output: Record<string, unknown>;
}
export interface PresetVersion extends PresetDocument {
  id: string;
  preset_id: string;
  revision: number;
}
export interface PresetSummary {
  version: PresetVersion;
  approved: boolean;
}
export interface ValidationIssue {
  step_id: string;
  code: string;
  message: string;
}
export interface FieldSchema {
  type?: string;
  default?: unknown;
  enum?: string[];
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  anyOf?: FieldSchema[];
}
export interface CatalogStep {
  kind: string;
  label: string;
  scopes: ("main" | "full")[];
  multiple: boolean;
  parameters: { properties: Record<string, FieldSchema> };
}
export interface Asset {
  id: string;
  sha256: string;
  local_path: string;
  media_type: "image" | "video" | "font" | "subtitle";
  size_bytes: number;
  display_name: string;
}
export interface MediaStatus {
  running: boolean;
  error_code: string | null;
  job_id: string | null;
  operation_id?: string | null;
  kind?: string | null;
  percent?: number | null;
  publishing_enabled: boolean;
}
export interface MediaOperation {
  id: string;
  job_id: string;
  state: string;
  error_code: string | null;
  result: { preview_id: string } | null;
}
export interface Preview {
  id: string;
  approved: boolean;
  clips: {
    index: number;
    start_ms: number;
    end_ms: number;
    proxy_label: string;
    proxy_url: string;
    official_url: string;
  }[];
}
export interface JobPlan {
  digest: string;
  output_size: { width: number; height: number };
  duration_ms: number;
  preset: PresetVersion;
  source_manifest: { thumbnail: { id: string } };
}
export const getPresets = () => request<PresetSummary[]>("/api/presets");
export const getCatalog = () => request<CatalogStep[]>("/api/presets/catalog");
export const getAssets = () => request<Asset[]>("/api/assets");
export const savePreset = (id: string | null, doc: PresetDocument) =>
  request<PresetVersion>(id ? `/api/presets/${id}/versions` : "/api/presets", {
    method: "POST",
    body: JSON.stringify(doc),
  });
export const validatePreset = (doc: PresetDocument) =>
  request<{ valid: boolean; issues: ValidationIssue[] }>(
    "/api/presets/validate",
    { method: "POST", body: JSON.stringify(doc) },
  );
export const importAsset = (path: string, media_type: Asset["media_type"]) =>
  request<Asset>("/api/assets", {
    method: "POST",
    body: JSON.stringify({ path, media_type }),
  });
export const getMediaStatus = () => request<MediaStatus>("/api/media/status");
export const getJobPlan = (id: string) =>
  request<JobPlan>(`/api/jobs/${id}/plan`);
export const getJobPreviews = (id: string) =>
  request<MediaOperation[]>(`/api/jobs/${id}/previews`);
export const createPreview = (job: Job, windows?: [number, number][]) =>
  request<MediaOperation>("/api/previews", {
    method: "POST",
    body: JSON.stringify({
      job_id: job.id,
      expected_revision: job.revision,
      windows,
    }),
  });
export const getOperation = (id: string) =>
  request<MediaOperation>(`/api/previews/operations/${id}`);
export const getPreview = (id: string) =>
  request<Preview>(`/api/previews/${id}`);
export const approvePreview = (id: string) =>
  request<{ preview_id: string }>(`/api/previews/${id}/approve`, {
    method: "POST",
    body: "{}",
  });
export const resumeJob = (job: Job) =>
  request<Job>(`/api/jobs/${job.id}/resume`, {
    method: "POST",
    body: JSON.stringify({ expected_revision: job.revision }),
  });

export const reviseJob = (job: Job, preset_version_id: string) =>
  request<Job>(`/api/jobs/${job.id}/revise`, {
    method: "POST",
    body: JSON.stringify({
      expected_revision: job.revision,
      spec: { ...job.spec, preset_version_id },
    }),
  });
export interface PublishAccount {
  account_id: string;
  platform: string;
  nickname: string;
  active: boolean;
  category_names: string[];
}
export interface AccountCatalogue {
  scope: "simulation" | "live";
  live_enabled: boolean;
  accounts: PublishAccount[];
  topic_groups: { name: string; tags: string[] }[];
}
export interface PublicationDraft {
  expected_revision: number;
  scope: "simulation" | "live";
  mode: "immediate" | "native_schedule";
  scheduled_at_utc?: string;
  topic_override: string | null;
  accounts: {
    account_id: string;
    declaration: {
      origin: "licensed_reuse" | "self_created";
      statement: string;
      bilibili_type: "self_produced" | "repost" | null;
      confirmed: boolean;
    };
  }[];
}
export interface PublishTarget {
  account_id: string;
  platform: string;
  nickname: string;
  state: string;
  can_retry: boolean;
  category: string[];
}
export interface PublishRun {
  resource_blocked?: boolean;
  source_conflict?: boolean;
  job_id?: string;
  content_revision?: number;
  snapshot_digest?: string;
  scope: string;
  state: string;
  mode?: string;
  scheduled_at_utc?: string | null;
  reason?: string | null;
  targets: PublishTarget[];
  topic?: { group_name: string | null; tags: string[] };
  can_submit: boolean;
  can_reconcile: boolean;
  command?: { state: string; error_code: string | null } | null;
}
export const getAccounts = (scope: "simulation" | "live" = "simulation") =>
  request<AccountCatalogue>(`/api/accounts?scope=${scope}`);
export const configurePublication = (jobId: string, draft: PublicationDraft) =>
  request<Job>(`/api/jobs/${jobId}/publication`, {
    method: "PUT",
    body: JSON.stringify(draft),
  });
export const preflightPublication = (
  job: Job,
  scope: "simulation" | "live" = "simulation",
) =>
  request<PublishRun>("/api/publish/preflight", {
    method: "POST",
    body: JSON.stringify({
      job_id: job.id,
      expected_revision: job.revision,
      scope,
    }),
  });
export const getTargetStatus = (
  jobId: string,
  scope: "simulation" | "live" = "simulation",
) => request<PublishRun>(`/api/jobs/${jobId}/targets?scope=${scope}`);
export const submitPublication = (jobId: string, digest: string) =>
  request(`/api/jobs/${jobId}/submit`, {
    method: "POST",
    body: JSON.stringify({ snapshot_digest: digest, scope: "simulation" }),
  });
export const reconcilePublication = (jobId: string, digest: string) =>
  request(`/api/jobs/${jobId}/reconcile`, {
    method: "POST",
    body: JSON.stringify({ snapshot_digest: digest, scope: "simulation" }),
  });
export interface PublishingConnection {
  configured: boolean;
  package_root: string | null;
  live_enabled: boolean;
}
export const getPublishingConnection = () =>
  request<PublishingConnection>("/api/publishing/connection");
export const savePublishingConnection = (data: {
  package_root: string;
  api_key: string;
  local_client_id: string;
}) =>
  request<PublishingConnection>("/api/publishing/connection", {
    method: "PUT",
    body: JSON.stringify(data),
  });
export const refreshPublishingAccounts = () =>
  request<{ refreshed: number }>("/api/accounts/refresh", { method: "POST" });
export interface PublicationProfile {
  id: string;
  name: string;
  scope: "simulation" | "live";
  can_run: boolean;
  accounts: PublishAccount[];
  topic_override: string | null;
}
export const getPublicationProfiles = () =>
  request<PublicationProfile[]>("/api/publish/profiles");
export const savePublicationProfile = (
  job: Job,
  name: string,
  scope: "simulation" | "live",
) =>
  request<PublicationProfile>("/api/publish/profiles", {
    method: "POST",
    body: JSON.stringify({
      job_id: job.id,
      expected_revision: job.revision,
      name,
      scope,
    }),
  });
export const createWorkflowJob = (data: {
  source_url: string;
  title: string;
  preset_version_id: string;
  profile_id: string;
  mode: "immediate" | "native_schedule";
  scheduled_at_utc?: string;
}) =>
  request<Job>("/api/workflow/jobs", {
    method: "POST",
    body: JSON.stringify(data),
  });
