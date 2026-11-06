import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getPresets: vi.fn(),
  getCatalog: vi.fn(),
  getAssets: vi.fn(),
  savePreset: vi.fn(),
  validatePreset: vi.fn(),
  importAsset: vi.fn(),
  getJobPreviews: vi.fn(),
  getJobPlan: vi.fn(),
  createPreview: vi.fn(),
  getOperation: vi.fn(),
  getPreview: vi.fn(),
  approvePreview: vi.fn(),
  resumeJob: vi.fn(),
  createJob: vi.fn(),
}));
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  ...api,
}));
const base = {
  id: "v1",
  preset_id: "p1",
  revision: 1,
  name: "基础2K",
  schema_version: 1,
  steps: [],
  output: { crf: 20 },
};
const catalog = [
  {
    kind: "flip",
    label: "水平翻转",
    scopes: ["main"],
    multiple: false,
    parameters: { properties: {} },
  },
  {
    kind: "layout",
    label: "画布与装饰区",
    scopes: ["main"],
    multiple: false,
    parameters: {
      properties: {
        aspect: {
          type: "string",
          default: "16:9",
          enum: ["16:9", "9:16", "1:1"],
        },
      },
    },
  },
];
const paused = {
  id: "job1",
  revision: 8,
  content_revision: 1,
  state: "PAUSED",
  pause_reason: "preview_required",
  content_frozen: false,
  spec: {
    source_url: "https://youtu.be/L6imSiJLd9Q",
    title: "剪发",
    preset_version_id: "v1",
    publish_mode: "local",
    target_ids: [],
  },
};
beforeEach(() => {
  vi.resetAllMocks();
  api.getPresets.mockResolvedValue([{ version: base, approved: false }]);
  api.getCatalog.mockResolvedValue(catalog);
  api.getAssets.mockResolvedValue([]);
  api.validatePreset.mockImplementation(
    async (doc: { steps: { kind: string; id: string }[] }) => {
      const kinds = doc.steps.map((s) => s.kind);
      const bad =
        kinds.indexOf("layout") >= 0 &&
        kinds.indexOf("flip") > kinds.indexOf("layout");
      return {
        valid: !bad,
        issues: bad
          ? [
              {
                step_id: doc.steps[kinds.indexOf("flip")].id,
                code: "invalid_order",
                message: "步骤顺序不符合处理阶段",
              },
            ]
          : [],
      };
    },
  );
  api.getJobPreviews.mockResolvedValue([]);
});
it("base_preset_preserves_audio_and_frame", async () => {
  const { default: PipelinePage } = await import("../pages/PipelinePage");
  render(<PipelinePage />);
  expect(await screen.findByText("附加处理全部关闭")).toBeVisible();
  expect(screen.getByText(/CRF20/)).toBeVisible();
  expect(api.approvePreview).not.toHaveBeenCalled();
});
it("toggle_reorder_save_and_confirm", async () => {
  const { default: PipelinePage } = await import("../pages/PipelinePage");
  render(<PipelinePage />);
  fireEvent.click(await screen.findByRole("button", { name: "添加水平翻转" }));
  fireEvent.click(screen.getByRole("button", { name: "添加画布与装饰区" }));
  fireEvent.click(screen.getByRole("button", { name: "上移画布与装饰区" }));
  expect(await screen.findByText("步骤顺序不符合处理阶段")).toBeVisible();
  expect(screen.getByRole("button", { name: "保存新版本" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "下移画布与装饰区" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "保存新版本" })).toBeEnabled(),
  );
  api.savePreset.mockResolvedValue({
    ...base,
    id: "v2",
    revision: 2,
    steps: [],
  });
  fireEvent.click(screen.getByRole("button", { name: "保存新版本" }));
  await waitFor(() => expect(api.savePreset).toHaveBeenCalled());
  expect(await screen.findByText(/版本 2 已保存/)).toBeVisible();
  expect(api.approvePreview).not.toHaveBeenCalled();
});
it("preview_confirmation_requires_explicit_user_checkbox", async () => {
  const { default: PreviewPanel } = await import("../components/PreviewPanel");
  api.createPreview.mockResolvedValue({ id: "op1", state: "QUEUED" });
  api.getOperation.mockResolvedValue({
    id: "op1",
    state: "SUCCEEDED",
    result: { preview_id: "preview1" },
  });
  api.getPreview.mockResolvedValue({
    id: "preview1",
    approved: false,
    clips: [
      {
        index: 0,
        start_ms: 0,
        end_ms: 500,
        proxy_label: "观看代理",
        proxy_url: "/proxy",
        official_url: "/official",
      },
    ],
  });
  api.approvePreview.mockResolvedValue({ preview_id: "preview1" });
  api.resumeJob.mockResolvedValue(paused);
  render(<PreviewPanel job={paused} refresh={async () => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "生成样片" }));
  const confirm = await screen.findByRole("button", {
    name: "确认预设并继续处理",
  });
  expect(confirm).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /我已检查正式样片/ }));
  fireEvent.click(confirm);
  await waitFor(() =>
    expect(api.approvePreview).toHaveBeenCalledWith("preview1"),
  );
  expect(api.resumeJob).toHaveBeenCalledWith(paused);
});
it("topics_do_not_select_media_preset", async () => {
  const { default: NewJobPage } = await import("../pages/NewJobPage");
  const alternate = {
    ...base,
    id: "v-alt",
    preset_id: "p-alt",
    name: "我的处理模板",
  };
  render(
    <NewJobPage
      presets={[
        { version: base, approved: false },
        { version: alternate, approved: true },
      ]}
      defaultPreset="v1"
      onClose={() => {}}
      onSaved={async () => {}}
    />,
  );
  fireEvent.change(screen.getByLabelText("处理预设"), {
    target: { value: "v-alt" },
  });
  for (const title of ["剃光头", "剃须仙人的日常", "沉浸式剪发"]) {
    fireEvent.change(screen.getByLabelText("发布标题"), {
      target: { value: title },
    });
    expect(screen.getByLabelText("处理预设")).toHaveValue("v-alt");
  }
});

