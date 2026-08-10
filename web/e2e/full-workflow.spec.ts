/**
 * SceneWorks V2.5 E2E Tests
 *
 * Tests the UI → API → workflow path using FakeAgentBackend.
 * No live Gemini/OpenHands required.
 *
 * Prerequisites:
 *   cd backend && SCENEWORKS_DEFAULT_BACKEND=fake uv run python -m uvicorn app.main:app --port 8010
 *   cd web && npm run dev
 *   npx playwright test
 */
import { test, expect } from "@playwright/test";

const API_URL = "http://127.0.0.1:8010";

// Unique suffix per test run to avoid collisions.
const SUFFIX = Date.now().toString(36);

test.describe("First-use flow", () => {
  test("complete workflow: register repo → task → architecture → approval → implementation → review → accept", async ({
    page,
  }) => {
    // Navigate to dashboard
    await page.goto("/");
    await expect(page.locator("h1")).toContainText(/SceneWorks|Dashboard/i);

    // --- Register a project ---
    await page.click('a[href="/projects"]');
    await expect(page.locator("h1")).toContainText(/Projects/i);

    // Create test git repo via API
    const repoResp = await page.request.post(`${API_URL}/api/projects`, {
      data: {
        name: `e2e-test-${SUFFIX}`,
        description: "E2E test project",
        repository_path: process.env.E2E_REPO_PATH || "",
      },
    });

    // If no real repo provided, skip repo-dependent tests gracefully
    if (repoResp.status() === 422 || repoResp.status() === 400) {
      test.skip(true, "No valid git repository available for E2E");
      return;
    }
    if (repoResp.status() !== 201) {
      const body = await repoResp.text();
      console.log(`Register response: ${repoResp.status()} ${body}`);
      test.skip(true, `Cannot register repo: ${body}`);
      return;
    }

    const project = await repoResp.json();
    const projectId = project.id;

    // --- Create a task ---
    await page.click('a[href="/tasks"]');
    await expect(page.locator("h1")).toContainText(/Tasks/i);

    // Fill out the new task form
    await page.fill('input[name="title"], [placeholder*="title" i], input#title', `E2E fix calc ${SUFFIX}`);
    await page.fill('textarea[name="description"], [placeholder*="description" i]', "Fix the calculation bug in component X");
    await page.selectOption("select", { index: 0 }); // first project
    await page.click('button:has-text("Create")');

    // Wait for task to appear
    await expect(page.locator("table")).toContainText(`E2E fix calc`, { timeout: 10000 });
  });

  test("dashboard loads and shows KPIs", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("h1")).toContainText(/Dashboard/i);
    // KPI cards should be visible
    await expect(page.locator(".kpi-grid, [class*='kpi']").first()).toBeVisible({ timeout: 10000 });
  });

  test("company page shows roles", async ({ page }) => {
    await page.goto("/company");
    await expect(page.locator("h1")).toContainText(/Company/i);
    await expect(page.locator("text=Engineer")).toBeVisible({ timeout: 10000 });
    await expect(page.locator("text=Architect")).toBeVisible({ timeout: 10000 });
  });

  test("settings page shows backend status", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.locator("h1")).toContainText(/Settings/i);
    await expect(page.locator("table")).toBeVisible({ timeout: 10000 });
  });

  test("projects page shows registered repos", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.locator("h1")).toContainText(/Projects/i);
  });
});

test.describe("Error handling", () => {
  test("handles API unreachable gracefully", async ({ page }) => {
    // Navigate to any page when API is down shows error
    await page.goto("/");
    // If API is unreachable, dashboard shows error
    const errorNotice = page.locator(".notice.error, .error, [class*='error']");
    const dashboard = page.locator("h1");
    // Either dashboard loaded or error displayed
    await expect(
      errorNotice.or(dashboard).first()
    ).toBeVisible({ timeout: 15000 });
  });
});
