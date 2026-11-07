import { useEffect, useState } from "react";
import {
  configurePublication,
  getAccounts,
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
import { beijingToUtc, formatBeijing } from "../publishing-time";
import TargetStatus, { platformNames } from "./TargetStatus";
export default function PublishForm({
  job,
  refresh,
}: {
  job: Job;
  refresh: () => Promise<void>;
}) {
  const [handoff, setHandoff] = useState<WorkflowHandoff | null>(null);
  const [profileName, setProfileName] = useState("日常助眠"),
    [profileMessage, setProfileMessage] = useState("");
  const [scope, setScope] = useState<"simulation" | "live">("simulation");
  const [catalog, setCatalog] = useState<AccountCatalogue | null>(null),
    [selected, setSelected] = useState<string[]>([]),
    [run, setRun] = useState<PublishRun | null>(null),
    [mode, setMode] = useState<"immediate" | "native_schedule">("immediate"),
    [wall, setWall] = useState(""),
    [topic, setTopic] = useState(""),
    [origin, setOrigin] = useState<"licensed_reuse" | "self_created">(
      "licensed_reuse",
    ),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setRun(null);
    setCatalog(null);
    setConfirmed(false);
    getAccounts(scope)
      .then((c) => {
        if (alive) {
          setCatalog(c);
          setSelected(
            scope === "simulation"
              ? c.accounts.filter((a) => a.active).map((a) => a.account_id)
              : [],
          );
        }
      })
      .catch((e) => {
        if (alive) setError(String(e));
      });
    const sync = () =>
      getTargetStatus(job.id, scope)
        .then((r) => {
          if (alive) setRun(r);
        })
        .catch((e) => {
          if (alive) setError(String(e));
        });
    void sync();
    const timer = setInterval(() => void sync(), 1500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [job.id, scope]);
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
  async function resumeHandoff() {
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
    job.content_frozen || hasAttempts || commandActive || job.state !== "READY";
  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const accounts = (catalog?.accounts ?? []).filter((a) =>
        selected.includes(a.account_id),
      );
      if (!confirmed || !accounts.length)
        throw new Error("请选择账号并确认来源声明");
      const command: PublicationDraft = {
        expected_revision: job.revision,
        scope,
        mode,
        topic_override: topic || null,
        accounts: accounts.map((a) => ({
          account_id: a.account_id,
          declaration: {
            origin,
            confirmed,
            statement:
              a.platform === "toutiao"
                ? origin === "licensed_reuse"
                  ? "offsite"
                  : "self_shot"
                : "none",
            bilibili_type:
              a.platform === "bilibili"
                ? origin === "self_created"
                  ? "self_produced"
                  : "repost"
                : null,
          },
        })),
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
    if (!run?.snapshot_digest) return;
    setBusy(true);
    setError("");
    try {
      if (action === "submit") {
        if (scope !== "simulation" || run.scope !== "simulation")
          throw new Error("真实投稿尚未解锁");
        await submitPublication(job.id, run.snapshot_digest);
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
          disabled={busy || commandActive}
          onChange={(e) => setScope(e.target.value as typeof scope)}
        >
          <option value="simulation">本地仿真（不会上传）</option>
          <option value="live">真实账号（配置、预检与只读对账）</option>
        </select>
      </label>
      <div className="preset-note">
        <strong>
          {scope === "simulation"
            ? "仅本地仿真 · 真实发布未解锁"
            : "真实账号配置 · 投稿功能未解锁"}
        </strong>
        <p>
          当前操作不会上传或投稿。真实账号支持配置、预检及已有任务的只读对账；回执协议未经验证时仍显示结果不明，不会把“查询成功”当作“平台已公开”。预约时间表示平台原生预约，不是在本机等到时间再上传。
        </p>
      </div>
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
            {catalog?.accounts.map((a) => (
              <label className="checkbox-row" key={a.account_id}>
                <input
                  type="checkbox"
                  checked={selected.includes(a.account_id)}
                  disabled={!a.active}
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
                }}
              >
                <option value="licensed_reuse">取得授权的转载内容</option>
                <option value="self_created">本人创作的原创内容</option>
              </select>
            </label>
            <p>
              授权转载不等于本人原创。B站将声明为
              {origin === "self_created" ? "原创" : "转载并附来源链接"}
              ，头条将声明为
              {origin === "self_created" ? "自行拍摄" : "取材站外"}。
            </p>
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
                  立即发布{scope === "simulation" ? "（仿真）" : "（尚未解锁）"}
                </option>
                <option value="native_schedule">
                  平台原生定时
                  {scope === "simulation" ? "（仿真）" : "（尚未解锁）"}
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
      {run?.can_submit && (
        <button
          className="primary"
          disabled={busy}
          onClick={() => void execute("submit")}
        >
          {hasAttempts ? "续跑已确认未提交的目标" : "开始本地仿真"}
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
