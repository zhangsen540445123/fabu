import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { beijingToUtc, formatBeijing } from "../publishing-time";
import TargetStatus from "../components/TargetStatus";
import PublishForm from "../components/PublishForm";
const api = vi.hoisted(() => ({
  getAccounts: vi.fn(),
  getPublicationForm: vi.fn(),
  getPublicationDefaults: vi.fn(),
  savePublicationDefaults: vi.fn(),
  getWorkflowHandoff: vi.fn(),
  resumeWorkflowHandoff: vi.fn(),
  getPublicationProfiles: vi.fn(),
  createWorkflowJob: vi.fn(),
  getTargetStatus: vi.fn(),
  configurePublication: vi.fn(),
  preflightPublication: vi.fn(),
  submitPublication: vi.fn(),
  reconcilePublication: vi.fn(),
}));
vi.mock("../api", async (original) => ({
  ...(await original<object>()),
  ...api,
}));
const job = {
  id: "job",
  revision: 1,
  content_revision: 1,
  state: "READY",
  content_frozen: false,
  pause_reason: null,
  spec: {
    source_url: "https://youtu.be/L6imSiJLd9Q",
    title: "沉浸式剪发",
    preset_version_id: "preset",
    publish_mode: "local",
    target_ids: [],
  },
};
const target = {
  account_id: "sim-bilibili",
  platform: "bilibili",
  nickname: "枕畔慢时光 · 仿真",
  state: "PENDING",
  can_retry: false,
  category: ["生活经验"],
};
const run = {
  job_id: "job",
  snapshot_digest: "a".repeat(64),
  content_revision: 2,
  scope: "simulation",
  state: "NEW",
  mode: "immediate",
  targets: [target],
  topic: { group_name: "QKHLZMFO", tags: ["解压", "助眠", "ASMR", "剪发"] },
  can_submit: true,
  can_reconcile: false,
};
beforeEach(() => {
  vi.resetAllMocks();
  api.getPublicationDefaults.mockResolvedValue({
    profile: null,
    mode: "immediate",
  });
  api.getPublicationForm.mockImplementation((_id, scope) =>
    Promise.resolve({
      source: "fallback",
      scope: scope ?? "simulation",
      mode: "immediate",
      scheduled_at_utc: null,
      accounts: [],
      topic_override: null,
      topic_selection: {
        status: "selected",
        group_name: "QKHLZMFO",
        tags: ["剪发"],
      },
    }),
  );
  api.getAccounts.mockResolvedValue({
    scope: "simulation",
    live_enabled: false,
    accounts: [
      {
        account_id: target.account_id,
        platform: "bilibili",
        nickname: target.nickname,
        active: true,
        category_names: ["生活经验"],
      },
    ],
    topic_groups: [{ name: "QKHLZMFO", tags: ["剪发"] }],
  });
  api.getTargetStatus.mockResolvedValue({
    ...run,
    state: "NOT_PREPARED",
    targets: [],
    can_submit: false,
  });
  api.configurePublication.mockResolvedValue({ ...job, revision: 2 });
  api.preflightPublication.mockResolvedValue(run);
});
it("schedule_uses_beijing_not_browser_zone", () => {
  expect(beijingToUtc("2027-01-02T20:30")).toBe("2027-01-02T12:30:00.000Z");
  expect(formatBeijing("2027-01-02T12:30:00Z")).toContain("20:30");
  expect(() => beijingToUtc("2027-02-30T20:30")).toThrow();
});
it("provider_accepted_is_not_published", () => {
  render(
    <TargetStatus
      targets={[{ ...target, state: "PROVIDER_ACCEPTED" }]}
      simulation
    />,
  );
  expect(screen.getByText("已被蚁小二接收")).toBeInTheDocument();
  expect(screen.queryByText("平台已公开")).not.toBeInTheDocument();
});
it("unknown_shows_reconcile_not_retry", async () => {
  api.getTargetStatus.mockResolvedValue({
    ...run,
    state: "PAUSED",
    can_submit: false,
    can_reconcile: true,
    targets: [{ ...target, state: "UNKNOWN" }],
  });
  render(<PublishForm job={job} refresh={async () => {}} />);
  expect(
    await screen.findByRole("button", { name: "查询对账（不重投）" }),
  ).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "开始本地仿真" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查询对账（不重投）" }));
  await waitFor(() => expect(api.reconcilePublication).toHaveBeenCalledOnce());
  expect(api.submitPublication).not.toHaveBeenCalled();
});
it("native schedule form submits an explicit UTC instant with simulation scope", async () => {
  render(<PublishForm job={job} refresh={async () => {}} />);
  await screen.findByText("生活经验");
  fireEvent.change(screen.getByLabelText("发布方式"), {
    target: { value: "native_schedule" },
  });
  fireEvent.change(screen.getByLabelText("北京时间（Asia/Shanghai）"), {
    target: { value: "2027-01-02T20:30" },
  });
  fireEvent.click(screen.getByLabelText("我确认来源声明与授权相符"));
  fireEvent.click(screen.getByRole("button", { name: "保存配置并预检" }));
  await waitFor(() => expect(api.configurePublication).toHaveBeenCalledOnce());
  expect(api.configurePublication.mock.calls[0][1]).toMatchObject({
    scope: "simulation",
    mode: "native_schedule",
    scheduled_at_utc: "2027-01-02T12:30:00.000Z",
  });
  expect(api.submitPublication).not.toHaveBeenCalled();
});
it("real account choices require explicit stable-id selection", async () => {
  render(<PublishForm job={job} refresh={async () => {}} />);
  await screen.findByText("生活经验");
  fireEvent.change(screen.getByLabelText("发布环境"), {
    target: { value: "live" },
  });
  await waitFor(() =>
    expect(screen.getByRole("checkbox", { name: /B站/ })).not.toBeChecked(),
  );
});
it("unknown resource upload requires investigation and never offers reupload", async () => {
  api.getTargetStatus.mockResolvedValue({
    ...run,
    state: "PAUSED",
    resource_blocked: true,
    can_submit: false,
    can_reconcile: false,
  });
  render(<PublishForm job={job} refresh={async () => {}} />);
  expect(await screen.findByText(/资源上传结果不明/)).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "开始本地仿真" }),
  ).not.toBeInTheDocument();
});
it("another task owning the source cannot be silently submitted again", async () => {
  api.getTargetStatus.mockResolvedValue({
    ...run,
    source_conflict: true,
    can_submit: false,
    can_reconcile: false,
  });
  render(<PublishForm job={job} refresh={async () => {}} />);
  expect(
    await screen.findByText(/此来源在所选账号已有其他发布任务/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "开始本地仿真" }),
  ).not.toBeInTheDocument();
});
import NewJobPage from "../pages/NewJobPage";
it("new task selects reusable profile and native schedule without later publish click", async () => {
  api.getPublicationProfiles.mockResolvedValue([
    {
      id: "profile",
      name: "日常",
      scope: "simulation",
      can_run: true,
      accounts: [],
    },
  ]);
  api.createWorkflowJob.mockResolvedValue(job);
  render(
    <NewJobPage
      presets={[
        {
          approved: true,
          version: {
            id: "preset",
            preset_id: "p",
            revision: 1,
            name: "基础",
            schema_version: 1,
            steps: [],
            output: {},
          },
        },
      ]}
      defaultPreset="preset"
      onClose={() => {}}
      onSaved={async () => {}}
    />,
  );
  await screen.findByRole("option", { name: "日常 · 仿真" });
  fireEvent.change(screen.getByLabelText("发布流程模板"), {
    target: { value: "profile" },
  });
  fireEvent.change(screen.getByLabelText("YouTube链接"), {
    target: { value: "https://youtu.be/Z6imSiJLd9Q" },
  });
  fireEvent.change(screen.getByLabelText("发布标题"), {
    target: { value: "剃光头" },
  });
  // An available processing preset is required; check state serialization through the form.
  expect(
    screen.getByText(/成片验证完成后自动进入发布仿真/),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("执行模式"), {
    target: { value: "native_schedule" },
  });
  fireEvent.change(screen.getByLabelText("流程预约北京时间"), {
    target: { value: "2027-01-02T20:30" },
  });
  fireEvent.click(screen.getByRole("button", { name: "创建自动流程（仿真）" }));
  await waitFor(() => expect(api.createWorkflowJob).toHaveBeenCalledOnce());
  expect(api.createWorkflowJob.mock.calls[0][0]).toMatchObject({
    profile_id: "profile",
    mode: "native_schedule",
    scheduled_at_utc: "2027-01-02T12:30:00.000Z",
  });
});

