import { test, expect } from "@playwright/test";
test("pipeline editor uses backend ordering and completes a real local media workflow", async ({
  page,
}) => {
  test.setTimeout(90000);
  const faults: string[] = [];
  const resumeRequests: string[] = [];
  const approvalRequests: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "POST") return;
    const path = new URL(request.url()).pathname;
    if (/^\/api\/jobs\/[^/]+\/resume$/.test(path)) resumeRequests.push(path);
    if (/^\/api\/previews\/[^/]+\/approve$/.test(path))
      approvalRequests.push(path);
  });
  page.on("pageerror", (e) => faults.push(e.message));
  await page.goto("/");
  await expect(page.locator(".app-shell")).toHaveCSS("display", "flex");
  await page.getByRole("button", { name: "处理流水线", exact: true }).click();
  await expect(page.getByText("附加处理全部关闭")).toBeVisible();
  await page.getByLabel("处理加速").selectOption("auto");
  await page.getByLabel("预设名称").fill("助眠处理 · 验收模板");
  await page.getByRole("button", { name: "添加水平翻转" }).click();
  await page.getByRole("button", { name: "添加画布与装饰区" }).click();
  await page.getByRole("button", { name: "上移画布与装饰区" }).click();
  await expect(
    page.getByText("步骤顺序不符合处理阶段，请手动调整"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "保存新版本" })).toBeDisabled();
  await page.screenshot({
    path: "test-results/pipeline-order-error.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "下移画布与装饰区" }).click();
  await page.getByRole("button", { name: "保存新版本" }).click();
  await expect(page.getByRole("status")).toContainText("版本 2 已保存");
  await page.screenshot({
    path: "test-results/pipeline-editor.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "任务工作台", exact: true }).click();
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await page.getByLabel("YouTube链接").fill("https://youtu.be/L6imSiJLd9Q");
  await page.getByLabel("发布标题").fill("沉浸式剪发 · 流水线验收");
  await page
    .getByLabel("处理预设")
    .selectOption({ label: "助眠处理 · 验收模板 · v2" });
  const created = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/jobs" &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "保存本地任务" }).click();
  const job = await (await created).json();
  const row = page.getByRole("row").filter({
    has: page.getByRole("button", {
      name: "沉浸式剪发 · 流水线验收",
      exact: true,
    }),
  });
  await expect(row.getByText("成片就绪", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  const actualPlan = await (
    await page.request.get(`/api/jobs/${job.id}/plan`)
  ).json();
  expect(actualPlan.execution.path).toBe("cpu");
  expect(actualPlan.execution.reason).toBe("no_device");
  expect(actualPlan.timings.encoding).toBeGreaterThan(0);
  expect(actualPlan.timings.output_validation).toBeGreaterThan(0);
  expect(approvalRequests).toEqual([]);
  expect(resumeRequests).toEqual([]);
  await page
    .getByRole("button", { name: "沉浸式剪发 · 流水线验收", exact: true })
    .click();
  await expect(page.getByText("本地成片已验证", { exact: true })).toBeVisible();
  const output = page.getByRole("link", { name: "下载已验证成片" });
  await expect(output).toBeVisible();
  await expect(output).toHaveAttribute("href", `/api/jobs/${job.id}/output`);
  const outputResponse = await page.request.get(
    (await output.getAttribute("href"))!,
  );
  expect(outputResponse.status()).toBe(200);
  expect(outputResponse.headers()["content-type"]).toContain("video/");
  const previewsResponse = await page.request.get(
    `/api/jobs/${job.id}/previews`,
  );
  expect(previewsResponse.status()).toBe(200);
  expect(await previewsResponse.json()).toEqual([]);
  const readyResponse = await page.request.get(`/api/jobs/${job.id}`);
  expect(readyResponse.status()).toBe(200);
  const ready = await readyResponse.json();
  expect(ready.state).toBe("READY");
  expect(ready.output_manifest).toBeTruthy();
  await page.screenshot({
    path: "test-results/local-output-ready.png",
    fullPage: true,
  });
  const optionalReview = page
    .locator("details")
    .filter({ has: page.getByText("可选样片检查", { exact: true }) });
  await expect(optionalReview).not.toHaveAttribute("open");
  await page.getByText("可选样片检查", { exact: true }).click();
  await page.getByRole("button", { name: "生成样片", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "下载正式HEVC样片" }).first(),
  ).toBeVisible({ timeout: 25000 });
  await expect
    .poll(
      () =>
        page
          .locator("video")
          .first()
          .evaluate((v: HTMLVideoElement) => v.readyState),
      { timeout: 10000 },
    )
    .toBeGreaterThanOrEqual(1);
  await page
    .locator("video")
    .first()
    .evaluate(async (video: HTMLVideoElement) => {
      video.muted = true;
      await video.play();
    });
  await expect
    .poll(() =>
      page
        .locator("video")
        .first()
        .evaluate((video: HTMLVideoElement) => video.currentTime),
    )
    .toBeGreaterThan(0.1);
  await page
    .locator("video")
    .first()
    .evaluate((video: HTMLVideoElement) => {
      video.pause();
      video.muted = false;
    });
  await expect(
    page.getByRole("button", { name: "确认样片", exact: true }),
  ).toBeDisabled();
  await page.screenshot({
    path: "test-results/preview-confirmation.png",
    fullPage: true,
  });
  await page.getByRole("checkbox", { name: /我已检查正式样片/ }).check();
  const approvalResponse = page.waitForResponse(
    (response) =>
      /\/api\/previews\/[^/]+\/approve$/.test(
        new URL(response.url()).pathname,
      ) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "确认样片", exact: true }).click();
  expect((await approvalResponse).status()).toBe(200);
  await expect(
    page.getByRole("button", { name: "样片已确认", exact: true }),
  ).toBeDisabled();
  const afterReviewResponse = await page.request.get(`/api/jobs/${job.id}`);
  expect(afterReviewResponse.status()).toBe(200);
  const afterReview = await afterReviewResponse.json();
  expect(afterReview.state).toBe("READY");
  expect(afterReview.revision).toBe(ready.revision);
  expect(afterReview.output_manifest).toEqual(ready.output_manifest);
  expect(approvalRequests).toHaveLength(1);
  expect(resumeRequests).toEqual([]);
  await expect(page.getByText("本地成片已验证", { exact: true })).toBeVisible({
    timeout: 20000,
  });
  await expect(
    page.getByRole("link", { name: "下载已验证成片" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "立即发布", exact: true }),
  ).toBeDisabled();
  await page.screenshot({
    path: "test-results/optional-preview-approved.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "关闭任务详情" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "处理流水线", exact: true }).click();
  await expect(page.getByRole("button", { name: "保存新版本" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/pipeline-mobile.png",
    fullPage: true,
  });
  expect(faults).toEqual([]);
});
