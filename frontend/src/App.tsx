import "./styles.css";
import { useCallback, useEffect, useState } from "react";
import {
  getCapabilities,
  getJobs,
  getMediaStatus,
  getPresets,
  getSettings,
  initializeSession,
  type Capabilities,
  type Job,
  type MediaStatus,
  type PresetSummary,
  type Settings,
} from "./api";
import PublishingReadinessCard from "./components/PublishingReadinessCard";
import { usePublishingReadiness } from "./publishing-readiness";
import JobsPage from "./pages/JobsPage";
import SettingsPage from "./pages/SettingsPage";
import PipelinePage from "./pages/PipelinePage";
import AccountsPage from "./pages/AccountsPage";
export default function App() {
  const [tab, setTab] = useState("jobs"),
    [jobs, setJobs] = useState<Job[]>([]),
    [settings, setSettings] = useState<Settings | null>(null),
    [capabilities, setCapabilities] = useState<Capabilities | null>(null),
    [presets, setPresets] = useState<PresetSummary[]>([]),
    [status, setStatus] = useState<MediaStatus | null>(null),
    [error, setError] = useState(""),
    [lastSync, setLastSync] = useState("");
  const { readiness, refreshReadiness } = usePublishingReadiness(!!settings);
  const refreshJobs = useCallback(async () => {
    try {
      const [rows, state] = await Promise.all([getJobs(), getMediaStatus()]);
      setJobs(rows);
      setStatus(state);
      setLastSync(new Date().toLocaleTimeString("zh-CN"));
      setError("");
    } catch {
      setError("与本地服务的连接暂时中断，保留上次同步结果。");
    }
  }, []);
  const refresh = useCallback(async () => {
    await refreshJobs();
    await Promise.all([
      getPresets()
        .then(setPresets)
        .catch(() => {
          setError("预设列表刷新失败，请稍后重试。");
        }),
      getSettings()
        .then(setSettings)
        .catch(() => {
          setError("默认处理预设刷新失败，保留上次配置；请刷新后再新建任务。");
        }),
    ]);
  }, [refreshJobs]);
  useEffect(() => {
    let alive = true;
    initializeSession()
      .then(() =>
        Promise.all([
          getJobs(),
          getSettings(),
          getCapabilities(),
          getPresets(),
          getMediaStatus(),
        ]),
      )
      .then(([rows, config, caps, templates, state]) => {
        if (alive) {
          setJobs(rows);
          setSettings(config);
          setCapabilities(caps);
          setPresets(templates);
          setStatus(state);
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
  useEffect(() => {
    if (!settings) return;
    const timer = setInterval(() => void refreshJobs(), 2000);
    return () => clearInterval(timer);
  }, [settings, refreshJobs]);
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
          <button
            className={tab === "pipeline" ? "active" : ""}
            onClick={() => setTab("pipeline")}
          >
            <span aria-hidden="true">☷</span>处理流水线
          </button>
          <button
            className={tab === "accounts" ? "active" : ""}
            onClick={() => setTab("accounts")}
          >
            <span aria-hidden="true">◎</span>账号与发布
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
            {tab === "jobs"
              ? "任务"
              : tab === "pipeline"
                ? "流水线"
                : tab === "accounts"
                  ? "账号"
                  : "设置"}
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
              <JobsPage
                jobs={jobs}
                settings={settings}
                presets={presets}
                status={status}
                refresh={refresh}
              />
            ) : tab === "pipeline" ? (
              <PipelinePage onChanged={() => void refresh()} />
            ) : tab === "accounts" ? (
              <AccountsPage />
            ) : (
              <SettingsPage settings={settings} onSaved={setSettings} />
            )
          ) : (
            <div className="panel padded">
              <h1>连接本地工作台</h1>
              <p>请通过本机启动入口打开受保护的会话。</p>
            </div>
          )}
          <PublishingReadinessCard
            readiness={readiness}
            capabilities={capabilities}
            refresh={refreshReadiness}
          />
          <footer>
            本地优先 · 源文件保留 · 异常暂停
            <span>{lastSync ? `最后同步 ${lastSync}` : "等待连接"}</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
