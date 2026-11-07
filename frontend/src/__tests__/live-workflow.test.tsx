import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../App";
import NewJobPage from "../pages/NewJobPage";
import PublishForm from "../components/PublishForm";
import TargetStatus from "../components/TargetStatus";
import AccountsPage from "../pages/AccountsPage";
import * as api from "../api";

const account = {
  account_id: "live-bilibili",
  platform: "bilibili",
  nickname: "真实账号",
  active: true,
  category_names: [],
};
const preset = {
  approved: false,
  version: {
    id: "preset-default",
    preset_id: "p",
    revision: 1,
    name: "默认处理",
    schema_version: 1,
    steps: [],
    output: {},
  },
};
const job = {
  id: "new-job",
  revision: 2,
  content_revision: 1,
  state: "READY",
  content_frozen: false,
  pause_reason: null,
  spec: {
    source_url: "https://youtu.be/new-video",
    title: "新的授权视频",
    preset_version_id: preset.version.id,
    target_ids: [],
    publish_mode: "local",
  },
};
const ready = {
  enabled: true,
  status: "ready",
  blockers: [],
  submission_strategy: "per_target",
  daily_submission_limit: 3,
  quota_source: "local_conservative_budget",
  remaining_local_submissions: 3,
  platform_quality_verified: false,
  native_schedule_enabled: false,
};
const liveProfile = {
  id: "live-default",
  name: "默认发布",
  scope: "live",
  execution: "live",
  can_run: true,
  accounts: [account],
  topic_override: null,
};
const target = {
  ...account,
  state: "PROVIDER_ACCEPTED",
  can_retry: false,
  category: [],
  provider_observation: null,
};
const baseRun = {
  scope: "live",
  state: "PRECHECKED",
  snapshot_digest: "digest",
  mode: "immediate",
  targets: [],
  can_submit: true,
  can_reconcile: false,
};
let routes: Record<string, unknown>;
let fetchMock: ReturnType<typeof vi.fn>;
function writes(path: string) {
  return fetchMock.mock.calls.filter(
    ([url, init]) => url === path && ["POST", "PUT"].includes(init?.method),
  );
}
function newTask() {
  return render(
    <NewJobPage
      presets={[preset]}
      defaultPreset={preset.version.id}
      onClose={() => {}}
      onSaved={async () => {}}
    />,
  );
}
function fillTask() {
  fireEvent.change(screen.getByLabelText("YouTube链接"), {
    target: { value: job.spec.source_url },
  });
  fireEvent.change(screen.getByLabelText("发布标题"), {
    target: { value: job.spec.title },
  });
}
beforeEach(() => {
  routes = {
    "/api/session": { csrf_token: "test" },
    "/api/jobs": [job],
    "/api/settings": {
      data_dir: "D:/kept/jobs",
      state_dir: "D:/kept/state",
      tools_dir: "D:/tools",
      bind_host: "127.0.0.1",
      test_mode: "local",
      default_preset_version_id: preset.version.id,
      credentials: { configured: true },
      environment: { python_version: "3.12", platform: "Windows", paths: {} },
    },
    "/api/capabilities": {
      upload: "UNKNOWN",
      immediate: "UNKNOWN",
      quota: "UNKNOWN",
      native_schedule: "UNKNOWN",
    },
    "/api/presets": [preset],
    "/api/media/status": {
      running: false,
      error_code: null,
      job_id: null,
      publishing_enabled: true,
    },
    "/api/publishing/readiness": { ...ready },
    "/api/publishing/connection": {
      configured: true,
      live_enabled: true,
      package_root: "D:/cli",
    },
    "/api/publish/profiles": [liveProfile],
    "/api/publishing/defaults": { profile: liveProfile, mode: "immediate" },
    "/api/workflow/jobs": job,
    "/api/jobs/new-job/publication": {
      source: "default",
      scope: "live",
      mode: "immediate",
      scheduled_at_utc: null,
      topic_override: null,
      topic_selection: { status: "selected", group_name: "日常", tags: [] },
      accounts: [
        {
          account_id: account.account_id,
          declaration: {
            origin: "licensed_reuse",
            statement: "none",
            bilibili_type: "repost",
            confirmed: true,
          },
        },
      ],
    },
    "/api/accounts?scope=live": {
      scope: "live",
      live_enabled: true,
      accounts: [account],
      topic_groups: [],
    },
    "/api/jobs/new-job/targets?scope=live": { ...baseRun },
    "/api/jobs/new-job/submit": { queued: true },
  };
  fetchMock = vi.fn(async (url: string) => {
    const value = routes[url];
    if (value instanceof Error) throw value;
    return new Response(JSON.stringify(value ?? { detail: "not mocked" }), {
      status: value === undefined ? 404 : 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("shows dynamic enabled readiness without promoting UNKNOWN capabilities or provider quota evidence", async () => {
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "自动真实发布已就绪" }),
  ).toBeVisible();
  expect(screen.getAllByText("未验收").length).toBe(4);
  expect(screen.getByText(/本地今日剩余预算/)).toHaveTextContent("3 / 3");
  expect(screen.getByText(/不是官方剩余额度/)).toBeVisible();
  expect(screen.getByText(/三平台分别提交/)).toHaveTextContent("3次");
  expect(screen.queryByText(/此版本不会上传或发布/)).not.toBeInTheDocument();
  expect(writes("/api/jobs/new-job/submit")).toHaveLength(0);
});

it.each([undefined, new Error("offline"), {}, { ...ready, enabled: "true" }])(
  "fails closed for unavailable or malformed readiness without losing jobs/settings: %s",
  async (value) => {
    routes["/api/publishing/readiness"] = value;
    render(<App />);
    expect(
      await screen.findByRole("button", { name: job.spec.title }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "真实发布已阻断" }),
    ).toBeVisible();
    expect(screen.getByText(/readiness_unavailable/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "运行设置" }));
    expect(screen.getByLabelText("媒体保存目录")).toHaveValue("D:/kept/jobs");
  },
);

it("renders concrete blockers and never describes local authorization as operational readiness", async () => {
  routes["/api/publishing/readiness"] = {
    ...ready,
    status: "blocked",
    blockers: ["凭证尚未配置", "local_daily_budget_exhausted"],
    remaining_local_submissions: 0,
  };
  render(<App />);
  expect(await screen.findByText("凭证尚未配置")).toBeVisible();
  expect(screen.getByText("local_daily_budget_exhausted")).toBeVisible();
  expect(screen.getByRole("heading", { name: "真实发布已阻断" })).toBeVisible();
  expect(screen.getByText(/本地今日剩余预算/)).toHaveTextContent("0 / 3");
});

it("refreshes readiness independently when policy changes", async () => {
  render(<App />);
  await screen.findByRole("heading", { name: "自动真实发布已就绪" });
  routes["/api/publishing/readiness"] = {
    ...ready,
    enabled: false,
    status: "blocked",
    blockers: ["live_publish_not_enabled"],
  };
  fireEvent.click(screen.getByRole("button", { name: "刷新发布就绪状态" }));
  expect(await screen.findByText("live_publish_not_enabled")).toBeVisible();
  expect(screen.getByRole("heading", { name: "真实发布已阻断" })).toBeVisible();
});

it("uses saved live defaults and creates the automatic workflow only after an explicit click", async () => {
  newTask();
  const button = await screen.findByRole("button", {
    name: "创建并自动真实发布",
  });
  await waitFor(() => expect(button).toBeEnabled());
  expect(screen.getByLabelText("处理预设")).toHaveValue(preset.version.id);
  expect(screen.getByLabelText("发布流程模板")).toHaveValue(liveProfile.id);
  expect(screen.getByText(/样片检查可选/)).toBeVisible();
  expect(screen.getByText(/B站2秒/)).toBeVisible();
  expect(screen.queryByText(/目前仅预检/)).not.toBeInTheDocument();
  expect(writes("/api/workflow/jobs")).toHaveLength(0);
  fillTask();
  fireEvent.click(button);
  await waitFor(() => expect(writes("/api/workflow/jobs")).toHaveLength(1));
  expect(JSON.parse(writes("/api/workflow/jobs")[0][1].body)).toEqual({
    source_url: job.spec.source_url,
    title: job.spec.title,
    preset_version_id: preset.version.id,
    profile_id: liveProfile.id,
    mode: "immediate",
  });
  expect(writes("/api/jobs/new-job/submit")).toHaveLength(0);
});

it("prefers the current default profile execution over a stale duplicate in the profile list", async () => {
  routes["/api/publish/profiles"] = [
    { ...liveProfile, execution: "preflight_only" },
  ];
  newTask();
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "创建并自动真实发布" }),
    ).toBeEnabled(),
  );
});

