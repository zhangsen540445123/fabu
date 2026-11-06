import { test as setup, expect } from "@playwright/test";
setup("authenticate isolated fixture once", async ({ request }) => {
  const response = await request.post("/api/session", {
    headers: {
      Origin: "http://127.0.0.1:8766",
      Authorization: "Bearer synthetic-e2e-bootstrap-not-a-real-credential",
    },
  });
  expect(response.status()).toBe(200);
  await request.storageState({ path: "test-results/auth.json" });
});
