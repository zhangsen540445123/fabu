import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
const root = path.resolve(import.meta.dirname, "..");
export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.spec.ts",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:8766", trace: "retain-on-failure" },
  projects: [
    { name: "setup", testMatch: "**/auth.setup.ts" },
    {
      name: "chromium",
      dependencies: ["setup"],
      testMatch: "**/*.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
        storageState: "test-results/auth.json",
        viewport: { width: 1440, height: 1080 },
      },
    },
  ],
  webServer: {
    command: `"${path.join(root, ".venv", "Scripts", "python.exe")}" tests/serve_fixture.py`,
    cwd: import.meta.dirname,
    url: "http://127.0.0.1:8766/api/health",
    reuseExistingServer: false,
    timeout: 20000,
  },
});