it.each([
  undefined,
  { ...ready, enabled: false },
  { ...ready, status: "blocked", blockers: ["tools_missing"] },
])(
  "blocks live workflow creation when readiness is not confirmed: %s",
  async (value) => {
    routes["/api/publishing/readiness"] = value;
    newTask();
    const button = await screen.findByRole("button", {
      name: "创建并自动真实发布",
    });
    fillTask();
    expect(button).toBeDisabled();
    fireEvent.submit(button.closest("form")!);
    expect(writes("/api/workflow/jobs")).toHaveLength(0);
    expect(writes("/api/jobs")).toHaveLength(0);
  },
);

it("calls a preflight-only profile unauthorized, never automatic live publishing", async () => {
  const profile = { ...liveProfile, execution: "preflight_only" };
  routes["/api/publishing/defaults"] = { profile, mode: "immediate" };
  routes["/api/publish/profiles"] = [profile];
  newTask();
  expect(await screen.findByText(/此流程尚未授权真实发布/)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "创建自动流程（真实预检）" }),
  ).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "创建并自动真实发布" }),
  ).not.toBeInTheDocument();
});

it("keeps native scheduling visible but blocks unverified live execution without immediate fallback", async () => {
  newTask();
  const button = await screen.findByRole("button", {
    name: "创建并自动真实发布",
  });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.change(screen.getByLabelText("执行模式"), {
    target: { value: "native_schedule" },
  });
  fireEvent.change(screen.getByLabelText("流程预约北京时间"), {
    target: { value: "2027-01-02T20:30" },
  });
  fillTask();
  expect(screen.getByText(/平台原生定时尚未验收/)).toHaveTextContent(
    "不会回退为立即发布",
  );
  expect(button).toBeDisabled();
  fireEvent.submit(button.closest("form")!);
  expect(writes("/api/workflow/jobs")).toHaveLength(0);
});

