import { test, expect, type Page } from "@playwright/test";
import { build } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const origin = "http://fabu-ui.test";
const files = new Map<string, string | Uint8Array>();
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
const profile = {
  id: "default-live",
  name: "默认真实发布",
  scope: "live",
  execution: "live",
  can_run: true,
  accounts: [],
  topic_override: null,
};

test.beforeAll(async () => {
  // Production bundle is kept in memory; no dist replacement or dev server.
  const bundle = await build({
    configFile: false,
    root: path.resolve(import.meta.dirname, ".."),
    plugins: [react()],
    logLevel: "error",
    build: { write: false },
  });
  for (const result of Array.isArray(bundle) ? bundle : [bundle]) {
    if (!("output" in result)) throw new Error("Unexpected build output");
    for (const item of result.output)
      files.set(
        `/${item.fileName}`,
        item.type === "chunk" ? item.code : item.source,
      );
  }
});

async function mockApp(page: Page, readinessAvailable = true) {
  const requests: { path: string; method: string; body: unknown }[] = [];
  const unexpected: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const responses: Record<string, unknown> = {
    "/api/session": { csrf_token: "mock-only" },
    "/api/jobs": [],
    "/api/settings": {
      data_dir: "D:/existing/jobs",
      state_dir: "D:/existing/state",
      tools_dir: "D:/tools",
      test_mode: "local",
      bind_host: "127.0.0.1",
      default_preset_version_id: "preset",
      credentials: { configured: true },
      environment: { python_version: "3.12", platform: "Windows", paths: {} },
    },
    "/api/capabilities": {
      upload: "UNKNOWN",
      immediate: "UNKNOWN",
      quota: "UNKNOWN",
      native_schedule: "UNKNOWN",
    },
    "/api/presets": [
      {
        approved: false,
        version: {
          id: "preset",
          preset_id: "p",
          revision: 1,
          schema_version: 1,
          name: "默认处理预设",
          steps: [],
          output: {},
        },
      },
    ],
    "/api/media/status": {
      running: false,
      error_code: null,
      job_id: null,
      publishing_enabled: true,
    },
    "/api/publishing/readiness": readinessAvailable ? ready : undefined,
    "/api/publish/profiles": [profile],
    "/api/publishing/defaults": { profile, mode: "immediate" },
    "/api/workflow/jobs": { id: "mock-created", revision: 1 },
  };
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) {
      unexpected.push(request.url());
      return route.abort();
    }
    if (url.pathname.startsWith("/api/")) {
      requests.push({
        path: url.pathname,
        method: request.method(),
        body: request.postDataJSON(),
      });
      const data = responses[url.pathname];
      return route.fulfill({
        status: data === undefined ? 404 : 200,
        contentType: "application/json",
        body: JSON.stringify(data ?? { detail: "not mocked" }),
      });
    }
    const name = url.pathname === "/" ? "/index.html" : url.pathname;
    const body = files.get(name);
    if (body === undefined) {
      unexpected.push(name);
      return route.abort();
    }
    return route.fulfill({
      body: typeof body === "string" ? body : Buffer.from(body),
      contentType: name.endsWith(".html")
        ? "text/html"
        : name.endsWith(".css")
          ? "text/css"
          : "application/javascript",
    });
  });
  await page.goto(`${origin}/`);
  return { requests, unexpected, errors };
}

test("default live workflow is explicit, scheduling fails closed, and layout fits", async ({
  page,
}, testInfo) => {
  const { requests, unexpected, errors } = await mockApp(page);
  await expect(
    page.getByRole("heading", { name: "自动真实发布已就绪" }),
  ).toBeVisible();
  await expect(page.getByText("未验收", { exact: true })).toHaveCount(4);
  await expect(page.getByText(/不是官方剩余额度/)).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("dashboard.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("处理预设")).toHaveValue("preset");
  await expect(dialog.getByLabel("发布流程模板")).toHaveValue("default-live");
  const create = dialog.getByRole("button", {
    name: "创建并自动真实发布",
    exact: true,
  });
  await expect(create).toBeEnabled();
  await dialog.getByLabel("YouTube链接").fill("https://youtu.be/NEW-UI-ONLY");
  await dialog.getByLabel("发布标题").fill("仅浏览器模拟的新流程");
  await dialog.getByLabel("执行模式").selectOption("native_schedule");
  await expect(dialog.getByText(/不会回退为立即发布/)).toBeVisible();
  await expect(create).toBeDisabled();
  expect(requests.filter((r) => r.method === "POST")).toEqual([]);
  await dialog.getByLabel("执行模式").selectOption("immediate");
  await expect(create).toBeEnabled();
  const box = await dialog.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.screenshot({
    path: testInfo.outputPath("new-live-workflow.png"),
    fullPage: true,
  });
  await create.click();
  await expect(dialog).not.toBeVisible();
  const writes = requests.filter((r) => r.method === "POST");
  expect(writes).toEqual([
    {
      path: "/api/workflow/jobs",
      method: "POST",
      body: {
        source_url: "https://youtu.be/NEW-UI-ONLY",
        title: "仅浏览器模拟的新流程",
        preset_version_id: "preset",
        profile_id: "default-live",
        mode: "immediate",
      },
    },
  ]);
  expect(unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

test("missing readiness preserves settings and blocks the default live action", async ({
  page,
}) => {
  const { requests, unexpected, errors } = await mockApp(page, false);
  await expect(
    page.getByRole("heading", { name: "每条视频，都有迹可循。" }),
  ).toBeVisible();
  await expect(page.getByText(/readiness_unavailable/)).toBeVisible();
  await page.getByRole("button", { name: "运行设置", exact: true }).click();
  await expect(page.getByLabel("媒体保存目录")).toHaveValue("D:/existing/jobs");
  await page.getByRole("button", { name: "任务工作台", exact: true }).click();
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "创建并自动真实发布", exact: true }),
  ).toBeDisabled();
  expect(requests.filter((r) => r.method === "POST")).toEqual([]);
  expect(unexpected).toEqual([]);
  expect(errors).toEqual([]);
});
