import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  initializeSession: vi.fn(),
  getJobs: vi.fn(),
  getSettings: vi.fn(),
  getCapabilities: vi.fn(),
  getPresets: vi.fn(),
  getJobPlan: vi.fn(),
  getJobPreviews: vi.fn(),
  getAccounts: vi.fn(),
  getTargetStatus: vi.fn(),
  getPublicationProfiles: vi.fn(),
  createPreview: vi.fn(),
  approvePreview: vi.fn(),
  getMediaStatus: vi.fn(),
  createJob: vi.fn(),
  resumeJob: vi.fn(),
  cancelJob: vi.fn(),
  saveSettings: vi.fn(),
}));
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  ...api,
}));
const settings = {
  data_dir: "D:/FabuData/jobs",
  state_dir: "C:/Fake/Fabu",
  tools_dir: "C:/Fake/Fabu/tools",
  bind_host: "127.0.0.1",
  test_mode: "simulation",
  default_preset_version_id: "preset-1",
  credentials: { configured: false },
  environment: {
    python_version: "3.12.10",
    platform: "Windows",
    paths: {
      data_dir: { exists: false, free_bytes: 100000000000, writable: null },
    },
  },
};
const job = {
  id: "job-1",
  revision: 1,
  content_revision: 1,
  state: "NEW",
  pause_reason: null,
  content_frozen: false,
  spec: {
    title: "剃须仙人的日常",
    source_url: "https://youtu.be/L6imSiJLd9Q",
    preset_version_id: "preset-1",
    target_ids: [],
    publish_mode: "local",
  },
};

beforeEach(() => {
  vi.resetAllMocks();
  api.initializeSession.mockResolvedValue(undefined);
  api.getJobPlan.mockResolvedValue({
    digest: "plan1234567890abcdef",
    output_size: { width: 2560, height: 1440 },
    duration_ms: 500,
    preset: { id: "preset-1", name: "基础2K", revision: 1 },
    source_manifest: {},
  });
  api.getJobPreviews.mockResolvedValue([]);
  api.getAccounts.mockResolvedValue({
    scope: "simulation",
    live_enabled: false,
    accounts: [],
    topic_groups: [],
  });
  api.getTargetStatus.mockResolvedValue({
    state: "NOT_PREPARED",
    targets: [],
    can_submit: false,
  });
  api.getPublicationProfiles.mockResolvedValue([]);
  api.getJobs.mockResolvedValue([]);
  api.getSettings.mockResolvedValue(settings);
  api.getPresets.mockResolvedValue([
    {
      version: {
        id: "preset-1",
        preset_id: "p1",
        revision: 1,
        name: "基础2K",
        schema_version: 1,
        steps: [],
        output: { crf: 20 },
      },
      approved: false,
    },
  ]);
  api.getMediaStatus.mockResolvedValue({
    running: false,
    error_code: null,
    job_id: null,
    publishing_enabled: false,
  });
  api.getCapabilities.mockResolvedValue({
    upload: "UNKNOWN",
    immediate: "UNKNOWN",
    native_schedule: "UNKNOWN",
    quota: "UNKNOWN",
  });
});

