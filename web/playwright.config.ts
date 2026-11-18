/**
 * End-to-end flow against the real backend, with files the user supplies.
 *
 *   RFQ_E2E_FILES="/path/a.pdf:/path/b.xlsx" pnpm test:e2e
 *
 * Nothing in the repository is test data: without RFQ_E2E_FILES the suite is
 * skipped and no server is started. With it, the Python API runs (or is
 * reused) on :8011 and the web app is built and served on :3200. The backend's
 * CORS allows http://localhost:3000 and :3200 only.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = 3200;
const API = process.env.RFQ_E2E_API ?? "http://127.0.0.1:8011";
const HAS_FILES = (process.env.RFQ_E2E_FILES ?? "").trim() !== "";

export default defineConfig({
  testDir: "tests/e2e-real",
  // A real analysis takes seconds; a large package may take longer.
  timeout: 240_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  webServer: HAS_FILES
    ? [
        {
          command: `uv run autorfx serve --port ${new URL(API).port || 8011}`,
          cwd: "..",
          url: `${API}/health`,
          timeout: 120_000,
          reuseExistingServer: true,
        },
        {
          command: `pnpm build && pnpm start --port ${PORT}`,
          url: `http://localhost:${PORT}/`,
          timeout: 240_000,
          // NEXT_PUBLIC_* is fixed at build time, so never reuse another build.
          reuseExistingServer: false,
          env: { NEXT_PUBLIC_RFQ_API_BASE: API },
        },
      ]
    : [],
});
