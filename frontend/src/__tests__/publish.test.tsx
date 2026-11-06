import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { beijingToUtc, formatBeijing } from "../publishing-time";
import TargetStatus from "../components/TargetStatus";
import PublishForm from "../components/PublishForm";
const api = vi.hoisted(() => ({
  getAccounts: vi.fn(),
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