it("live reconciliation keeps the selected scope without enabling submission", async () => {
  api.getTargetStatus.mockImplementation((_id, scope) =>
    Promise.resolve({
      ...run,
      scope,
      can_submit: false,
      can_reconcile: true,
      targets: [{ ...target, state: "UNKNOWN" }],
    }),
  );
  render(<PublishForm job={job} refresh={async () => {}} />);
  await screen.findByRole("button", { name: "查询对账（不重投）" });
  fireEvent.change(screen.getByLabelText("发布环境"), {
    target: { value: "live" },
  });
  await waitFor(() =>
    expect(api.getTargetStatus).toHaveBeenLastCalledWith("job", "live"),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "查询对账（不重投）" }),
  );
  await waitFor(() =>
    expect(api.reconcilePublication).toHaveBeenCalledWith(
      "job",
      run.snapshot_digest,
      "live",
    ),
  );
  expect(api.submitPublication).not.toHaveBeenCalled();
});
it("interrupted workflow resumes only after explicit request using current revision", async () => {
  const paused = {
    ...job,
    state: "PAUSED",
    revision: 7,
    pause_reason: "handoff_interrupted",
    spec: { ...job.spec, publication_profile_id: "profile" },
  };
  api.getWorkflowHandoff.mockResolvedValue({
    state: "PAUSED",
    error_code: "handoff_interrupted",
    can_resume: true,
  });
  api.resumeWorkflowHandoff.mockResolvedValue({
    ...paused,
    state: "READY",
    revision: 8,
  });
  const refresh = vi.fn().mockResolvedValue(undefined);
  render(<PublishForm job={paused} refresh={refresh} />);
  const button = await screen.findByRole("button", {
    name: "恢复自动衔接（仿真）",
  });
  expect(api.resumeWorkflowHandoff).not.toHaveBeenCalled();
  fireEvent.click(button);
  await waitFor(() =>
    expect(api.resumeWorkflowHandoff).toHaveBeenCalledWith(paused),
  );
  expect(refresh).toHaveBeenCalled();
  expect(api.submitPublication).not.toHaveBeenCalled();
});
it("workflow with uncertain side effects has no reset button", async () => {
  api.getWorkflowHandoff.mockResolvedValue({
    state: "PAUSED",
    error_code: "workflow_preflight_failed",
    can_resume: false,
  });
  render(
    <PublishForm
      job={{
        ...job,
        state: "PAUSED",
        spec: { ...job.spec, publication_profile_id: "profile" },
      }}
      refresh={async () => {}}
    />,
  );
  await waitFor(() =>
    expect(api.getWorkflowHandoff).toHaveBeenCalledWith("job"),
  );
  expect(
    screen.queryByRole("button", { name: "恢复自动衔接（仿真）" }),
  ).not.toBeInTheDocument();
  expect(api.resumeWorkflowHandoff).not.toHaveBeenCalled();
});
it("reconciliation HTTP body carries explicit live scope", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  const fetcher = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => ({}) });
  vi.stubGlobal("fetch", fetcher);
  try {
    await actual.reconcilePublication("job", run.snapshot_digest, "live");
    expect(fetcher.mock.calls[0][0]).toBe("/api/jobs/job/reconcile");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
      snapshot_digest: run.snapshot_digest,
      scope: "live",
    });
  } finally {
    vi.unstubAllGlobals();
  }
});

