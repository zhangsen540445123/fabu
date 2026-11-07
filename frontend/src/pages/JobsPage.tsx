import { hardwareReasons, mediaPhases } from "../mediaLabels";
import { useEffect, useState } from "react";
import {
  cancelJob,
  getJobPlan,
  resumeJob,
  reviseJob,
  type Job,
  type JobPlan,
  type MediaStatus,
  type PresetSummary,
  type Settings,
} from "../api";
import NewJobPage from "./NewJobPage";
import PreviewPanel from "../components/PreviewPanel";
import PublishForm from "../components/PublishForm";
const states: Record<string, string> = {
  NEW: "等待处理",
  INSPECTING: "检查任务",
  DOWNLOADING: "下载中",
  PROCESSING: "处理中",
  READY: "成片就绪",
  PUBLISHING: "提交中",
  PAUSED: "已暂停",
  FAILED: "失败",
  CANCELED: "已取消",
  COMPLETED: "提交处理完成",
};
const reasons: Record<string, string> = {
  preview_required: "旧版样片暂停：可点击继续按所选预设处理，无需样片确认",
  source_unavailable: "下载未完成，请检查网络、访问权限与下载工具配置",
  source_authentication_required:
    "YouTube要求登录验证；请在Chrome中登录或完成验证，再手动重试。登录态不保证能解决所有访问限制",
  source_browser_session_required:
    "请在已登录YouTube的Chrome中启用Fabu YouTube助手，确认已连接后手动重试；无需导出Cookie文件",
  node_not_configured: "请在运行设置中配置Node.js可执行文件",
  pipeline_invalid: "处理参数不兼容，请检查截取范围、字幕时刻、素材或字体",
  render_failed: "成片生成未完成，请检查磁盘空间与素材",
  gpu_driver_incompatible:
    "驱动与当前NVENC编码器不兼容；未进行CPU回退，可切换预设为自动或CPU模式",
  gpu_no_device: "未检测到可用NVIDIA显卡；请选择自动或CPU模式",
  gpu_encoder_unavailable: "当前FFmpeg不具备可用NVENC编码器",
  gpu_probe_failed: "GPU编码探测未通过，任务未开始正式编码",
  gpu_inventory_unavailable: "无法可靠获取显卡与驱动信息",
  gpu_environment_changed:
    "显卡或驱动已变化，冻结任务不会自动切换后端；请创建新内容版本",
  gpu_probe_required: "硬件能力尚未验证",
  processing_snapshot_changed: "处理环境或素材发生变化，请创建新内容版本",
  processing_canceled: "处理已取消",
  interrupted_processing: "上次处理被中断，可从已验证阶段重新开始",
  user_canceled: "用户取消",
  workflow_preflight_failed:
    "自动发布预检未通过；检查媒体确认、账号、话题、预约时间或重复来源",
  handoff_interrupted: "自动衔接被中断，请先查看发布状态",
  target_failed: "某平台提交失败，后续目标已停止",
  reconcile_required: "提交结果需要对账，不会盲目重投",
  publication_stage_failed: "发布阶段中断，请检查状态后再继续",
  interrupted_publication: "上次发布中断，先查看逐平台回执",
  manual: "手动暂停",
};
function JobDetail({
  job,
  presets,
  refresh,
  onClose,
}: {
  job: Job;
  presets: PresetSummary[];
  refresh: () => Promise<void>;
  onClose: () => void;
}) {
  const [plan, setPlan] = useState<JobPlan | null>(null),
    [chosen, setChosen] = useState(job.spec.preset_version_id),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setPlan(null);
    setChosen(job.spec.preset_version_id);
    getJobPlan(job.id)
      .then((value) => {
        if (alive) setPlan(value);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [job.id, job.state, job.content_revision]);
  async function changePreset() {
    setBusy(true);
    setError("");
    try {
      await reviseJob(job, chosen);
      await refresh();
      setPlan(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "更换失败");
    } finally {
      setBusy(false);
    }
  }
  const versions = presets.map((p) => p.version);
  if (plan && !versions.some((v) => v.id === plan.preset.id))
    versions.push(plan.preset);
  return (
    <div className="modal-backdrop">
      <section
        className="modal detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="detail-title"
      >
        <div className="panel-title">
          <h2 id="detail-title">任务详情 · 内容版本 {job.content_revision}</h2>
          <button className="quiet" aria-label="关闭任务详情" onClick={onClose}>
            ×
          </button>
        </div>
        <h3>{job.spec.title}</h3>
        <dl>
          <dt>状态</dt>
          <dd>{states[job.state] ?? job.state}</dd>
          <dt>来源</dt>
          <dd className="break">{job.spec.source_url}</dd>
          <dt>处理预设</dt>
          <dd>
            {plan
              ? `${plan.preset.name} · v${plan.preset.revision}`
              : job.spec.preset_version_id}
          </dd>
          <dt>异常原因</dt>
          <dd>
            {job.pause_reason
              ? (reasons[job.pause_reason] ?? job.pause_reason)
              : "无"}
          </dd>
        </dl>
        {plan && (
          <div className="source-summary">
            {plan.source_manifest?.thumbnail && (
              <img
                alt="原始YouTube封面"
                src={`/api/assets/${plan.source_manifest.thumbnail.id}/file`}
              />
            )}
            <div>
              <strong>
                {plan.output_size.width} × {plan.output_size.height}
              </strong>
              <p>
                预计时长 {(plan.duration_ms / 1000).toFixed(1)} 秒 ·
                固定处理快照
              </p>
              <p>
                实际执行：
                {plan.execution?.path === "mixed"
                  ? "CPU处理 + GPU编码"
                  : "CPU编码"}{" "}
                · {plan.actual_encoder ?? "libx265"}
              </p>
              {plan.execution && (
                <p>
                  {hardwareReasons[plan.execution.reason] ?? "执行配置已冻结"}
                </p>
              )}
              {!!plan.timings && Object.keys(plan.timings).length > 0 && (
                <details>
                  <summary>处理阶段耗时</summary>
                  <dl>
                    {Object.entries(plan.timings).map(([key, seconds]) => (
                      <div key={key}>
                        <dt>{mediaPhases[key] ?? key}</dt>
                        <dd>{seconds.toFixed(1)} 秒</dd>
                      </div>
                    ))}
                  </dl>
                </details>
              )}
              <small className="break">计划 {plan.digest.slice(0, 16)}</small>
            </div>
          </div>
        )}
        {job.state === "READY" && (
          <div className="preset-note">
            <strong>本地成片已验证</strong>
            <p>文件完整性、格式和处理快照已检查。尚未上传或发布。</p>
            <a className="media-link" href={`/api/jobs/${job.id}/output`}>
              下载已验证成片
            </a>
          </div>
        )}
        {!job.content_frozen &&
          ["PAUSED", "FAILED", "READY", "NEW"].includes(job.state) && (
            <details className="revision-editor">
              <summary>更换处理预设（创建新内容版本）</summary>
              <label>
                新版本处理预设
                <select
                  value={chosen}
                  onChange={(e) => setChosen(e.target.value)}
                >
                  {versions.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name} · v{v.revision}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="secondary"
                disabled={busy || chosen === job.spec.preset_version_id}
                onClick={() => void changePreset()}
              >
                应用为新内容版本
              </button>
              <p>
                不会修改已经开始提交的内容。修改后重新检查素材与处理计划，本地处理无需样片确认；发布仍保留专用预检。
              </p>
              {error && (
                <p role="alert" className="error">
                  {error}
                </p>
              )}
            </details>
          )}
        {!!job.output_manifest &&
          ["READY", "PAUSED", "PUBLISHING", "COMPLETED", "CANCELED"].includes(
            job.state,
          ) && <PublishForm job={job} refresh={refresh} />}
        {plan && ["PAUSED", "READY"].includes(job.state) && (
          <details open={job.state === "PAUSED"}>
            <summary>可选样片检查</summary>
            <PreviewPanel job={job} refresh={refresh} />
          </details>
        )}
      </section>
    </div>
  );
}
export default function JobsPage({
  jobs,
  settings,
  presets,
  status,
  refresh,
}: {
  jobs: Job[];
  settings: Settings;
  presets: PresetSummary[];
  status: MediaStatus | null;
  refresh: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false),
    [selected, setSelected] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const detail = jobs.find((j) => j.id === selected);
  async function action(job: Job, kind: "cancel" | "resume") {
    setBusy(true);
    setError("");
    try {
      if (kind === "cancel") await cancelJob(job);
      else await resumeJob(job);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }
  const workerText = !status?.running
    ? "处理引擎未启动"
    : status.error_code === "tools_not_configured"
      ? "处理工具未配置：请设置FFmpeg/ffprobe目录"
      : status.job_id
        ? status.kind === "preview"
          ? "正在生成样片，其他重任务串行等待"
          : status.kind === "publishing"
            ? "正在执行发布仿真，其他重任务串行等待"
            : "正在执行本地处理任务"
        : "处理引擎已就绪，按队列串行运行";
  return (
    <>
      <section className="page-heading">
        <div>
          <span className="eyebrow">WORKSPACE / 工作台</span>
          <h1>每条视频，都有迹可循。</h1>
          <p>从下载、完整性校验到预设处理，自动生成本地成片；样片检查可选。</p>
        </div>
        <button className="primary" onClick={() => setEditing(true)}>
          新建任务 <span aria-hidden="true">＋</span>
        </button>
      </section>
      <div className="metrics">
        <article>
          <span>全部任务</span>
          <strong>{String(jobs.length).padStart(2, "0")}</strong>
          <small>本机保存，随时查看</small>
        </article>
        <article>
          <span>待处理</span>
          <strong>
            {String(
              jobs.filter((j) =>
                ["NEW", "INSPECTING", "DOWNLOADING", "PROCESSING"].includes(
                  j.state,
                ),
              ).length,
            ).padStart(2, "0")}
          </strong>
          <small>下载、预览、渲染串行运行</small>
        </article>
        <article>
          <span>需要关注</span>
          <strong>
            {String(
              jobs.filter((j) => ["PAUSED", "FAILED"].includes(j.state)).length,
            ).padStart(2, "0")}
          </strong>
          <small>异常暂停，不盲目重试</small>
        </article>
      </div>
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>任务队列</h2>
            <p>{workerText}</p>
          </div>
          <button className="quiet" onClick={() => void refresh()}>
            刷新任务
          </button>
        </div>
        {error && (
          <div role="alert" className="error">
            {error}
          </div>
        )}
        {!jobs.length ? (
          <div className="empty">
            <span className="empty-icon" aria-hidden="true">
              ▤
            </span>
            <h3>准备好第一条视频了吗？</h3>
            <p>
              填入视频链接与标题，选择处理预设。
              <br />
              目前不会上传或发布任何内容。
            </p>
            <button className="secondary" onClick={() => setEditing(true)}>
              创建第一条任务
            </button>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>视频任务</th>
                  <th>处理预设</th>
                  <th>状态</th>
                  <th>模式</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <button
                        className="title-button"
                        onClick={() => setSelected(job.id)}
                      >
                        {job.spec.title}
                      </button>
                      <small>
                        {job.id.slice(0, 8)} · 内容版本 {job.content_revision}
                      </small>
                    </td>
                    <td>
                      {presets.find(
                        (p) => p.version.id === job.spec.preset_version_id,
                      )?.version.name ??
                        `版本 ${job.spec.preset_version_id.slice(0, 8)}`}
                    </td>
                    <td>
                      <span
                        className={`badge ${job.state === "FAILED" ? "danger" : ""}`}
                      >
                        {states[job.state] ?? job.state}
                      </span>
                      {job.pause_reason === "preview_required" && (
                        <small>可继续处理，无需样片确认</small>
                      )}
                      {status?.job_id === job.id && status.phase && (
                        <small>
                          {mediaPhases[status.phase] ?? "处理中"} ·{" "}
                          {Math.round(status.phase_elapsed_s ?? 0)} 秒
                        </small>
                      )}
                      {status?.job_id === job.id && status.percent != null && (
                        <div>
                          <progress
                            aria-label="整体渲染进度"
                            value={status.percent}
                            max={100}
                          />
                          <small>{status.percent.toFixed(1)}%</small>
                        </div>
                      )}
                    </td>
                    <td>
                      {
                        (
                          {
                            local: "仅本地处理",
                            simulation: "本地仿真",
                            immediate: "立即",
                            native_schedule: "平台定时",
                          } as Record<string, string>
                        )[job.spec.publish_mode]
                      }
                    </td>
                    <td>
                      <div className="table-actions">
                        <button
                          className="quiet"
                          onClick={() => setSelected(job.id)}
                        >
                          查看
                        </button>
                        {["PAUSED", "FAILED"].includes(job.state) &&
                          !job.output_manifest && (
                            <button
                              className="quiet"
                              disabled={busy || job.content_frozen}
                              onClick={() => void action(job, "resume")}
                            >
                              继续
                            </button>
                          )}
                        <button
                          className="quiet"
                          disabled={
                            busy ||
                            job.content_frozen ||
                            ["CANCELED", "COMPLETED"].includes(job.state)
                          }
                          onClick={() => void action(job, "cancel")}
                        >
                          取消
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="footnote">
        <span className="dot" />
        <p>原视频保留 · 附加处理默认关闭 · 参数与素材按版本保存</p>
      </section>
      {editing && (
        <NewJobPage
          presets={presets}
          defaultPreset={settings.default_preset_version_id}
          onClose={() => setEditing(false)}
          onSaved={refresh}
        />
      )}{" "}
      {detail && (
        <JobDetail
          key={detail.id}
          job={detail}
          presets={presets}
          refresh={refresh}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
