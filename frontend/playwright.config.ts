import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs against its own database and its own ports, never the demo data.
 * `scripts/e2e-server.mjs` migrates that database, seeds it, and starts the API, the
 * worker and the web UI; it tears all three down afterwards.
 */
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);

// A proxy in the environment would otherwise swallow the loopback requests that both the
// readiness check and the browser make.
const loopback = "127.0.0.1,localhost";
process.env.NO_PROXY = process.env.NO_PROXY ? `${process.env.NO_PROXY},${loopback}` : loopback;
process.env.no_proxy = process.env.NO_PROXY;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    locale: "fa-IR",
    timezoneId: "Asia/Tehran",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    {
      name: "mobile",
      use: { ...devices["Desktop Chrome"], viewport: { width: 360, height: 780 } },
    },
  ],
  webServer: {
    command: "node scripts/e2e-server.mjs",
    env: { NO_PROXY: process.env.NO_PROXY, no_proxy: process.env.NO_PROXY },
    url: `http://127.0.0.1:${WEB_PORT}`,
    reuseExistingServer: false,
    timeout: 240_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