const originalAccounts = ["douyin", "toutiao", "bilibili"].map((platform) => ({
  account_id: `real-${platform}`,
  declaration: {
    origin: "self_created",
    confirmed: true,
    statement: platform === "toutiao" ? "self_shot" : "none",
    bilibili_type: platform === "bilibili" ? "self_produced" : null,
  },
}));
function defaultForm(overrides = {}) {
  api.getAccounts.mockResolvedValue({
    scope: "live",
    live_enabled: false,
    accounts: originalAccounts.map((a) => ({
      account_id: a.account_id,
      platform: a.account_id.slice(5),
      nickname: a.account_id,
      active: true,
      category_names: [],
    })),
    topic_groups: [{ name: "QKHLZMFO", tags: ["剪发"] }],
  });
  api.getPublicationForm.mockResolvedValue({
    source: "default",
    scope: "live",
    mode: "immediate",
    scheduled_at_utc: null,
    accounts: originalAccounts,
    topic_override: null,
    topic_selection: {
      status: "selected",
      group_name: "QKHLZMFO",
      tags: ["剪发"],
    },
    ...overrides,
  });
}
it("hydrates all six explicit live defaults without submitting", async () => {
  defaultForm();
  render(<PublishForm job={job} refresh={async () => {}} />);
  await waitFor(() =>
    expect(screen.getByLabelText("发布环境")).toHaveValue("live"),
  );
  expect(screen.getAllByRole("checkbox")).toHaveLength(4);
  screen.getAllByRole("checkbox").forEach((c) => expect(c).toBeChecked());
  expect(screen.getByLabelText("视频来源")).toHaveValue("self_created");
  expect(screen.getByLabelText("话题组")).toHaveValue("");
  expect(screen.getByLabelText("发布方式")).toHaveValue("immediate");
  expect(api.configurePublication).not.toHaveBeenCalled();
  expect(api.submitPublication).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("视频来源"), {
    target: { value: "licensed_reuse" },
  });
  expect(screen.getByLabelText("我确认来源声明与授权相符")).not.toBeChecked();
});
it("loads a saved licensed declaration and schedule instead of replacing it with defaults", async () => {
  defaultForm({
    source: "draft",
    mode: "native_schedule",
    scheduled_at_utc: "2027-01-02T12:30:00Z",
    accounts: [
      {
        account_id: "real-bilibili",
        declaration: {
          origin: "licensed_reuse",
          confirmed: true,
          statement: "none",
          bilibili_type: "repost",
        },
      },
    ],
    topic_override: "QKHLZMFO",
  });
  render(<PublishForm job={job} refresh={async () => {}} />);
  await waitFor(() =>
    expect(screen.getByLabelText("发布方式")).toHaveValue("native_schedule"),
  );
  expect(screen.getByLabelText("视频来源")).toHaveValue("licensed_reuse");
  expect(screen.getByLabelText("北京时间（Asia/Shanghai）")).toHaveValue(
    "2027-01-02T20:30",
  );
  expect(screen.getByLabelText("话题组")).toHaveValue("QKHLZMFO");
  expect(
    screen
      .getAllByRole("checkbox")
      .filter((c) => (c as HTMLInputElement).checked),
  ).toHaveLength(2);
});
it("persists defaults separately from a task and never publishes it", async () => {
  defaultForm();
  api.savePublicationDefaults.mockResolvedValue({
    profile: { id: "default" },
    mode: "immediate",
  });
  render(<PublishForm job={job} refresh={async () => {}} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "保存为新任务默认发布配置" }),
  );
  await waitFor(() =>
    expect(api.savePublicationDefaults).toHaveBeenCalledWith({
      scope: "live",
      mode: "immediate",
      topic_override: null,
      accounts: originalAccounts,
    }),
  );
  expect(api.configurePublication).not.toHaveBeenCalled();
  expect(api.submitPublication).not.toHaveBeenCalled();
});
it("selects default live workflow for new tasks and labels it preflight only", async () => {
  const profile = {
    id: "live-default",
    name: "默认发布配置",
    scope: "live",
    can_run: true,
    execution: "preflight_only",
    accounts: [],
    topic_override: null,
  };
  api.getPublicationProfiles.mockResolvedValue([profile]);
  api.getPublicationDefaults.mockResolvedValue({ profile, mode: "immediate" });
  render(
    <NewJobPage
      presets={[
        {
          approved: false,
          version: {
            id: "preset",
            preset_id: "p",
            revision: 1,
            name: "基础",
            schema_version: 1,
            steps: [],
            output: {},
          },
        },
      ]}
      defaultPreset="preset"
      onClose={() => {}}
      onSaved={async () => {}}
    />,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("发布流程模板")).toHaveValue("live-default"),
  );
  fireEvent.change(screen.getByLabelText("YouTube链接"), {
    target: { value: "https://youtu.be/Z6imSiJLd9Q" },
  });
  fireEvent.change(screen.getByLabelText("发布标题"), {
    target: { value: "剃光头" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "创建自动流程（真实预检）" }),
  );
  await waitFor(() =>
    expect(api.createWorkflowJob).toHaveBeenCalledWith({
      source_url: "https://youtu.be/Z6imSiJLd9Q",
      title: "剃光头",
      preset_version_id: "preset",
      profile_id: "live-default",
      mode: "immediate",
    }),
  );
  expect(api.submitPublication).not.toHaveBeenCalled();
});
it("shows live automatic handoff as blocked not as published", async () => {
  defaultForm({ source: "profile" });
  api.getWorkflowHandoff.mockResolvedValue({
    state: "PAUSED",
    error_code: "live_publish_not_enabled",
    can_resume: false,
  });
  render(
    <PublishForm
      job={{
        ...job,
        spec: { ...job.spec, publication_profile_id: "live-profile" },
      }}
      refresh={async () => {}}
    />,
  );
  expect(await screen.findByText(/成片已自动进入发布预检/)).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "开始本地仿真" }),
  ).not.toBeInTheDocument();
});

