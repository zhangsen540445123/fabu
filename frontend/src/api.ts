export interface JobSpec {
  source_url: string;
  title: string;
  preset_version_id: string;
  target_ids: string[];
  publish_mode: string;
  scheduled_at_utc?: string | null;
}
export interface Job {
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
    throw new Error(`操作未完成（${response.status}），请检查配置后重试。`);
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
  patch: Partial<Pick<Settings, "data_dir" | "tools_dir" | "test_mode">>,
) =>
  request<Settings>("/api/settings", {
    method: "PUT",
    body: JSON.stringify(patch),
  });
