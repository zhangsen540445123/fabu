import { test, expect } from "@playwright/test";

test("local dashboard persists tasks, blocks external requests, and adapts to narrow screens", async ({
  page,
  request,
}) => {
  const faults: string[] = [];
  page.on("pageerror", (error) => faults.push(error.message));
  await page.goto("/");
  await expect(page.getByText("发布未解锁")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "立即发布", exact: true }),
  ).toBeDisabled();
  await expect(page).toHaveURL("http://127.0.0.1:8766/");
  await page.screenshot({
    path: "test-results/dashboard-empty.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await page.getByLabel("YouTube链接").fill("https://youtu.be/L6imSiJLd9Q");
  await page.getByLabel("发布标题").fill("剃须仙人的日常 · 本地测试");
  await page.getByRole("button", { name: "保存本地任务" }).click();
  await expect(
    page.getByRole("button", { name: "剃须仙人的日常 · 本地测试" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "剃须仙人的日常 · 本地测试" }),
  ).toBeVisible();
  const external = await request.post("/api/jobs", {
    headers: { Origin: "https://untrusted.invalid" },
    data: {},
  });
  expect(external.status()).toBe(403);
  await page.screenshot({
    path: "test-results/dashboard-task.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "运行设置", exact: true }).click();
  await expect(page.getByLabel("媒体保存目录")).toBeVisible();
  await page.screenshot({ path: "test-results/settings.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "任务工作台" }).click();
  await expect(
    page.getByRole("button", { name: "新建任务", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const titleBox = await page
    .getByRole("button", { name: "剃须仙人的日常 · 本地测试" })
    .boundingBox();
  expect(titleBox?.height).toBeLessThan(45);
  await page.screenshot({ path: "test-results/mobile.png", fullPage: true });
  expect(faults).toEqual([]);
});
