import { useState, useEffect } from "react";
import {
  createJob,
  createWorkflowJob,
  getPublicationProfiles,
  getPublicationDefaults,
  type PublicationProfile,
  type PresetSummary,
} from "../api";
import { beijingToUtc } from "../publishing-time";
export default function NewJobPage({
  presets,
  defaultPreset,
  onClose,
  onSaved,
}: {
  presets: PresetSummary[];
  defaultPreset: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [url, setUrl] = useState(""),
    [title, setTitle] = useState(""),
    [version, setVersion] = useState(
      presets.some((p) => p.version.id === defaultPreset)
        ? defaultPreset
        : (presets[0]?.version.id ?? ""),
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [profiles, setProfiles] = useState<PublicationProfile[]>([]),
    [profile, setProfile] = useState(""),
    [mode, setMode] = useState<"immediate" | "native_schedule">("immediate"),
    [wall, setWall] = useState("");
  useEffect(() => {
    let alive = true;
    Promise.all([getPublicationProfiles(), getPublicationDefaults()])
      .then(([v, defaults]) => {
        if (!alive) return;
        const selected = defaults.profile;
        setProfiles(
          selected && !v.some((p) => p.id === selected.id)
            ? [selected, ...v]
            : v,
        );
        if (selected) setProfile(selected.id);
        setLoading(false);
      })
      .catch((e) => {
        if (alive) setError(`默认发布配置加载失败，请关闭后重试：${String(e)}`);
      });
    return () => {
      alive = false;
    };
  }, []);
  const liveProfile = profiles.find((p) => p.id === profile)?.scope === "live";
  const selected = presets.find((p) => p.version.id === version);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (loading) return;
    setBusy(true);
    setError("");
    try {
      if (profile)
        await createWorkflowJob({
          source_url: url,
          title,
          preset_version_id: version,
          profile_id: profile,
          mode,
          ...(mode === "native_schedule"
            ? { scheduled_at_utc: beijingToUtc(wall) }
            : {}),
        });
      else
        await createJob({
          source_url: url,
          title,
          preset_version_id: version,
          target_ids: [],
          publish_mode: "local",
        });
      await onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-title"
      >
        <div className="panel-title">
          <h2 id="new-title">新建本地任务</h2>
          <button className="quiet" aria-label="关闭新建任务" onClick={onClose}>
            ×
          </button>
        </div>
        <form onSubmit={submit}>
          <label>
            YouTube链接
            <input
              required
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://youtu.be/…"
            />
          </label>
          <label>
            发布标题
            <input
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如：剃须仙人的日常"
            />
          </label>
          <label>
            处理预设
            <select
              required
              value={version}
              onChange={(e) => setVersion(e.target.value)}
            >
              {presets.map((p) => (
                <option key={p.version.id} value={p.version.id}>
                  {p.version.name} · v{p.version.revision}
                </option>
              ))}
            </select>
          </label>
          <div className="preset-note">
            <strong>下载完成并通过完整性校验后，自动按所选预设处理。</strong>
            <p>
              基础输出 HEVC · CRF{String(selected?.version.output.crf ?? 20)} ·
              30fps。标题只用于发布内容，不改变处理预设。样片检查可选，不影响本地成片生成。
            </p>
          </div>
          <label>
            发布流程模板
            <select
              value={profile}
              disabled={loading || busy}
              onChange={(e) => setProfile(e.target.value)}
            >
              <option value="">不使用发布模板 · 仅本地处理</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id} disabled={!p.can_run}>
                  {p.name} · {p.scope === "simulation" ? "仿真" : "真实预检"}
                </option>
              ))}
            </select>
          </label>
          {profile ? (
            <>
              <label>
                执行模式
                <select
                  value={mode}
                  onChange={(e) => setMode(e.target.value as typeof mode)}
                >
                  <option value="immediate">
                    自动立即发布{liveProfile ? "（真实预检）" : "（仿真）"}
                  </option>
                  <option value="native_schedule">
                    自动平台定时{liveProfile ? "（真实预检）" : "（仿真）"}
                  </option>
                </select>
              </label>
              {mode === "native_schedule" && (
                <label>
                  流程预约北京时间
                  <input
                    type="datetime-local"
                    value={wall}
                    onChange={(e) => setWall(e.target.value)}
                    required
                  />
                </label>
              )}
              <p className="preset-note">
                {liveProfile
                  ? "成片验证完成后自动进入真实账号发布预检，带入默认账号与来源声明。目前仅预检，不上传、不投稿；真实发布仍须通过免费额度、平台能力与媒体批准检查。"
                  : "成片验证完成后自动进入发布仿真流程；发布仿真仍需通过样片确认等发布专用预检。使用此模板的账号与来源声明，无需再点击发布。仿真不上传、不扣次；真实发布权限与安全限制不变。"}
              </p>
            </>
          ) : (
            <label>
              执行模式
              <select value="local" disabled>
                <option value="local">仅本地处理</option>
              </select>
            </label>
          )}
          <p className="muted">使用原始YouTube封面；不自动上传或投稿。</p>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button
            className="primary wide"
            disabled={loading || busy || !version}
          >
            {busy
              ? "保存中…"
              : profile
                ? liveProfile
                  ? "创建自动流程（真实预检）"
                  : "创建自动流程（仿真）"
                : "保存本地任务"}
          </button>
        </form>
      </section>
    </div>
  );
}
