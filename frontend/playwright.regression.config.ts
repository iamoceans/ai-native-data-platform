import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";

// This suite mocks the API and never signs into a running platform stack.
const installedEdge = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
export default defineConfig({
  testDir: "./tests/regression",
  timeout: 20_000,
  expect: { timeout: 5_000 },
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:3018",
    headless: true,
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH
        ?? (existsSync(installedEdge) ? installedEdge : undefined),
    },
  },
  webServer: {
    command: "node node_modules/vite/bin/vite.js --config vite.regression.config.ts --host 127.0.0.1 --port 3018",
    url: "http://127.0.0.1:3018",
    reuseExistingServer: false,
  },
});
