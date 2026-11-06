import { useEffect, useState } from "react";
import {
  getJobs,
  getSettings,
  getCapabilities,
  initializeSession,
  type Job,
  type Settings,
  type Capabilities,
} from "./api";
import JobsPage from "./pages/JobsPage";
import SettingsPage from "./pages/SettingsPage";
import "./styles.css";
export default function App() {
  const [tab, setTab] = useState("jobs"),
    [jobs, setJobs] = useState<Job[]>([]),
    [settings, setSettings] = useState<Settings | null>(null),
    [capabilities, setCapabilities] = useState<Capabilities | null>(null),
    [error, setError] = useState(""),
    [lastSync, setLastSync] = useState("");
  async function refresh() {
    try {
      const rows = await getJobs();
      setJobs(rows);
      setLastSync(new Date().toLocaleTimeString("zh-CN"));
      setError("");
    } catch {
      setError("与本地服务的连接暂时中断，保留上次同步结果。");
    }
  }
  useEffect(() => {
    let alive = true;
    initializeSession()
      .then(() => Promise.all([getJobs(), getSettings(), getCapabilities()]))
      .then(([rows, config, caps]) => {
        if (alive) {
          setJobs(rows);
          setSettings(config);
          setCapabilities(caps);
          setLastSync(new Date().toLocaleTimeString("zh-CN"));
        }
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : "连接失败");
      });
    return () => {
      alive = false;
    };
  }, []);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">F</span>
          <div>
            FABU<small>本地视频工作台</small>
          </div>
        </div>
        <div className="workspace-label">
          个人工作空间 <span>LOCAL</span>
        </div>
        <nav aria-label="主导航">
          <button
            className={tab === "jobs" ? "active" : ""}
            onClick={() => setTab("jobs")}
          >
            <span aria-hidden="true">▤</span>任务工作台
          </button>
          <button disabled title="将在流水线阶段接入">
            <span aria-hidden="true">☷</span>处理流水线 <small>开发中</small>
          </button>
          <button
            className={tab === "settings" ? "active" : ""}
            onClick={() => setTab("settings")}
          >
            <span aria-hidden="true">⚙</span>运行设置
          </button>
        </nav>
        <div className="sidebar-bottom">
          <span className="dot" />
          本机运行<small>文件留在本地，操作有据可查。</small>
          <span className="version">V0.2 · 开发预览</span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            工作空间 <span className="slash">/</span>{" "}
            {tab === "jobs" ? "任务" : "设置"}
          </span>
          <span className="local-pill">
            <span className="dot" />
            仅本机可访问
          </span>
        </header>
        <div className="content">
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          {settings ? (
            tab === "jobs" ? (
              <JobsPage jobs={jobs} settings={settings} refresh={refresh} />
            ) : (
              <SettingsPage settings={settings} onSaved={setSettings} />
            )
          ) : (
            <div className="panel padded">
              <h1>连接本地工作台</h1>
              <p>请通过本机启动入口打开受保护的会话。</p>
            </div>
          )}
          <section className="publish-card">
            <div className="publish-intro">
              <span className="eyebrow">PUBLISHING / 发布能力</span>
              <h2>发布未解锁</h2>
              <p>
                先核对免费权益与平台实际接收结果。
                <br />
                此版本不会上传或发布视频。
              </p>
              <button className="secondary" disabled>
                立即发布
              </button>
            </div>
            <div className="capability-list">
              {[
                ["视频上传", capabilities?.upload],
                ["立即发布", capabilities?.immediate],
                ["平台原生定时", capabilities?.native_schedule],
                ["免费扣次规则", capabilities?.quota],
              ].map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <span className="badge neutral">
                    {value === "SUPPORTED"
                      ? "已核查"
                      : value === "UNSUPPORTED"
                        ? "不支持"
                        : "未验证"}
                  </span>
                </div>
              ))}
            </div>
          </section>
          <footer>
            本地优先 · 源文件保留 · 异常暂停
            <span>{lastSync ? `最后同步 ${lastSync}` : "等待连接"}</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