it("submits live through the same route with explicit scope and never on environment selection", async () => {
  render(<PublishForm job={job} refresh={async () => {}} />);
  const submit = await screen.findByRole("button", { name: "开始真实发布" });
  await waitFor(() => expect(submit).toBeEnabled());
  expect(screen.getByText(/仅选择真实模式不代表已经发送/)).toBeVisible();
  expect(writes("/api/jobs/new-job/submit")).toHaveLength(0);
  fireEvent.click(submit);
  await waitFor(() =>
    expect(writes("/api/jobs/new-job/submit")).toHaveLength(1),
  );
  expect(JSON.parse(writes("/api/jobs/new-job/submit")[0][1].body)).toEqual({
    snapshot_digest: "digest",
    scope: "live",
  });
});

it.each([
  "connection",
  "can_submit",
  "readiness",
  "native_schedule",
  "scope",
  "command",
])("does not submit a live task without every guard: %s", async (guard) => {
  if (guard === "connection")
    routes["/api/publishing/connection"] = {
      configured: true,
      live_enabled: false,
    };
  if (guard === "readiness") routes["/api/publishing/readiness"] = undefined;
  if (guard === "can_submit")
    routes["/api/jobs/new-job/targets?scope=live"] = {
      ...baseRun,
      can_submit: false,
    };
  if (guard === "native_schedule")
    routes["/api/jobs/new-job/targets?scope=live"] = {
      ...baseRun,
      mode: "native_schedule",
    };
  if (guard === "scope")
    routes["/api/jobs/new-job/targets?scope=live"] = {
      ...baseRun,
      scope: "simulation",
    };
  if (guard === "command")
    routes["/api/jobs/new-job/targets?scope=live"] = {
      ...baseRun,
      command: { state: "RUNNING", error_code: null },
    };
  render(<PublishForm job={job} refresh={async () => {}} />);
  await waitFor(() =>
    expect(screen.getByLabelText("发布环境")).toHaveValue("live"),
  );
  await act(async () => {});
  const submit = screen.queryByRole("button", { name: "开始真实发布" });
  if (submit) {
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
  }
  expect(writes("/api/jobs/new-job/submit")).toHaveLength(0);
  expect(
    screen.queryByRole("button", { name: "开始本地仿真" }),
  ).not.toBeInTheDocument();
});

it("uses the same explicit live HTTP submit contract without changing the simulation default", async () => {
  await api.submitPublication(job.id, "digest", "live");
  await api.submitPublication(job.id, "digest");
  expect(
    writes("/api/jobs/new-job/submit").map(
      ([, init]) => JSON.parse(init.body).scope,
    ),
  ).toEqual(["live", "simulation"]);
});