describe("local dashboard", () => {
  it("resumes a legacy preview pause only through the explicit Continue action", async () => {
    const { default: App } = await import("../App");
    const legacy = {
      ...job,
      state: "PAUSED",
      pause_reason: "preview_required",
    };
    api.getJobs.mockResolvedValue([legacy]);
    api.resumeJob.mockResolvedValue({ ...legacy, state: "NEW", revision: 2 });
    render(<App />);
    const resume = await screen.findByRole("button", {
      name: "继续",
    });
    expect(resume).toBeEnabled();
    expect(screen.queryByText("等待样片确认")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: job.spec.title }));
    expect(await screen.findByText(/旧版样片暂停/)).toHaveTextContent(
      "无需样片确认",
    );
    expect(api.resumeJob).not.toHaveBeenCalled();
    fireEvent.click(resume);
    await waitFor(() =>
      expect(api.resumeJob).toHaveBeenCalledExactlyOnceWith(legacy),
    );
    expect(api.createPreview).not.toHaveBeenCalled();
    expect(api.approvePreview).not.toHaveBeenCalled();
  });

  it("offers verified READY output before optional review without granting publishing permission", async () => {
    const { default: App } = await import("../App");
    api.getJobs.mockResolvedValue([
      { ...job, state: "READY", output_manifest: { sha256: "verified" } },
    ]);
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: job.spec.title }),
    );
    expect(
      await screen.findByRole("link", { name: "下载已验证成片" }),
    ).toHaveAttribute("href", "/api/jobs/job-1/output");
    const summary = await screen.findByText("可选样片检查");
    expect(summary.closest("details")).not.toHaveAttribute("open");
    // jsdom does not toggle details on summary clicks; set the browser-owned open state.
    summary.closest("details")!.open = true;
    expect(screen.getByRole("button", { name: "生成样片" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "立即发布" })).toBeDisabled();
    expect(screen.getByText("仅本地仿真 · 真实发布未解锁")).toBeVisible();
    expect(api.createPreview).not.toHaveBeenCalled();
    expect(api.approvePreview).not.toHaveBeenCalled();
    expect(api.resumeJob).not.toHaveBeenCalled();
  });

  it("explains source login verification without implying automatic authentication", async () => {
    const { default: App } = await import("../App");
    api.getJobs.mockResolvedValue([
      {
        ...job,
        state: "PAUSED",
        pause_reason: "source_authentication_required",
      },
    ]);
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: job.spec.title }),
    );
    expect(await screen.findByText(/YouTube要求登录验证/)).toHaveTextContent(
      "登录或完成验证",
    );
    expect(screen.getByText(/YouTube要求登录验证/)).toHaveTextContent(
      "手动重试",
    );
    expect(api.createJob).not.toHaveBeenCalled();
    expect(api.resumeJob).not.toHaveBeenCalled();
  });

  it("explains the Chrome helper connection without automatically retrying", async () => {
    const { default: App } = await import("../App");
    api.getJobs.mockResolvedValue([
      {
        ...job,
        state: "PAUSED",
        pause_reason: "source_browser_session_required",
      },
    ]);
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: job.spec.title }),
    );
    expect(await screen.findByText(/Fabu.*YouTube.*助手/)).toHaveTextContent(
      "手动重试",
    );
    expect(api.resumeJob).not.toHaveBeenCalled();
  });

  it("shows unverified capabilities, not publishing success", async () => {
    const { default: App } = await import("../App");
    render(<App />);
    expect(await screen.findByText("发布未解锁")).toBeVisible();
    expect(screen.getByRole("button", { name: "立即发布" })).toBeDisabled();
    expect(screen.getAllByText("未验证").length).toBeGreaterThan(0);
  });
  it("creates a local job and reloads persisted rows", async () => {
    const { default: App } = await import("../App");
    api.createJob.mockResolvedValue(job);
    render(<App />);
    await screen.findByText("发布未解锁");
    fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
    fireEvent.change(screen.getByLabelText("YouTube链接"), {
      target: { value: job.spec.source_url },
    });
    fireEvent.change(screen.getByLabelText("发布标题"), {
      target: { value: job.spec.title },
    });
    api.getJobs.mockResolvedValue([job]);
    fireEvent.click(screen.getByRole("button", { name: "保存本地任务" }));
    await waitFor(() =>
      expect(api.createJob).toHaveBeenCalledWith(
        expect.objectContaining({
          publish_mode: "local",
          title: job.spec.title,
          preset_version_id: "preset-1",
        }),
      ),
    );
    expect(
      await screen.findByRole("button", { name: job.spec.title }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "刷新任务" }));
    expect(
      await screen.findByRole("button", { name: job.spec.title }),
    ).toBeVisible();
  });
  it("preserves last known data when refresh fails", async () => {
    const { default: App } = await import("../App");
    api.getJobs.mockResolvedValue([job]);
    render(<App />);
    await screen.findByRole("button", { name: job.spec.title });
    api.getJobs.mockRejectedValue(new Error("network"));
    fireEvent.click(screen.getByRole("button", { name: "刷新任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("连接");
    expect(screen.getByRole("button", { name: job.spec.title })).toBeVisible();
  });
  it("does not show a misleading active processor", async () => {
    const { default: App } = await import("../App");
    render(<App />);
    await screen.findByText("发布未解锁");
    expect(screen.getByText(/处理引擎未启动/)).toBeVisible();
    expect(screen.getByText(/下载、完整性校验到预设处理/)).toBeVisible();
    expect(screen.queryByText(/从下载、样片确认/)).not.toBeInTheDocument();
  });
  it("shows actual paths in settings", async () => {
    const { default: App } = await import("../App");
    render(<App />);
    await screen.findByText("发布未解锁");
    fireEvent.click(screen.getByRole("button", { name: "运行设置" }));
    expect(screen.getByLabelText("媒体保存目录")).toHaveValue(
      settings.data_dir,
    );
    expect(screen.getByText("3.12.10")).toBeVisible();
  });
});
