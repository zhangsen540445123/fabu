import { useState } from "react";
import { saveSettings, type Settings } from "../api";
export default function SettingsPage({
  settings,
  onSaved,
}: {
  settings: Settings;
  onSaved: (settings: Settings) => void;
}) {
  const [data, setData] = useState(settings.data_dir),
    [tools, setTools] = useState(settings.tools_dir),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      onSaved(await saveSettings({ data_dir: data, tools_dir: tools }));
      setMessage("设置已保存。");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  const free = settings.environment.paths.data_dir?.free_bytes;
  return (
    <>
      <section className="page-heading">
        <div>
          <span className="eyebrow">PREFERENCES / 本机环境</span>
          <h1>运行设置</h1>
          <p>所有文件保存在这台电脑上，不自动迁移或覆盖已有素材。</p>
        </div>
      </section>
      <div className="settings-grid">
        <section className="panel padded">
          <h2>文件与工具</h2>
          <form onSubmit={save}>
            <label>
              媒体保存目录
              <input value={data} onChange={(e) => setData(e.target.value)} />
            </label>
            <label>
              工具目录
              <input value={tools} onChange={(e) => setTools(e.target.value)} />
            </label>
            <p className="muted">
              有任务时不能直接切换媒体目录。工具存在与可用性还需独立核查。
            </p>
            <button className="primary" disabled={busy}>
              保存设置
            </button>
            {message && <p role="status">{message}</p>}
          </form>
        </section>
        <section className="panel padded">
          <h2>环境检查</h2>
          <dl>
            <dt>Python</dt>
            <dd>{settings.environment.python_version}</dd>
            <dt>系统</dt>
            <dd>{settings.environment.platform}</dd>
            <dt>媒体盘可用空间</dt>
            <dd>{free ? `${(free / 1024 ** 3).toFixed(1)} GB` : "未检测"}</dd>
            <dt>配置目录</dt>
            <dd className="break">{settings.state_dir}</dd>
            <dt>凭证</dt>
            <dd>
              {settings.credentials.configured
                ? "已配置"
                : "尚未接入安全凭证存储"}
            </dd>
            <dt>网络访问</dt>
            <dd>仅 127.0.0.1</dd>
          </dl>
        </section>
      </div>
    </>
  );
}
