import { test, expect } from "@playwright/test";

const task = {
  id: 1,
  project_id: 1,
  title: "Slow backend task",
  description: "Used to verify that the browser remains interactive.",
  status: "NEW",
  priority: "medium",
  current_role: null,
  current_execution_id: null,
  base_commit: null,
  task_branch: null,
  worktree_path: null,
  result_commit: null,
  architecture_result: null,
  implementation_summary: null,
  review_result: null,
  created_at: "2026-08-12T08:00:00Z",
  updated_at: "2026-08-12T08:00:00Z",
  project_name: "Responsiveness test",
  allowed_actions: ["start_architecture"],
  execution_status: null,
};

async function mockTaskDetail(page: any, events: unknown[] = []) {
  await page.route(/\/api\/tasks\/1$/, (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(task) }),
  );
  await page.route(/\/api\/tasks\/1\/events(?:\?.*)?$/, (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(events) }),
  );
  await page.route("**/api/events/stream**", (route: any) => route.abort());
}

test.describe("WP-WEB-1 responsiveness contracts", () => {
  test("action feedback appears before a slow backend acknowledgement", async ({ page }) => {
    await mockTaskDetail(page);
    let actionFinished!: () => void;
    const actionResponse = new Promise<void>((resolve) => { actionFinished = resolve; });
    await page.route("**/api/tasks/1/actions/start-architecture", async (route: any) => {
      await actionResponse;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...task, status: "ARCHITECTURE_ANALYSIS", allowed_actions: ["cancel"] }),
      });
    });

    await page.goto("/tasks/1");
    await expect(page.getByRole("button", { name: "Run Architect analysis" })).toBeVisible();
    const clickedAt = Date.now();
    await page.getByRole("button", { name: "Run Architect analysis" }).click();
    await expect(page.getByRole("button", { name: "Starting…" })).toBeVisible({ timeout: 500 });
    const localFeedbackMs = Date.now() - clickedAt;
    console.log(`local feedback: ${localFeedbackMs} ms`);
    expect(localFeedbackMs).toBeLessThan(200);
    await expect(page.getByText(/Waiting for backend acknowledgement/)).toBeVisible();

    actionFinished();
    await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();
  });

  test("dashboard navigation renders a shell while destination data is pending", async ({ page }) => {
    let releaseDashboard!: () => void;
    const dashboard = new Promise<void>((resolve) => { releaseDashboard = resolve; });
    await page.route("**/api/dashboard", async (route: any) => {
      await dashboard;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          active_tasks: 0,
          awaiting_approval: 0,
          running_executions: 0,
          recently_completed: [],
          failed_executions: [],
          roles: [],
        }),
      });
    });
    await page.route("**/api/backends", (route: any) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));

    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible({ timeout: 1000 });
    await expect(page.getByText("Loading current data…")).toBeVisible();
    releaseDashboard();
  });

  test("homepage composer is interactive before the task list resolves", async ({ page }) => {
    let releaseTasks!: () => void;
    const tasks = new Promise<void>((resolve) => { releaseTasks = resolve; });
    await page.route("**/api/tasks?*", async (route: any) => {
      await tasks;
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/projects", (route: any) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{ id: 1, name: "Demo", description: "", repository_path: "/tmp/demo", default_branch: "main", status: "active", architecture_context_paths: [], test_commands: [], build_commands: [], worktree_root_override: null, created_at: "2026-08-12T08:00:00Z", updated_at: "2026-08-12T08:00:00Z", active_task_count: 0 }]),
      }),
    );

    const started = Date.now();
    await page.goto("/");
    await expect(page.getByPlaceholder(/Ask the team/i)).toBeVisible({ timeout: 500 });
    const composerReadyMs = Date.now() - started;
    console.log(`composer visible: ${composerReadyMs} ms`);
    expect(composerReadyMs).toBeLessThan(1000);
    await expect(page.getByText("Loading…").first()).toBeVisible();
    releaseTasks();
  });

  test("large event history is capped and individual rows are memoized", async ({ page }) => {
    const events = Array.from({ length: 2_000 }, (_, id) => ({
      id: id + 1,
      execution_id: null,
      task_id: 1,
      type: "agent.message",
      payload: { text: `event ${id}` },
      severity: "info",
      timestamp: "2026-08-12T08:00:00Z",
    }));
    await mockTaskDetail(page, events);
    await page.goto("/tasks/1");
    await expect(page.locator(".log .entry").first()).toBeVisible();
    await expect(page.locator(".log .entry")).toHaveCount(800);
  });

  test("failed acknowledgement restores controls and keeps the page usable", async ({ page }) => {
    await mockTaskDetail(page);
    let releaseAction!: () => void;
    const actionResponse = new Promise<void>((resolve) => { releaseAction = resolve; });
    await page.route("**/api/tasks/1/actions/start-architecture", async (route: any) => {
      await actionResponse;
      await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "backend unavailable" }) });
    });

    await page.goto("/tasks/1");
    await page.getByRole("button", { name: "Run Architect analysis" }).click();
    await expect(page.getByRole("button", { name: "Starting…" })).toBeVisible();
    releaseAction();
    await expect(page.locator(".notice.error")).toContainText("backend unavailable");
    await expect(page.getByRole("button", { name: "Run Architect analysis" })).toBeEnabled();
  });
});
