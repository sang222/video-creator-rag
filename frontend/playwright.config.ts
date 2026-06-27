import { defineConfig, devices } from "@playwright/test";

const e2ePort = process.env.VCOS_DASHBOARD_E2E_PORT ?? "3000";
const e2eBaseUrl = process.env.VCOS_DASHBOARD_E2E_BASE_URL ?? `http://127.0.0.1:${e2ePort}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: e2eBaseUrl,
    trace: "on-first-retry"
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ],
  webServer: {
    command: `npm run dev -- -H 127.0.0.1 -p ${e2ePort}`,
    url: e2eBaseUrl,
    reuseExistingServer: !process.env.CI
  }
});