it("allows the same decoration asset in both bands without duplicate references", async () => {
  const { assetIds } = await import("../components/StepParameters");
  expect(
    assetIds({
      id: "layout",
      kind: "layout",
      enabled: true,
      scope: "main",
      params: { top_image_id: "same-image", bottom_image_id: "same-image" },
      asset_ids: [],
    }),
  ).toEqual(["same-image"]);
});

it("locks editor inputs while an immutable version is being saved", async () => {
  const { default: PipelinePage } = await import("../pages/PipelinePage");
  api.savePreset.mockImplementation(() => new Promise(() => {}));
  render(<PipelinePage />);
  const save = await screen.findByRole("button", { name: "保存新版本" });
  await waitFor(() => expect(save).toBeEnabled());
  fireEvent.click(save);
  expect(screen.getByLabelText("预设名称")).toBeDisabled();
  expect(screen.getByRole("button", { name: "添加水平翻转" })).toBeDisabled();
});
it("does not show the previous content plan while a new revision is being inspected", async () => {
  const { default: JobsPage } = await import("../pages/JobsPage");
  const settings = {
    data_dir: "D:/data",
    state_dir: "C:/state",
    tools_dir: "D:/tools",
    bind_host: "127.0.0.1",
    test_mode: "local" as const,
    default_preset_version_id: "v1",
    credentials: { configured: false },
    environment: { python_version: "3.12", platform: "Windows", paths: {} },
  };
  api.getJobPlan.mockResolvedValue({
    digest: "oldplan1234567890",
    output_size: { width: 2560, height: 1440 },
    duration_ms: 500,
    preset: base,
    source_manifest: { thumbnail: { id: "cover" } },
  });
  const props = {
    settings,
    presets: [{ version: base, approved: false }],
    status: null,
    refresh: async () => {},
  };
  const view = render(<JobsPage {...props} jobs={[paused]} />);
  fireEvent.click(screen.getByRole("button", { name: "剪发" }));
  expect(await screen.findByText(/计划 oldplan/)).toBeVisible();
  api.getJobPlan.mockRejectedValue(new Error("No plan for new revision"));
  view.rerender(
    <JobsPage
      {...props}
      jobs={[{ ...paused, state: "NEW", content_revision: 2, revision: 9 }]}
    />,
  );
  await waitFor(() =>
    expect(screen.queryByText(/计划 oldplan/)).not.toBeInTheDocument(),
  );
});
