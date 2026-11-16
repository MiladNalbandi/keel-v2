// keel v2 daily-use journeys. One worker, files in name order (the journeys share one throw-away keel; the backup
// journey stops and starts it, so it runs last). Everything env-driven by run.sh: KEEL_URL, KEEL_KEY, ...
import { defineConfig } from "@playwright/test";
import path from "node:path";

const out = process.env.KEEL_E2E_OUT || path.join(__dirname, "report");

export default defineConfig({
  testDir: "./tests",
  outputDir: path.join(out, "artifacts"),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 15_000 },
  reporter: [
    ["list"],
    ["html", { outputFolder: path.join(out, "html"), open: "never" }],
    ["./reporter.mjs", { out }],
  ],
  use: {
    baseURL: process.env.KEEL_URL || "http://127.0.0.1:18188",
    viewport: { width: 1360, height: 860 },
    colorScheme: "light",
    screenshot: "on",
    trace: "retain-on-failure",
    actionTimeout: 15_000,
  },
  // KEEL_E2E_CHROMIUM: a Chromium that is already on this computer (no `npx playwright install chromium`)
  projects: [
    {
      name: "chromium",
      use: {
        browserName: "chromium",
        ...(process.env.KEEL_E2E_CHROMIUM ? { launchOptions: { executablePath: process.env.KEEL_E2E_CHROMIUM } } : {}),
      },
    },
  ],
});
