import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: "html",
  globalSetup: "./e2e/cleanup-generated-projects.ts",
  globalTeardown: "./e2e/cleanup-generated-projects.ts",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    actionTimeout: 15000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      // Run against the production build by default. `next dev` compiles
      // routes on first visit, which made the first navigation to a page
      // exceed the assertion timeout and fail intermittently — a property of
      // the dev server, not of the app. Set E2E_DEV=1 for the dev server.
      command: process.env.E2E_DEV
        ? "npx next dev --port 3000"
        : "npx next build && npx next start --port 3000",
      port: 3000,
      timeout: 240_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
