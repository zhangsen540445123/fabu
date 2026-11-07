import { test, expect } from "@playwright/test";

test("explicit live defaults survive reload and processed media stops at guarded preflight", async ({
  page,
}) => {
  test.setTimeout(90000);
  const posts: string[] = [],
    faults: string[] = [];
  page.on("request", (r) => {
    if (
      r.method() === "POST" &&
      /\/(submit|approve)$/.test(new URL(r.url()).pathname)
    )
      posts.push(r.url());
  });
  page.on("pageerror", (e) => faults.push(e.message));
  await page.goto("/");
  // Earlier browser tests may leave local rendering in progress. Wait for natural completion
  // before asserting preservation; saving defaults must not interrupt those jobs.
  await expect
    .poll(
      async () => {
        const jobs = await (await page.request.get("/api/jobs")).json();
        return jobs.some((j: { state: string }) =>
          [
            "NEW",
            "INSPECTING",
            "DOWNLOADING",
            "PROCESSING",
            "PUBLISHING",
          ].includes(j.state),
        );
      },
      { timeout: 30000 },
    )
    .toBe(false);
  const old = await (await page.request.get("/api/jobs")).json();
  const csrf = (await (await page.request.get("/api/session")).json())
    .csrf_token;
  const catalogue = await (
    await page.request.get("/api/accounts?scope=live")
  ).json();
  const defaults = await page.request.put("/api/publishing/defaults", {
    headers: { Origin: "http://127.0.0.1:8766", "X-CSRF-Token": csrf },
    data: {
      scope: "live",
      mode: "immediate",
      topic_override: null,
      accounts: catalogue.accounts.map(
        (a: { account_id: string; platform: string }) => ({
          account_id: a.account_id,
          declaration: {
            origin: "self_created",
            confirmed: true,
            statement: a.platform === "toutiao" ? "self_shot" : "none",
            bilibili_type: a.platform === "bilibili" ? "self_produced" : null,
          },
        }),
      ),
    },
  });
  expect(defaults.status()).toBe(200);
  const profileId = (await defaults.json()).profile.id;
  await page.reload();
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await expect(page.getByLabel("发布流程模板")).toHaveValue(profileId);
  await page.getByLabel("YouTube链接").fill("https://youtu.be/Z6imSiJLd9Q");
  await page.getByLabel("发布标题").fill("剃光头 · 默认配置验收");
  const created = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/workflow/jobs") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "创建自动流程（真实预检）" }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const job = await response.json();
  expect(job.spec.target_ids).toHaveLength(3);
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get(`/api/workflow/jobs/${job.id}/handoff`)
          ).json()
        ).error_code,
      { timeout: 40000 },
    )
    .toBe("live_publish_not_enabled");
  await page
    .getByRole("button", { name: "剃光头 · 默认配置验收", exact: true })
    .click();
  await expect(page.getByText(/成片已自动进入发布预检/)).toBeVisible();
  await expect(page.getByLabel("发布环境")).toHaveValue("live");
  await expect(page.getByLabel("视频来源")).toHaveValue("self_created");
  await expect(page.getByLabel("我确认来源声明与授权相符")).toBeChecked();
  await expect(page.getByLabel("发布方式")).toHaveValue("immediate");
  await expect(page.getByLabel("话题组")).toHaveValue("");
  const panel = page.locator(".publication-panel");
  await expect(panel.getByRole("checkbox", { checked: true })).toHaveCount(4);
  await page.screenshot({
    path: "test-results/live-defaults-preflight.png",
    fullPage: true,
  });
  await page.getByLabel("发布方式").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "test-results/live-defaults-fields.png",
    fullPage: true,
  });
  await page.reload();
  await page
    .getByRole("button", { name: "剃光头 · 默认配置验收", exact: true })
    .click();
  await expect(page.getByLabel("我确认来源声明与授权相符")).toBeChecked();
  const run = await (
    await page.request.get(`/api/jobs/${job.id}/targets?scope=live`)
  ).json();
  expect(run.state).toBe("NOT_PREPARED");
  expect(run.can_submit).toBe(false);
  for (const prior of old) {
    expect(
      await (await page.request.get(`/api/jobs/${prior.id}`)).json(),
    ).toEqual(prior);
  }
  expect(posts).toEqual([]);
  expect(faults).toEqual([]);
});
