import { defineConfig, devices } from "@playwright/test";

// Drives the ALREADY-RUNNING dev server (Vite :5173 + FastAPI :8000). We don't
// auto-start servers here — they're managed for the session. Serial, single
// worker: the backend/DB is shared state and the laptop is low-RAM.
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
