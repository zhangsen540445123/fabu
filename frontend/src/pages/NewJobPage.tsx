import { useState } from "react";
import { createJob, type PresetSummary } from "../api";
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
  const selected = presets.find((p) => p.version.id === version);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
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
            <strong>
              {selected?.approved
                ? "预设已确认，可自动处理"
                : "首次使用或参数变化时，将暂停等待样片确认"}
            </strong>
            <p>
              基础输出 HEVC · CRF{String(selected?.version.output.crf ?? 20)} ·
              30fps。标题只用于发布内容，不改变处理预设。
            </p>
          </div>
          <label>
            执行模式
            <select value="local" disabled>
              <option value="local">仅本地处理</option>
              <option disabled>立即发布（未解锁）</option>
              <option disabled>平台原生定时（未解锁）</option>
            </select>
          </label>
          <p className="muted">使用原始YouTube封面；不自动上传或投稿。</p>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary wide" disabled={busy || !version}>
            {busy ? "保存中…" : "保存本地任务"}
          </button>
        </form>
      </section>
    </div>
  );
}