it("blocks saving a missing default target instead of silently dropping it", async () => {
  defaultForm({
    accounts: [
      ...originalAccounts,
      { account_id: "missing", declaration: originalAccounts[0].declaration },
    ],
  });
  render(<PublishForm job={job} refresh={async () => {}} />);
  const missing = await screen.findByRole("checkbox", {
    name: /未找到已选账号/,
  });
  expect(missing).toBeChecked();
  fireEvent.click(
    screen.getByRole("button", { name: "保存为新任务默认发布配置" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "目标账号缺失或失效",
  );
  expect(api.savePublicationDefaults).not.toHaveBeenCalled();
  fireEvent.click(missing);
  expect(
    screen.queryByRole("checkbox", { name: /未找到已选账号/ }),
  ).not.toBeInTheDocument();
});
it("keeps per-platform declaration details when saving metadata without a source change", async () => {
  const accounts = [
    {
      account_id: "real-bilibili",
      description: "单独说明",
      declaration: {
        origin: "self_created",
        confirmed: true,
        statement: "ai",
        bilibili_type: "self_produced",
      },
    },
  ];
  defaultForm({ source: "draft", accounts });
  render(<PublishForm job={job} refresh={async () => {}} />);
  await waitFor(() =>
    expect(screen.getByLabelText("发布环境")).toHaveValue("live"),
  );
  fireEvent.click(screen.getByRole("button", { name: "保存配置并预检" }));
  await waitFor(() =>
    expect(api.configurePublication).toHaveBeenCalledWith(
      "job",
      expect.objectContaining({ accounts }),
    ),
  );
});
it("does not create a local-only job if default loading fails", async () => {
  api.getPublicationProfiles.mockResolvedValue([]);
  api.getPublicationDefaults.mockRejectedValue(new Error("offline"));
  render(
    <NewJobPage
      presets={[]}
      defaultPreset=""
      onClose={() => {}}
      onSaved={async () => {}}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "默认发布配置加载失败",
  );
  expect(screen.getByRole("button", { name: "保存本地任务" })).toBeDisabled();
  expect(api.createWorkflowJob).not.toHaveBeenCalled();
});
it("reloads task declarations when a new content revision arrives", async () => {
  defaultForm();
  const { rerender } = render(
    <PublishForm job={job} refresh={async () => {}} />,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("视频来源")).toHaveValue("self_created"),
  );
  defaultForm({
    source: "draft",
    accounts: [
      {
        account_id: "real-bilibili",
        declaration: {
          origin: "licensed_reuse",
          confirmed: true,
          statement: "none",
          bilibili_type: "repost",
        },
      },
    ],
  });
  rerender(
    <PublishForm
      job={{ ...job, revision: 2, content_revision: 2 }}
      refresh={async () => {}}
    />,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("视频来源")).toHaveValue("licensed_reuse"),
  );
  expect(api.configurePublication).not.toHaveBeenCalled();
});
it("offers preflight-only retry for a recoverable live handoff", async () => {
  defaultForm({ source: "profile" });
  api.getWorkflowHandoff.mockResolvedValue({
    state: "PAUSED",
    error_code: "workflow_preflight_failed",
    can_resume: true,
  });
  const refresh = vi.fn().mockResolvedValue(undefined);
  const liveJob = {
    ...job,
    spec: { ...job.spec, publication_profile_id: "live-profile" },
  };
  api.resumeWorkflowHandoff.mockResolvedValue(liveJob);
  render(<PublishForm job={liveJob} refresh={refresh} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "重试发布预检（不上传）" }),
  );
  await waitFor(() =>
    expect(api.resumeWorkflowHandoff).toHaveBeenCalledWith(liveJob),
  );
  expect(api.submitPublication).not.toHaveBeenCalled();
});
