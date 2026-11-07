import { useState, useEffect } from "react";
import {
  createJob,
  createWorkflowJob,
  getPublicationProfiles,
  getPublicationDefaults,
  type PublicationProfile,
  type PresetSummary,
} from "../api";
import { usePublishingReadiness } from "../publishing-readiness";
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
  const { readiness } = usePublishingReadiness();
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
          selected ? [selected, ...v.filter((p) => p.id !== selected.id)] : v,
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
  const publicationProfile = profiles.find((p) => p.id === profile);
  const liveProfile = publicationProfile?.scope === "live";
  const automaticLive = liveProfile && publicationProfile?.execution === "live";
  const liveReady =
    readiness.enabled &&
    readiness.status === "ready" &&
    !readiness.blockers.length;
  const scheduleBlocked =
    liveProfile &&
    mode === "native_schedule" &&
    !readiness.native_schedule_enabled;
  const blocked =
    !!profile &&
    (!publicationProfile?.can_run ||
      (automaticLive && !liveReady) ||
      scheduleBlocked);
  const selected = presets.find((p) => p.version.id === version);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (loading || busy || blocked || !version) return;
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
          <h2 id="new-title">新建自动工作流</h2>
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
                  {p.name} ·{" "}
                  {p.scope === "simulation"
                    ? "仿真"
                    : p.execution === "live"
                      ? "真实自动发布"
                      : "真实预检（尚未授权）"}
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
                    {liveProfile
                      ? automaticLive
                        ? "自动立即真实发布"
                        : "立即发布预检（不投稿）"
                      : "自动立即发布（仿真）"}
                  </option>
                  <option value="native_schedule">
                    {liveProfile
                      ? "平台原生定时（尚未验收）"
                      : "自动平台定时（仿真）"}
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
                  ? automaticLive
                    ? "点击创建并自动真实发布后，将使用已保存的账号、话题和来源声明，自动下载校验、处理校验、上传、逐平台提交并跟踪。仅选择真实模式不代表已经发送；只影响此新任务，不恢复或重发历史任务。三平台分别提交，最多消耗3次本地预算；不是官方剩余额度。平台成片质量未验收，B站2秒故障风险仍需核对，provider成功不代表平台完整成片通过。"
                    : "此流程尚未授权真实发布：目前仅预检，不上传、不投稿，不能视为自动真实发布。成片验证完成后带入账号与来源声明进行预检。"
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
          {automaticLive && !liveReady && (
            <div className="error" role="alert">
              真实发布已阻断。
              {readiness.blockers.length
                ? readiness.blockers.join("；")
                : "本机尚未明确授权或当前配置未就绪。"}
            </div>
          )}
          {profile && !publicationProfile?.can_run && (
            <p role="alert">
              所选发布流程当前不可运行；请核对账号和来源声明，不会自动替换为本地任务。
            </p>
          )}
          {scheduleBlocked && (
            <p role="alert">
              平台原生定时尚未验收，不能执行自动真实定时；不会回退为立即发布。
            </p>
          )}
          <p className="muted">
            使用原始YouTube封面；
            {automaticLive
              ? "创建后按上述真实流程执行。"
              : "当前流程不会自动上传或真实投稿。"}
          </p>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button
            className="primary wide"
            disabled={loading || busy || !version || blocked}
          >
            {busy
              ? "保存中…"
              : profile
                ? liveProfile
                  ? automaticLive
                    ? "创建并自动真实发布"
                    : "创建自动流程（真实预检）"
                  : "创建自动流程（仿真）"
                : "保存本地任务"}
          </button>
        </form>
      </section>
    </div>
  );
}