it.each(["pending", "running", "success", "failed", "unknown"] as const)(
  "renders separate provider %s observation without inventing platform publication or quality acceptance",
  (status) => {
    const observation = {
      status,
      checked_at: "2026-11-08T01:00:00Z",
      next_check_at: "2026-11-08T01:05:00Z",
      platform_content_id: "platform-123",
      reason:
        status === "success"
          ? "provider_reported_success"
          : "provider_tracking_pending",
      quality_status: "unverified",
      expected_duration_s: 120,
      observed_duration_s: 2,
    };
    render(
      <TargetStatus
        targets={[{ ...target, provider_observation: observation }]}
      />,
    );
    const region = screen.getByLabelText("逐平台状态");
    expect(within(region).getByText("已被蚁小二接收")).toBeVisible();
    expect(
      within(region).getByText(
        status === "success"
          ? "服务商报告成功，平台公开状态与成片质量尚未验收"
          : "后台持续跟踪平台结果，无需手工推进",
      ),
    ).toBeVisible();
    expect(within(region).getByText(/平台成片质量未验收/)).toBeVisible();
    expect(within(region).getByText(/预期时长/)).toHaveTextContent(
      "120 秒 / 观察时长：2 秒",
    );
    expect(within(region).getByText(/platform-123/)).toBeVisible();
    expect(within(region).queryByText("平台已公开")).not.toBeInTheDocument();
  },
);

it("shows unavailable provider tracking safely when the observation is null", () => {
  render(<TargetStatus targets={[target]} />);
  expect(screen.getByText(/暂无provider观察/)).toBeVisible();
  expect(screen.queryByText("平台已公开")).not.toBeInTheDocument();
});

it("shows policy-driven connection permission without claiming that credential saving sends content", async () => {
  render(<AccountsPage />);
  expect(await screen.findByText("本机真实发布授权已开启")).toBeVisible();
  expect(screen.getByText(/只读查询，不上传或发布/)).toBeVisible();
  expect(screen.queryByText(/真实发布仍未解锁/)).not.toBeInTheDocument();
});

it("fails closed after target refresh fails rather than retaining a stale submit permission", async () => {
  render(<PublishForm job={job} refresh={async () => {}} />);
  const submit = await screen.findByRole("button", { name: "开始真实发布" });
  await waitFor(() => expect(submit).toBeEnabled());
  routes["/api/jobs/new-job/targets?scope=live"] = new Error(
    "target_status_unavailable",
  );
  expect(
    await screen.findByText(/target_status_unavailable/, {}, { timeout: 2500 }),
  ).toBeVisible();
  const remaining = screen.queryByRole("button", { name: "开始真实发布" });
  if (remaining) expect(remaining).toBeDisabled();
  expect(writes("/api/jobs/new-job/submit")).toHaveLength(0);
});

it("preserves frozen live tasks as read-only and never switches an uncertain target into a new submission", async () => {
  routes["/api/jobs/new-job/targets?scope=live"] = {
    ...baseRun,
    can_submit: false,
    can_reconcile: true,
    reason: "provider_tracking_pending",
    targets: [{ ...target, state: "UNKNOWN" }],
  };
  const frozen = { ...job, content_frozen: true };
  render(<PublishForm job={frozen} refresh={async () => {}} />);
  expect(
    await screen.findByRole("button", { name: "查询对账（不重投）" }),
  ).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "开始真实发布" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /真实续跑/ }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/内容已冻结/)).toBeVisible();
  expect(writes("/api/jobs/new-job/submit")).toHaveLength(0);
});

