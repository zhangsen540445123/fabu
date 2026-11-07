import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { beijingToUtc, formatBeijing } from "../publishing-time";
import TargetStatus from "../components/TargetStatus";
import PublishForm from "../components/PublishForm";
const api = vi.hoisted(() => ({
  getAccounts: vi.fn(),
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
  vi.clearAllMocks();
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
