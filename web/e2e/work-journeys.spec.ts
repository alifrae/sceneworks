/**
 * WP-WEB-2 product journeys (browser-level, mocked API).
 *
 * These exercise the conversation-first UX end to end in a real browser:
 * the homepage composer, the Work Thread, decision cards, and failure
 * states. Full agent-run coverage against the fake backend already exists
 * in full-workflow.spec.ts; these tests mock the API so each journey is
 * fast and deterministic, matching the style established by
 * responsiveness.spec.ts.
 */
import { test, expect } from "@playwright/test";

const PROJECT = {
  id: 1,
  name: "Demo project",
  description: "",
  repository_path: "/tmp/demo",
  default_branch: "main",
  status: "active",
  architecture_context_paths: [],
  test_commands: [],
  build_commands: [],
  worktree_root_override: null,
  created_at: "2026-08-12T08:00:00Z",
  updated_at: "2026-08-12T08:00:00Z",
  active_task_count: 0,
};

function baseTask(overrides: Record<string, unknown>) {
  return {
    id: 1,
    project_id: 1,
    title: "Investigate startup regression",
    description: "Find why startup went from 8s to 30s and fix it.",
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
    project_name: "Demo project",
    allowed_actions: [],
    execution_status: null,
    ...overrides,
  };
}

async function mockCommon(page: any) {
  await page.route("**/api/projects", (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([PROJECT]) }),
  );
}

function stageBadge(page: any) {
  // Some stage labels ("Implementing") are shared with a progress-step
  // label, so assertions must target the badge specifically, not any text
  // on the page.
  return page.locator(".stage-badge");
}

async function mockTaskEndpoints(page: any, taskId: number, task: Record<string, unknown>, events: unknown[] = []) {
  await page.route(new RegExp(`/api/tasks/${taskId}$`), (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(task) }),
  );
  await page.route(new RegExp(`/api/tasks/${taskId}/events(\\?.*)?$`), (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(events) }),
  );
  await page.route(new RegExp(`/api/tasks/${taskId}/diff`), (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ stat: "", full: "", commits: [], status: "", error: "no worktree exists for this task yet" }) }),
  );
  await page.route(`**/api/executions?task_id=${taskId}`, (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/events/stream**", (route: any) => route.abort());
}

test.describe("Journey A — start work", () => {
  test("submitting the composer opens a Work Thread", async ({ page }) => {
    await mockCommon(page);

    await page.goto("/");
    await expect(page.getByPlaceholder(/Ask the team/i)).toBeVisible();

    const created = baseTask({ id: 501, title: "Fix flaky login test".slice(0, 120) });
    await page.route("**/api/tasks", async (route: any) => {
      if (route.request().method() === "POST") {
        return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(created) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/tasks/501/actions/start-architecture", (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...created, status: "ARCHITECTURE_ANALYSIS", allowed_actions: ["cancel"] }) }),
    );
    await mockTaskEndpoints(page, 501, { ...created, status: "ARCHITECTURE_ANALYSIS", current_role: "triage", allowed_actions: ["cancel"] });

    await page.getByPlaceholder(/Ask the team/i).fill("Fix flaky login test");
    await page.getByRole("button", { name: "Send" }).click();

    await expect(page).toHaveURL(/\/work\/501$/);
    await expect(stageBadge(page)).toHaveText("Investigating");
    await expect(page.getByText("Find why startup went from 8s to 30s and fix it.")).toBeVisible();
  });
});

test.describe("Journey B — follow active work", () => {
  test("a simulated workflow event updates stage and owner without reload", async ({ page }) => {
    let calls = 0;
    await page.route(/\/api\/tasks\/502$/, (route: any) => {
      calls += 1;
      const status = calls === 1 ? "IMPLEMENTING" : "REVIEWING";
      const role = calls === 1 ? "engineer" : "reviewer";
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          baseTask({
            id: 502,
            status,
            current_role: role,
            current_execution_id: "exec-1",
            execution_status: "RUNNING",
            architecture_result: "## Plan\nDo the fix.",
            implementation_summary: status === "REVIEWING" ? "Implemented the fix." : null,
            allowed_actions: ["cancel"],
          }),
        ),
      });
    });
    await page.route(/\/api\/tasks\/502\/events(\?.*)?$/, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );
    await page.route(/\/api\/tasks\/502\/diff/, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ stat: "", full: "", commits: [], status: "", error: "no worktree exists for this task yet" }) }),
    );
    await page.route("**/api/executions?task_id=502", (route: any) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));

    // Start with SSE aborted so the initial "Implementing" state is
    // observed deterministically, then swap in a route that delivers one
    // event — EventLog's onerror handler reconnects ~2s after an abort,
    // which is when it will pick up the new route. This avoids a race
    // where the event could otherwise arrive before the first render.
    await page.route("**/api/events/stream**", (route: any) => route.abort());

    await page.goto("/work/502");
    await expect(stageBadge(page)).toHaveText("Implementing");
    await expect(page.getByText("Engineer", { exact: true }).first()).toBeVisible();

    await page.unroute("**/api/events/stream**");
    // The SSE body is delivered in one shot; EventSource parses it as a
    // single message the instant the route resolves, simulating a
    // real-time workflow.transition event without a live connection.
    await page.route("**/api/events/stream**", (route: any) =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `data: ${JSON.stringify({
          id: 9001,
          execution_id: "exec-1",
          task_id: 502,
          type: "task.transitioned",
          payload: { from: "IMPLEMENTING", to: "REVIEWING", action: "implementation_completed", actor: "system" },
          severity: "info",
          timestamp: "2026-08-12T08:05:00Z",
        })}\n\n`,
      }),
    );

    await expect(stageBadge(page)).toHaveText("Reviewing", { timeout: 8000 });
  });
});