it("shows accepted frozen jobs as waiting for platform results rather than an attention-required pause", async () => {
  const tracking = {
    ...job,
    state: "PAUSED",
    pause_reason: "provider_tracking_pending",
    content_frozen: true,
    output_manifest: { sha256: "verified" },
    spec: { ...job.spec, publication_profile_id: "live-default" },
  };
  routes["/api/jobs"] = [
    tracking,
    {
      ...job,
      id: "failed-job",
      state: "FAILED",
      pause_reason: "render_failed",
      spec: { ...job.spec, title: "需处理的异常" },
    },
  ];
  routes["/api/jobs/new-job/plan"] = {
    digest: "plan-digest",
    output_size: { width: 1920, height: 1080 },
    duration_ms: 120000,
    preset: preset.version,
    source_manifest: {},
  };
  routes["/api/jobs/new-job/previews"] = [];
  routes["/api/workflow/jobs/new-job/handoff"] = {
    state: "PAUSED",
    error_code: "provider_tracking_pending",
    can_resume: false,
  };
  routes["/api/jobs/new-job/targets?scope=live"] = {
    ...baseRun,
    state: "PAUSED",
    reason: "provider_tracking_pending",
    can_submit: false,
    targets: ["bilibili", "douyin", "toutiao"].map((platform) => ({
      ...target,
      account_id: platform,
      platform,
    })),
  };
  render(<App />);
  const title = await screen.findByRole("button", { name: job.spec.title });
  const row = title.closest("tr")!;
  expect(within(row).getByText("等待平台结果")).toBeVisible();
  expect(
    within(row).getByText("已提交三平台，后台跟踪中；平台成片质量待确认"),
  ).toBeVisible();
  expect(
    screen.getByText("需要关注").closest("article")!.querySelector("strong"),
  ).toHaveTextContent("01");
  expect(
    within(row).queryByRole("button", { name: "继续" }),
  ).not.toBeInTheDocument();
  fireEvent.click(title);
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("等待平台结果")).toBeVisible();
  expect(within(dialog).getByText("跟踪说明")).toBeVisible();
  expect(within(dialog).queryByText("异常原因")).not.toBeInTheDocument();
  await waitFor(() =>
    expect(within(dialog).getAllByText("已被蚁小二接收")).toHaveLength(3),
  );
  expect(
    within(dialog).queryByText(/自动真实流程已暂停/),
  ).not.toBeInTheDocument();
  expect(within(dialog).queryByText("平台已公开")).not.toBeInTheDocument();
  expect(
    within(dialog).queryByRole("button", { name: /生成样片|继续|恢复自动/ }),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).queryByText("更换处理预设（创建新内容版本）"),
  ).not.toBeInTheDocument();
  expect(
    fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
  ).toHaveLength(0);
});

it("does not offer media retry even when a frozen tracking job has no public output manifest", async () => {
  routes["/api/jobs"] = [
    {
      ...job,
      state: "PAUSED",
      pause_reason: "provider_tracking_pending",
      content_frozen: true,
    },
  ];
  render(<App />);
  await screen.findByRole("button", { name: job.spec.title });
  expect(
    screen.queryByRole("button", { name: "继续" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText("需要关注").closest("article")!.querySelector("strong"),
  ).toHaveTextContent("00");
});

it("translates provider_success_unverified without promoting provider acceptance to publication", () => {
  render(
    <TargetStatus
      targets={[
        {
          ...target,
          provider_observation: {
            status: "success",
            reason: "provider_success_unverified",
            quality_status: "unverified",
            checked_at: null,
            next_check_at: null,
            platform_content_id: null,
            expected_duration_s: 120,
            observed_duration_s: 2,
          },
        },
      ]}
    />,
  );
  expect(
    screen.getByText("服务商报告成功，平台公开状态与成片质量尚未验收"),
  ).toBeVisible();
  expect(
    screen.queryByText("provider_success_unverified"),
  ).not.toBeInTheDocument();
  expect(screen.getByText("已被蚁小二接收")).toBeVisible();
  expect(screen.queryByText("平台已公开")).not.toBeInTheDocument();
});

it("uses the server-persisted processing preset as the default for the next new workflow", async () => {
  const nextPreset = {
    ...preset,
    version: { ...preset.version, id: "next-preset", name: "新的默认处理" },
  };
  routes["/api/presets"] = [preset, nextPreset];
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "新建任务" }));
  const create = await screen.findByRole("button", {
    name: "创建并自动真实发布",
  });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.change(screen.getByLabelText("处理预设"), {
    target: { value: nextPreset.version.id },
  });
  fillTask();
  routes["/api/settings"] = {
    ...(routes["/api/settings"] as object),
    default_preset_version_id: nextPreset.version.id,
  };
  fireEvent.click(create);
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(
    JSON.parse(writes("/api/workflow/jobs")[0][1].body).preset_version_id,
  ).toBe(nextPreset.version.id);
  fireEvent.click(screen.getByRole("button", { name: "新建任务" }));
  expect(screen.getByLabelText("处理预设")).toHaveValue(nextPreset.version.id);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "创建并自动真实发布" }),
    ).toBeEnabled(),
  );
  expect(writes("/api/settings")).toHaveLength(0);
});
