import { defineConfig, devices } from "@playwright/test";

function isolatedPort(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const value = Number.parseInt(raw, 10);
  if (!Number.isInteger(value) || value < 1_024 || value > 65_535) {
    throw new Error(`${name} must be a TCP port between 1024 and 65535`);
  }
  return value;
}

const backendPort = isolatedPort("PLAYWRIGHT_BACKEND_PORT", 8101);
const frontendPort = isolatedPort("PLAYWRIGHT_FRONTEND_PORT", 3100);
const backendBaseUrl = `http://127.0.0.1:${backendPort}`;

export default defineConfig({
  testDir: "./tests/e2e",
  // The suites share one Next dev server; parallel cold compilation causes
  // nondeterministic navigation and loading-state failures. Keep the release
  // gate serial so a green run reflects product behavior, not server races.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["html", { open: "never" }], ["junit", { outputFile: "playwright-results.xml" }]] : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || `http://127.0.0.1:${frontendPort}`,
    locale: "en-US",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : undefined,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.PLAYWRIGHT_BASE_URL ? undefined : [
    {
      command: `cd .. && TEST_TRAITTUTOR_HOME="$(mktemp -d)/traittutor-e2e" && AUTH_ENABLED=false TRAITTUTOR_HOME="$TEST_TRAITTUTOR_HOME" .venv/bin/python -m uvicorn traittutor.api.main:app --host 127.0.0.1 --port ${backendPort}`,
      url: `${backendBaseUrl}/`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `AUTH_ENABLED=false TRAITTUTOR_AUTH_ENABLED=false BACKEND_PORT=${backendPort} TRAITTUTOR_API_BASE_URL=${backendBaseUrl} NEXT_PUBLIC_API_BASE=${backendBaseUrl} NEXT_DIST_DIR=.next-playwright-${frontendPort} npm run dev -- --webpack --port ${frontendPort}`,
      url: `http://127.0.0.1:${frontendPort}`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