test.describe("Journey C — needs user action", () => {
  test("architecture approval surfaces on the homepage and can be approved from the thread", async ({ page }) => {
    await mockCommon(page);
    const pending = baseTask({
      id: 503,
      status: "AWAITING_ARCHITECTURE_APPROVAL",
      architecture_result: "## Plan\nRewrite the startup sequence to lazy-load the scene cache.",
      allowed_actions: ["approve_architecture", "reject_architecture", "request_architecture_revision", "cancel"],
    });
    await page.route("**/api/tasks?limit=50", (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([pending]) }),
    );
    await mockTaskEndpoints(page, 503, pending);

    await page.goto("/");
    await expect(page.getByText("Architecture plan is waiting for your approval.")).toBeVisible();
    await page.getByText(pending.title).click();

    await expect(page).toHaveURL(/\/work\/503$/);
    await expect(page.getByText(/Rewrite the startup sequence/).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve plan" })).toBeVisible();

    await page.route("**/api/tasks/503/actions/approve-architecture", (route: any) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...pending, status: "READY_TO_IMPLEMENT", allowed_actions: ["start_implementation", "cancel"] }),
      }),
    );
    await page.getByRole("button", { name: "Approve plan" }).click();
    await expect(stageBadge(page)).toHaveText("Implementing");
  });
});

test.describe("Journey D — completion", () => {
  test("a reviewed request exposes result, commit, and files changed", async ({ page }) => {
    const done = baseTask({
      id: 504,
      status: "READY_FOR_HUMAN",
      base_commit: "deadbeef0000",
      task_branch: "sw-task-504",
      result_commit: "abc123def456",
      implementation_summary: "Lazy-loaded the scene cache on startup.",
      review_result: "VERDICT: APPROVED\n\nChange is minimal and well tested.",
      allowed_actions: ["accept", "reject", "send_back_to_engineer"],
    });
    await mockTaskEndpoints(page, 504, done);
    await page.route(/\/api\/tasks\/504\/diff/, (route: any) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          stat: "2 files changed, 10 insertions(+), 2 deletions(-)",
          full: "diff --git a/startup.py b/startup.py\n+lazy load\n",
          commits: [{ sha: "abc123def456", subject: "Lazy-load scene cache on startup", author: "engineer" }],
          status: "",
          error: null,
        }),
      }),
    );

    await page.goto("/work/504");
    await page.getByRole("button", { name: "Results", exact: true }).click();
    const results = page.locator(".result-summary");
    await expect(results.getByText("Approved")).toBeVisible();
    await expect(results.getByText("Lazy-load scene cache on startup")).toBeVisible();
    await expect(results.getByText("abc123def456")).toBeVisible();
    await expect(results.getByText(/Files changed:\s*2/)).toBeVisible();
  });
});

test.describe("Journey E — failure", () => {
  test("a failed execution shows a clear error state instead of staying 'running'", async ({ page }) => {
    const failed = baseTask({
      id: 505,
      status: "FAILED",
      allowed_actions: ["retry", "retry_architecture"],
    });
    await mockTaskEndpoints(page, 505, failed);

    await page.goto("/work/505");
    await expect(stageBadge(page)).toHaveText("Failed");
    await expect(page.getByText(/Execution failed and needs a decision/)).toBeVisible();
    await expect(page.getByText(/is working/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
  });
});
