import { test, expect } from "@playwright/test";
test("unknown publication survives reload and only allows reconciliation", async ({
  page,
}) => {
  test.setTimeout(90000);
  const external: string[] = [];
  page.on("request", (r) => {
    if (!r.url().startsWith("http://127.0.0.1:8766")) external.push(r.url());
  });
  await page.goto("/");
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await page.getByLabel("YouTube链接").fill("https://youtu.be/Z6imSiJLd9Q");
  await page.getByLabel("发布标题").fill("沉浸式剪发 · 恢复验收");
  await page.getByRole("button", { name: "保存本地任务", exact: true }).click();
  const row = page.getByRole("row").filter({
    has: page.getByRole("button", {
      name: "沉浸式剪发 · 恢复验收",
      exact: true,
    }),
  });
  await expect(row).toContainText(/成片就绪|等待样片确认/, { timeout: 20000 });
  await page
    .getByRole("button", { name: "沉浸式剪发 · 恢复验收", exact: true })
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
  await page.getByLabel("我确认来源声明与授权相符").check();
  await page.getByRole("button", { name: "保存配置并预检" }).click();
  await page.getByRole("button", { name: "开始本地仿真", exact: true }).click();
  await expect(
    page.getByText("结果不明 · 需要对账", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "开始本地仿真", exact: true }),
  ).not.toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: "沉浸式剪发 · 恢复验收", exact: true })
    .click();
  await expect(
    page.getByText("结果不明 · 需要对账", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查询对账（不重投）" }).click();
  await expect(
    page.getByRole("button", { name: "查询对账（不重投）" }),
  ).toBeEnabled();
  await expect(
    page.getByText("结果不明 · 需要对账", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "续跑已确认未提交的目标" }),
  ).not.toBeVisible();
  await expect(page.getByText("尚未提交", { exact: true })).toHaveCount(2);
  await page.screenshot({
    path: "test-results/recovery-unknown.png",
    fullPage: true,
  });
  expect(external).toEqual([]);
});
