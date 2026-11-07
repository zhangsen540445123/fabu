import { defineConfig } from "@playwright/test";
import os from "node:os";
import path from "node:path";

// Deliberately separate from the fixture-server suite: no server or real API.
export default defineConfig({
  testDir: import.meta.dirname,
  testMatch: "live-workflow-ui.spec.ts",
  workers: 1,
  retries: 0,
  reporter: "list",
  outputDir: path.join(os.tmpdir(), "fabu-live-workflow-ui-results"),
  use: { browserName: "chromium", trace: "off" },
  projects: [
    {
      name: "desktop-mocked",
      use: { viewport: { width: 1440, height: 1080 } },
    },
    { name: "mobile-mocked", use: { viewport: { width: 390, height: 844 } } },
  ],
});
