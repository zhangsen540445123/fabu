import { useEffect, useState } from "react";
import {
  configurePublication,
  getPublishingConnection,
  getAccounts,
  getPublicationForm,
  savePublicationDefaults,
  type PublicationForm,
  getWorkflowHandoff,
  resumeWorkflowHandoff,
  type WorkflowHandoff,
  getTargetStatus,
  preflightPublication,
  reconcilePublication,
  submitPublication,
  savePublicationProfile,
  type AccountCatalogue,
  type Job,
  type PublishRun,
  type PublicationDraft,
} from "../api";
import { providerReasonLabel } from "../publishingLabels";
import { usePublishingReadiness } from "../publishing-readiness";
import { beijingToUtc, formatBeijing } from "../publishing-time";
import TargetStatus, { platformNames } from "./TargetStatus";
export default function PublishForm({
  job,
  refresh,
}: {
  job: Job;
  refresh: () => Promise<void>;
}) {
  const { readiness } = usePublishingReadiness();
  const [liveEnabled, setLiveEnabled] = useState(false);
  const [handoff, setHandoff] = useState<WorkflowHandoff | null>(null);
  const [profileName, setProfileName] = useState("日常助眠"),
    [profileMessage, setProfileMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [scopeOverride, setScopeOverride] = useState<{
    jobId: string;
    scope: "simulation" | "live";
  } | null>(null);
  const requestedScope =
    scopeOverride?.jobId === job.id ? scopeOverride.scope : undefined;
  const [form, setForm] = useState<PublicationForm | null>(null);
  const [declarationChanged, setDeclarationChanged] = useState(false);
  const [defaultsMessage, setDefaultsMessage] = useState("");
  const [scope, setScope] = useState<"simulation" | "live">("simulation");
  const [catalog, setCatalog] = useState<AccountCatalogue | null>(null),
    [selected, setSelected] = useState<string[]>([]),
    [run, setRun] = useState<PublishRun | null>(null),
    [mode, setMode] = useState<"immediate" | "native_schedule">("immediate"),
    [wall, setWall] = useState(""),
    [topic, setTopic] = useState(""),
    [origin, setOrigin] = useState<"licensed_reuse" | "self_created" | "">(
      "licensed_reuse",
    ),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setInterval> | undefined;
    setLoading(true);
    setRun(null);
    setCatalog(null);
    setForm(null);
    setError("");
    setDefaultsMessage("");
    setConfirmed(false);
    async function load() {
      try {
        const f = await getPublicationForm(job.id, requestedScope);
        const c = await getAccounts(f.scope);
        if (!alive) return;
        setForm(f);
        setCatalog(c);
        setScope(f.scope);
        setSelected(
          f.source === "fallback"
            ? f.scope === "simulation"
              ? c.accounts.filter((a) => a.active).map((a) => a.account_id)
              : []
            : f.accounts.map((a) => a.account_id),
        );
        const origins = [
          ...new Set(f.accounts.map((a) => a.declaration.origin)),
        ];
        setOrigin(
          origins.length === 1
            ? origins[0]
            : origins.length
              ? ""
              : "licensed_reuse",
        );
        setConfirmed(
          f.accounts.length > 0 &&
            f.accounts.every((a) => a.declaration.confirmed),
        );
        setDeclarationChanged(false);
        setTopic(f.topic_override ?? "");
        setMode(f.mode);
        setWall(
          f.scheduled_at_utc
            ? new Date(new Date(f.scheduled_at_utc).getTime() + 8 * 3600_000)
                .toISOString()
                .slice(0, 16)
            : "",
        );
        setLoading(false);
        const sync = () =>
          getTargetStatus(job.id, f.scope)
            .then((r) => {
              if (alive) setRun(r);
            })
            .catch((e) => {
              if (alive) {
                setError(String(e));
                setRun((previous) =>
                  previous ? { ...previous, can_submit: false } : null,
                );
              }
            });
        void sync();
        timer = setInterval(() => void sync(), 1500);
      } catch (e) {
        if (alive) setError(`发布配置加载失败，请重新打开任务：${String(e)}`);
      }
    }
    void load();
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [job.id, job.content_revision, requestedScope]);
  useEffect(() => {
    let alive = true;
    setHandoff(null);
    if (!job.spec.publication_profile_id) return;
    const sync = async () => {
      try {
        const status = await getWorkflowHandoff(job.id);
        if (alive) setHandoff(status);
      } catch (e) {
        if (alive) {
          setHandoff(null);
          setError(String(e));
        }
      }
    };
    void sync();
    const timer = setInterval(() => void sync(), 1500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [job.id, job.revision, job.spec.publication_profile_id]);
  useEffect(() => {
    let alive = true;
    setLiveEnabled(false);
    if (scope !== "live") return;
    async function syncPermission() {
      try {
        const connection = await getPublishingConnection();
        if (alive) setLiveEnabled(connection?.live_enabled === true);
      } catch {
        if (alive) setLiveEnabled(false);
      }
    }
    void syncPermission();
    const timer = setInterval(() => void syncPermission(), 1500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [job.id, scope]);
  const trackingInBackground =
    job.pause_reason === "provider_tracking_pending" ||
    handoff?.error_code === "provider_tracking_pending";
  const liveReady =
    liveEnabled &&
    readiness.enabled &&
    readiness.status === "ready" &&
    !readiness.blockers.length;
  const scheduleBlocked =
    scope === "live" &&
    (run?.mode === "native_schedule" || mode === "native_schedule") &&
    !readiness.native_schedule_enabled;
  async function resumeHandoff() {
    if (
      busy ||
      trackingInBackground ||
      (scope === "live" && (!liveReady || scheduleBlocked))
    )
      return;
    setBusy(true);
    setError("");
    try {
      await resumeWorkflowHandoff(job);
      setHandoff(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "恢复自动衔接失败");
    } finally {
      setBusy(false);
    }
  }
  const hasAttempts = run?.targets.some((t) => t.state !== "PENDING") ?? false;
  const commandActive =
    !!run?.command && ["QUEUED", "RUNNING"].includes(run.command.state);
  const locked =
    loading ||
    job.content_frozen ||
    hasAttempts ||
    commandActive ||
    job.state !== "READY";
  function accountChoices(): PublicationDraft["accounts"] {
    if (loading || !confirmed || !selected.length)
      throw new Error("请选择账号并确认来源声明");
    const accounts = selected.map((id) =>
      catalog?.accounts.find((a) => a.account_id === id),
    );
    if (accounts.some((a) => !a?.active))
      throw new Error(
        "已保存的目标账号缺失或失效；请核对账号选择，不会自动减少目标",
      );
    return accounts.map((a) => {
      const saved = form?.accounts.find(
        (entry) => entry.account_id === a!.account_id,
      );
      if (saved && !declarationChanged) return saved;
      if (!origin) throw new Error("新增账号需要明确选择来源声明");
      return {
        account_id: a!.account_id,
        ...(saved?.description !== undefined
          ? { description: saved.description }
          : {}),
        declaration: {
          origin,
          confirmed,
          statement:
            a!.platform === "toutiao"
              ? origin === "self_created"
                ? "self_shot"
                : "offsite"
              : "none",
          bilibili_type:
            a!.platform === "bilibili"
              ? origin === "self_created"
                ? "self_produced"
                : "repost"
              : null,
        },
      };
    });
  }
  async function saveDefaults() {
    setBusy(true);
    setError("");
    setDefaultsMessage("");
    try {
      await savePublicationDefaults({
        scope,
        mode: "immediate",
        topic_override: topic || null,
        accounts: accountChoices(),
      });
      setDefaultsMessage(
        "已保存：仅影响之后新建的任务，默认立即发布；不会修改或发布当前任务。",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存默认配置失败");
    } finally {
      setBusy(false);
    }
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const command: PublicationDraft = {
        expected_revision: job.revision,
        scope,
        mode,
        topic_override: topic || null,
        accounts: accountChoices(),
      };
      if (mode === "native_schedule")
        command.scheduled_at_utc = beijingToUtc(wall);
      const updated = await configurePublication(job.id, command);
      await refresh();
      setRun(await preflightPublication(updated, scope));
    } catch (e) {
      setError(e instanceof Error ? e.message : "预检失败");
    } finally {
      setBusy(false);
    }
  }
  async function execute(action: "submit" | "reconcile") {
    if (!run?.snapshot_digest || busy || loading || commandActive) return;
    setBusy(true);
    setError("");
    try {
      if (action === "submit") {
        if (
          run.scope !== scope ||
          !run.can_submit ||
          (scope === "live" && (!liveReady || scheduleBlocked))
        )
          throw new Error("当前发布未就绪或尚未授权，请刷新并核对发布状态");
        await submitPublication(job.id, run.snapshot_digest, scope);
      } else {
        if (run.scope !== scope)
          throw new Error("发布环境已变更，请等待状态刷新");
        await reconcilePublication(job.id, run.snapshot_digest, scope);
      }
      setRun(await getTargetStatus(job.id, scope));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  async function saveProfile() {
    setBusy(true);
    setError("");
    try {
      await savePublicationProfile(job, profileName, scope);
      setProfileMessage("流程模板已保存，新建任务时可选择并自动衔接。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="publication-panel">
      <h3>发布配置与结果</h3>
      <label>
        发布环境
        <select
          value={scope}
          disabled={loading || busy || commandActive}
          onChange={(e) =>
            setScopeOverride({
              jobId: job.id,
              scope: e.target.value as typeof scope,
            })
          }
        >
          <option value="simulation">本地仿真（不会上传）</option>
          <option value="live">真实账号（授权后可上传、投稿与跟踪）</option>
        </select>
      </label>
      <div className="preset-note">
        <strong>
          {scope === "simulation"
            ? "仅本地仿真 · 不会真实上传或投稿"
            : liveReady
              ? "真实发布已授权并就绪"
              : "真实发布已阻断 · 配置与只读查询仍可用"}
        </strong>
        <p>
          仅选择真实模式不代表已经发送。保存配置与预检不会投稿；明确点击真实发布或创建已授权自动流程后才可能上传、提交。
        </p>
        <p>
          三平台分别提交，最多消耗3次，每日本地上限3次；这是保守本地预算，不是官方剩余额度。官方拒绝时暂停，不支付、不盲目重试。
        </p>
        <p>
          provider成功不等于平台已公开或平台完整成片通过；平台成片质量未验收，B站2秒故障风险仍需核对。本地成片生成不要求先确认样片。
        </p>
        <p>
          平台原生定时尚未验收，保留设置；不是本地延时上传，不会回退为立即发布。
        </p>
        {scope === "live" && !liveReady && (
          <ul>
            {readiness.blockers.map((b, i) => (
              <li className="break" key={i}>
                {b}
              </li>
            ))}
            {!liveEnabled && (
              <li>本机连接尚未确认live授权（live_publish_not_enabled）。</li>
            )}
          </ul>
        )}
      </div>
      {loading && <p role="status">正在读取已保存的发布配置…</p>}
      {scope === "live" && handoff?.state === "PAUSED" && (
        <div className="preset-note" role="status">
          <p>
            {trackingInBackground
              ? providerReasonLabel("provider_tracking_pending")
              : handoff.error_code === "live_publish_not_enabled"
                ? "成片已自动进入发布预检，但尚未获得真实投稿授权。请核对本机政策与流程配置。"
                : "自动真实流程已暂停，请先核对成片、账号、预约时间和各目标回执；暂停不代表所有目标均未提交。"}
          </p>
          {handoff.error_code && !trackingInBackground && (
            <p className="break">{providerReasonLabel(handoff.error_code)}</p>
          )}
          {handoff.can_resume && !trackingInBackground && (
            <button
              type="button"
              className="secondary"
              disabled={busy || commandActive || !liveReady || scheduleBlocked}
              onClick={() => void resumeHandoff()}
            >
              恢复自动真实流程（可能上传/投稿）
            </button>
          )}
        </div>
      )}
      {scope === "simulation" &&
        job.state === "PAUSED" &&
        handoff?.state === "PAUSED" && (
          <div className="preset-note">
            <p>
              自动衔接已暂停。恢复时会重新检查成片、预览批准、账号和预约时间，不会重新渲染，也不会重试结果不明的上传或投稿。
            </p>
            {handoff.can_resume ? (
              <button
                className="secondary"
                disabled={busy || commandActive}
                onClick={() => void resumeHandoff()}
              >
                恢复自动衔接（仿真）
              </button>
            ) : (
              <p>
                当前证据不允许恢复；请先核对已有发布记录及配置，不要通过重置重复投稿。
              </p>
            )}
          </div>
        )}
      {!hasAttempts && (
        <form onSubmit={(e) => void save(e)}>
          <fieldset disabled={busy || locked}>
            <legend>目标账号（稳定ID）</legend>
            {selected
              .filter(
                (id) => !catalog?.accounts.some((a) => a.account_id === id),
              )
              .map((id) => (
                <label className="checkbox-row" key={id}>
                  <input
                    type="checkbox"
                    checked
                    onChange={() =>
                      setSelected((v) => v.filter((item) => item !== id))
                    }
                  />
                  未找到已选账号 · {id}（请同步账号或明确取消选择）
                </label>
              ))}
            {catalog?.accounts.map((a) => (
              <label className="checkbox-row" key={a.account_id}>
                <input
                  type="checkbox"
                  checked={selected.includes(a.account_id)}
                  disabled={!a.active && !selected.includes(a.account_id)}
                  onChange={(e) =>
                    setSelected((v) =>
                      e.target.checked
                        ? [...v, a.account_id]
                        : v.filter((id) => id !== a.account_id),
                    )
                  }
                />
                <span>
                  {platformNames[a.platform]} · {a.nickname}
                  <small className="break">{a.account_id}</small>
                </span>
                {a.category_names.length > 0 && (
                  <span>{a.category_names.join(" / ")}</span>
                )}
              </label>
            ))}
          </fieldset>
          <fieldset disabled={busy || locked}>
            <legend>共享内容</legend>
            <p>
              视频与封面使用本任务已验证成片和原始YouTube封面。标题：
              {job.spec.title}
            </p>
            <label>
              话题组
              <select value={topic} onChange={(e) => setTopic(e.target.value)}>
                <option value="">根据标题自动选择</option>
                {catalog?.topic_groups.map((g) => (
                  <option key={g.name} value={g.name}>
                    {g.name} · {g.tags.join("、")}
                  </option>
                ))}
              </select>
            </label>
            <label>
              视频来源
              <select
                value={origin}
                onChange={(e) => {
                  setOrigin(e.target.value as typeof origin);
                  setConfirmed(false);
                  setDeclarationChanged(true);
                }}
              >
                {origin === "" && (
                  <option value="">保留各平台已保存的来源声明</option>
                )}
                <option value="licensed_reuse">取得授权的转载内容</option>
                <option value="self_created">本人创作的原创内容</option>
              </select>
            </label>
            {origin && (
              <p>
                授权转载不等于本人原创。B站将声明为
                {origin === "self_created" ? "原创" : "转载并附来源链接"}
                ，头条将声明为
                {origin === "self_created" ? "自行拍摄" : "取材站外"}。
              </p>
            )}
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              我确认来源声明与授权相符
            </label>
            <label>
              发布方式
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as typeof mode)}
              >
                <option value="immediate">
                  立即发布{scope === "simulation" ? "（仿真）" : "（真实）"}
                </option>
                <option value="native_schedule">
                  平台原生定时
                  {scope === "simulation" ? "（仿真）" : "（尚未验收）"}
                </option>
              </select>
            </label>
            {mode === "native_schedule" && (
              <label>
                北京时间（Asia/Shanghai）
                <input
                  type="datetime-local"
                  value={wall}
                  onChange={(e) => setWall(e.target.value)}
                  required
                />
              </label>
            )}
            <button
              className="secondary"
              disabled={!confirmed || !selected.length}
            >
              保存配置并预检
            </button>
          </fieldset>
        </form>
      )}
      {!hasAttempts && !loading && (
        <div className="preset-note">
          <button
            type="button"
            className="secondary"
            disabled={busy || !confirmed || !selected.length}
            onClick={() => void saveDefaults()}
          >
            保存为新任务默认发布配置
          </button>
          <p>
            仅保存当前环境、所选账号、话题与来源声明；新任务默认立即发布，不复用本次预约时间。
          </p>
          {defaultsMessage && <p role="status">{defaultsMessage}</p>}
        </div>
      )}
      {run?.snapshot_digest &&
        !job.content_frozen &&
        !job.spec.publication_profile_id && (
          <details>
            <summary>保存为可复用发布流程模板</summary>
            <label>
              流程模板名称
              <input
                value={profileName}
                onChange={(e) => setProfileName(e.target.value)}
              />
            </label>
            <button
              className="secondary"
              disabled={busy || !profileName.trim()}
              onClick={() => void saveProfile()}
            >
              保存流程模板
            </button>
            <p>
              仅复用账号、话题规则和来源声明；不复用本视频的标题、封面或预约时间。
            </p>
            {profileMessage && <p role="status">{profileMessage}</p>}
          </details>
        )}
      {form?.topic_selection && !topic && !run?.topic && (
        <p>
          标题自动匹配：
          {form.topic_selection.status === "selected"
            ? `${form.topic_selection.group_name} · ${form.topic_selection.tags.join("、")}`
            : "未唯一匹配，请手动选择话题组"}
        </p>
      )}
      {run?.topic && (
        <p>
          命中话题组：{run.topic.group_name} · {run.topic.tags.join("、")}
        </p>
      )}
      {run?.scheduled_at_utc && (
        <p>平台预约：{formatBeijing(run.scheduled_at_utc)}（北京时间）</p>
      )}
      {!!run?.targets.length && (
        <TargetStatus
          targets={run.targets}
          simulation={run.scope === "simulation"}
        />
      )}
      {commandActive && (
        <p role="status">操作已排队或正在执行，请等待状态回执。</p>
      )}
      {run?.source_conflict && (
        <p className="error" role="alert">
          此来源在所选账号已有其他发布任务；请先核对原任务，不能重复投稿。
        </p>
      )}
      {run?.resource_blocked && (
        <p className="error" role="alert">
          资源上传结果不明：请先核对临时存储和扣次。本任务不会自动重新上传或继续投稿。
        </p>
      )}
      {run?.reason && (
        <p className="preset-note break">
          发布状态原因：{providerReasonLabel(run.reason)}（不等同于平台已公开）
        </p>
      )}
      {scheduleBlocked && (
        <p role="alert">
          平台原生定时尚未验收，真实提交已阻断；不会回退为立即发布。
        </p>
      )}
      {run?.can_submit && run.scope === scope && (
        <button
          className="primary"
          disabled={
            busy ||
            loading ||
            commandActive ||
            (scope === "live" && (!liveReady || scheduleBlocked))
          }
          onClick={() => void execute("submit")}
        >
          {scope === "live"
            ? hasAttempts
              ? "真实续跑已确认未提交的目标"
              : "开始真实发布"
            : hasAttempts
              ? "续跑已确认未提交的目标"
              : "开始本地仿真"}
        </button>
      )}
      {run?.can_reconcile && (
        <>
          <p>结果不明时只查询已有回执，不会重复上传或投稿。</p>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => void execute("reconcile")}
          >
            查询对账（不重投）
          </button>
        </>
      )}
      {job.content_frozen && (
        <p>
          内容已冻结；如需修改预约或删除远端作品，请前往对应平台。此处不会自动撤销。
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}
