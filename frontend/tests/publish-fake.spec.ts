import { test, expect } from "@playwright/test";
test.use({ timezoneId: "America/New_York" });
test("three-platform simulation keeps partial receipts after refresh and never sends external writes", async ({
  page,
}) => {
  test.setTimeout(90000);
  const external: string[] = [],
    faults: string[] = [];
  page.on("request", (r) => {
    if (!r.url().startsWith("http://127.0.0.1:8766")) external.push(r.url());
  });
  page.on("pageerror", (e) => faults.push(e.message));
  await page.goto("/");
  await page.getByRole("button", { name: "账号与发布", exact: true }).click();
  await expect(
    page.getByText("尚无已同步的账号", { exact: false }),
  ).toBeVisible();
  await page.getByLabel("账号环境").selectOption("simulation");
  await expect(page.getByText("稳定ID：sim-bilibili")).toBeVisible();
  await page.screenshot({
    path: "test-results/accounts-simulation.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "任务工作台", exact: true }).click();
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await page.getByLabel("YouTube链接").fill("https://youtu.be/L6imSiJLd9Q");
  await page.getByLabel("发布标题").fill("沉浸式剪发 · 发布验收");
  await page.getByRole("button", { name: "保存本地任务", exact: true }).click();
  const row = page.getByRole("row").filter({
    has: page.getByRole("button", {
      name: "沉浸式剪发 · 发布验收",
      exact: true,
    }),
  });
  await expect(row).toContainText(/成片就绪|等待样片确认/, { timeout: 20000 });
  await page
    .getByRole("button", { name: "沉浸式剪发 · 发布验收", exact: true })
    .click();
  if (
    await page
      .getByRole("button", { name: "生成样片", exact: true })
      .isVisible()
  ) {
    await page.getByRole("button", { name: "生成样片", exact: true }).click();
    await expect(
      page.getByRole("link", { name: "下载正式HEVC样片" }).first(),
    ).toBeVisible({ timeout: 25000 });
    await page.getByRole("checkbox", { name: /我已检查正式样片/ }).check();
    await page.getByRole("button", { name: "确认预设并继续处理" }).click();
  }
  await expect(page.getByText("本地成片已验证", { exact: true })).toBeVisible({
    timeout: 25000,
  });
  await expect(page.getByText("生活经验", { exact: true })).toBeVisible();
  await page.getByLabel("发布方式").selectOption("native_schedule");
  const future = new Date(Date.now() + 2 * 3600000);
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  })
    .format(future)
    .replace(" ", "T");
  await page.getByLabel("北京时间（Asia/Shanghai）").fill(parts);
  await page.getByLabel("我确认来源声明与授权相符").check();
  const savedRequest = page.waitForRequest(
    (r) => r.method() === "PUT" && r.url().includes("/publication"),
  );
  await page.getByRole("button", { name: "保存配置并预检" }).click();
  const payload = (await savedRequest).postDataJSON();
  expect(payload.scheduled_at_utc).toBe(
    new Date(`${parts}:00+08:00`).toISOString(),
  );
  expect(payload.scope).toBe("simulation");
  await expect(page.getByText(/平台预约：/)).toBeVisible();
  // Verify immediate partial submission independently of the scheduling form.
  await page.getByLabel("发布方式").selectOption("immediate");
  await page.getByRole("button", { name: "保存配置并预检" }).click();
  await expect(page.getByText(/平台预约：/)).not.toBeVisible();
  await page.getByText("保存为可复用发布流程模板", { exact: true }).click();
  await page.getByLabel("流程模板名称").fill("日常发布模板");
  await page.getByRole("button", { name: "保存流程模板", exact: true }).click();
  await expect(
    page.getByText("流程模板已保存，新建任务时可选择并自动衔接。"),
  ).toBeVisible();
  await page.getByRole("button", { name: "开始本地仿真", exact: true }).click();
  await expect(page.getByText("平台已提交", { exact: true })).toBeVisible({
    timeout: 10000,
  });
  await expect(page.getByText("提交失败", { exact: true })).toBeVisible();
  await expect(page.getByText("尚未提交", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "test-results/publish-partial.png",
    fullPage: true,
  });
  await page.reload();
  await page
    .getByRole("button", { name: "沉浸式剪发 · 发布验收", exact: true })
    .click();
  await expect(page.getByText("提交失败", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "续跑已确认未提交的目标" }),
  ).toBeEnabled();
  await expect(page.getByText("平台已公开", { exact: true })).not.toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/publish-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.getByRole("button", { name: "关闭任务详情" }).click();
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await page.getByLabel("YouTube链接").fill("https://youtu.be/Y6imSiJLd9Q");
  await page.getByLabel("发布标题").fill("剃光头 · 自动验收");
  await page
    .getByLabel("发布流程模板")
    .selectOption({ label: "日常发布模板 · 仿真" });
  await page
    .getByRole("button", { name: "创建自动流程（仿真）", exact: true })
    .click();
  await page
    .getByRole("button", { name: "剃光头 · 自动验收", exact: true })
    .click();
  // No post-render preflight or publish click: the approved preset and profile auto-chain.
  await expect(page.getByText("平台已提交", { exact: true })).toHaveCount(3, {
    timeout: 25000,
  });
  await expect(page.getByText(/2aZjVP8u/)).toBeVisible();
  await page.screenshot({
    path: "test-results/workflow-automatic.png",
    fullPage: true,
  });
  expect(external).toEqual([]);
  expect(faults).toEqual([]);
});
