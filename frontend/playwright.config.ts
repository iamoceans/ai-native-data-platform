import { defineConfig } from "@playwright/test";

/**
 * E2E tests target a running core stack. Set E2E_BASE_URL (for example
 * http://127.0.0.1:3000) to enable them; without it the suite is skipped and
 * reported as not executed.
 */
const baseURL = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: baseURL ?? "http://127.0.0.1:3000",
    headless: true,
    trace: "retain-on-failure",
  },
});
