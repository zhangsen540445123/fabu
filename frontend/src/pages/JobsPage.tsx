import { useState } from "react";
import { cancelJob, createJob, type Job, type Settings } from "../api";
const states: Record<string, string> = {
  NEW: "待处理",
  INSPECTING: "检查中",
  DOWNLOADING: "下载中",
  PROCESSING: "处理中",
  READY: "成片就绪",
  PUBLISHING: "提交中",
  PAUSED: "已暂停",
  FAILED: "失败",
  CANCELED: "已取消",
};
export default function JobsPage({
  jobs,
  settings,
  refresh,
}: {
  jobs: Job[];
  settings: Settings;
  refresh: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false),
    [title, setTitle] = useState(""),
    [url, setUrl] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [selected, setSelected] = useState<Job | null>(null);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await createJob({
        title,
        source_url: url,
        preset_version_id: settings.default_preset_version_id,
        target_ids: [],
        publish_mode: "local",
      });
      setEditing(false);
      setTitle("");
      setUrl("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  async function cancel(job: Job) {
    setBusy(true);
    setError("");
    try {
      await cancelJob(job);
      setSelected(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <section className="page-heading">
        <div>
          <span className="eyebrow">WORKSPACE / 工作台</span>
          <h1>每条视频，都有迹可循。</h1>
          <p>从素材到成片，再到发布。把重复操作交给流程。</p>
        </div>
        <button className="primary" onClick={() => setEditing(true)}>
          新建任务 <span aria-hidden="true">＋</span>
        </button>
      </section>
      <div className="metrics">
        <article>
          <span>全部任务</span>
          <strong>{jobs.length.toString().padStart(2, "0")}</strong>
          <small>本机保存，随时查看</small>
        </article>
        <article>
          <span>待处理</span>
          <strong>
            {jobs
              .filter((j) => j.state === "NEW")
              .length.toString()
              .padStart(2, "0")}
          </strong>
          <small>按队列顺序执行</small>
        </article>
        <article>
          <span>需要关注</span>
          <strong>
            {jobs
              .filter((j) => ["PAUSED", "FAILED"].includes(j.state))
              .length.toString()
              .padStart(2, "0")}
          </strong>
          <small>异常暂停，不盲目重试</small>
        </article>
      </div>
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>任务队列</h2>
            <p>处理引擎接入后，已保存任务才会开始下载与渲染。</p>
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
        {jobs.length === 0 ? (
          <div className="empty">
            <span className="empty-icon" aria-hidden="true">
              ▤
            </span>
            <h3>准备好第一条视频了吗？</h3>
            <p>
              创建本地任务，选择视频链接和标题。
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
                        onClick={() => setSelected(job)}
                      >
                        {job.spec.title}
                      </button>
                      <small>
                        {job.id.slice(0, 8)} · 内容版本 {job.content_revision}
                      </small>
                    </td>
                    <td>基础2K</td>
                    <td>
                      <span
                        className={`badge ${job.state === "FAILED" ? "danger" : ""}`}
                      >
                        {states[job.state] ?? job.state}
                      </span>
                    </td>
                    <td>
                      {job.spec.publish_mode === "local"
                        ? "仅本地处理"
                        : "仿真"}
                    </td>
                    <td>
                      <button
                        className="quiet"
                        disabled={
                          busy || job.content_frozen || job.state !== "NEW"
                        }
                        onClick={() => void cancel(job)}
                      >
                        取消
                      </button>
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
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="new-title"
          >
            <div className="panel-title">
              <h2 id="new-title">新建本地任务</h2>
              <button
                className="quiet"
                aria-label="关闭新建任务"
                onClick={() => setEditing(false)}
              >
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
              <div className="preset-note">
                <strong>基础2K · 保留原声</strong>
                <p>
                  HEVC · CRF20 · 30fps
                  <br />
                  不额外裁切、翻转、变速或降噪
                </p>
              </div>
              <p className="muted">只保存本地任务，不会上传或投稿。</p>
              {error && <p className="error">{error}</p>}
              <button className="primary wide" disabled={busy}>
                {busy ? "保存中…" : "保存本地任务"}
              </button>
            </form>
          </section>
        </div>
      )}
      {selected && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="detail-title"
          >
            <div className="panel-title">
              <h2 id="detail-title">任务详情</h2>
              <button
                className="quiet"
                aria-label="关闭任务详情"
                onClick={() => setSelected(null)}
              >
                ×
              </button>
            </div>
            <h3>{selected.spec.title}</h3>
            <dl>
              <dt>状态</dt>
              <dd>{states[selected.state] ?? selected.state}</dd>
              <dt>来源</dt>
              <dd className="break">{selected.spec.source_url}</dd>
              <dt>任务ID</dt>
              <dd className="break">{selected.id}</dd>
              <dt>异常原因</dt>
              <dd>{selected.pause_reason ?? "无"}</dd>
            </dl>
          </section>
        </div>
      )}
    </>
  );
}
