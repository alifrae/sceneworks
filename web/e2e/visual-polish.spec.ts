/**
 * WP-WEB-3 visual/interaction invariants (browser-level, mocked API).
 *
 * These protect the specific hierarchy and layout claims made by
 * WP-WEB-3 — that representative Work Thread states render with their
 * primary actions visible, that decision actions carry a real
 * primary/secondary/destructive hierarchy, and that long content doesn't
 * break the page layout. Semantic DOM/layout checks, not pixel diffs, per
 * the WP's own guidance. Mocking style matches work-journeys.spec.ts.
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

async function mockTask(page: any, id: number, task: Record<string, unknown>, diff?: Record<string, unknown>) {
  await page.route("**/api/projects", (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([PROJECT]) }),
  );
  await page.route(new RegExp(`/api/tasks/${id}$`), (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(task) }),
  );
  await page.route(new RegExp(`/api/tasks/${id}/events(\\?.*)?$`), (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route(
    new RegExp(`/api/tasks/${id}/diff`),
    (route: any) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(diff ?? { stat: "", full: "", commits: [], status: "", error: "no worktree exists for this task yet" }),
      }),
  );
  await page.route(`**/api/executions?task_id=${id}`, (route: any) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/events/stream**", (route: any) => route.abort());
}

function noHorizontalOverflow(page: any) {
  return page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
}

const REPRESENTATIVE_STATES: { name: string; id: number; task: Record<string, unknown> }[] = [
  {
    name: "Investigating",
    id: 701,
    task: baseTask({ id: 701, status: "ARCHITECTURE_ANALYSIS", current_role: "triage", allowed_actions: ["cancel"] }),
  },
  {
    name: "Architecture approval",
    id: 702,
    task: baseTask({
      id: 702,
      status: "AWAITING_ARCHITECTURE_APPROVAL",
      architecture_result: "## Plan\nRewrite the startup sequence.",
      allowed_actions: ["approve_architecture", "reject_architecture", "request_architecture_revision", "cancel"],
    }),
  },
  {
    name: "Implementing",
    id: 703,
    task: baseTask({
      id: 703,
      status: "IMPLEMENTING",
      current_role: "engineer",
      current_execution_id: "exec-703",
      execution_status: "RUNNING",
      architecture_result: "## Plan\nRewrite the startup sequence.",
      allowed_actions: ["cancel"],
    }),
  },
  {
    name: "Reviewing",
    id: 704,
    task: baseTask({
      id: 704,
      status: "REVIEWING",
      current_role: "reviewer",
      current_execution_id: "exec-704",
      execution_status: "RUNNING",
      architecture_result: "## Plan\nRewrite the startup sequence.",
      implementation_summary: "Implemented the fix.",
      allowed_actions: ["cancel"],
    }),
  },
  {
    name: "Completed",
    id: 705,
    task: baseTask({
      id: 705,
      status: "READY_FOR_HUMAN",
      implementation_summary: "Implemented the fix.",
      review_result: "VERDICT: APPROVED\n\nLooks good.",
      allowed_actions: ["accept", "reject", "send_back_to_engineer"],
    }),
  },
  {
    name: "Failed",
    id: 706,
    task: baseTask({ id: 706, status: "FAILED", allowed_actions: ["retry", "retry_architecture"] }),
  },
];

test.describe("WP-WEB-3 representative Work Thread states", () => {
  for (const { name, id, task } of REPRESENTATIVE_STATES) {
    test(`${name} renders with its stage badge and primary action visible`, async ({ page }) => {
      await mockTask(page, id, task);
      await page.goto(`/work/${id}`);

      await expect(page.locator(".stage-badge")).toBeVisible();
      // Every state above offers at least one meaningful action (cancel while
      // running, approve/reject at a gate, retry after failure, accept/reject
      // on completion) — it must never be hidden behind other content.
      await expect(page.locator(".decision-actions button, .thread-body button").first()).toBeVisible();
      expect(await noHorizontalOverflow(page)).toBe(true);
    });
  }
});

test.describe("WP-WEB-3 decision action hierarchy", () => {
  test("architecture approval exposes primary, secondary, and destructive actions distinctly", async ({ page }) => {
    const task = REPRESENTATIVE_STATES.find((s) => s.name === "Architecture approval")!.task;
    await mockTask(page, 702, task);
    await page.goto("/work/702");

    const approve = page.getByRole("button", { name: "Approve plan" });
    const requestChanges = page.getByRole("button", { name: "Request changes" });
    const reject = page.getByRole("button", { name: "Reject plan" });

    await expect(approve).toBeVisible();
    await expect(requestChanges).toBeVisible();
    await expect(reject).toBeVisible();

    await expect(approve).toHaveClass(/primary/);
    await expect(reject).toHaveClass(/danger/);
    await expect(requestChanges).not.toHaveClass(/primary/);
    await expect(requestChanges).not.toHaveClass(/danger/);
  });
});

test.describe("WP-WEB-3 long content", () => {
  test("a long work title does not overflow the page", async ({ page }) => {
    const longTitle =
      "Investigate and fix the intermittent startup regression that appears only on cold cache when the scene graph loader races the asset manifest fetch on Windows CI runners and also sometimes on macOS";
    await mockTask(
      page,
      707,
      baseTask({ id: 707, title: longTitle, status: "IMPLEMENTING", current_role: "engineer", allowed_actions: ["cancel"] }),
    );
    await page.goto("/work/707");
    await expect(page.locator(".thread-title")).toContainText(longTitle);
    expect(await noHorizontalOverflow(page)).toBe(true);
  });

  test("a long architecture plan with code and long lines renders without overflow", async ({ page }) => {
    const longArch = `## Root cause

A very long unbroken line intended to verify that long unbroken technical text such as a stack trace or a giant single-token identifier does not blow out the layout width of the plan panel when it is rendered inside the markdown viewer component used by the Work Thread page.

\`\`\`python
def load_manifest(path):
    return Manifest.stream(path)
\`\`\`
`;
    await mockTask(
      page,
      708,
      baseTask({ id: 708, status: "AWAITING_ARCHITECTURE_APPROVAL", architecture_result: longArch, allowed_actions: ["approve_architecture"] }),
    );
    await page.goto("/work/708");
    await page.getByRole("tab", { name: "Plan", exact: true }).click();
    await expect(page.locator("pre code")).toBeVisible();
    expect(await noHorizontalOverflow(page)).toBe(true);
  });

  test("a long diff file name renders and expands without overflow", async ({ page }) => {
    const longPath = "backend/app/services/very/deeply/nested/module/path/that/is/unusually/long/scene_cache_manifest_loader.py";
    await mockTask(
      page,
      709,
      baseTask({
        id: 709,
        status: "READY_FOR_HUMAN",
        result_commit: "abc123def456",
        implementation_summary: "Refactored the loader.",
        allowed_actions: ["accept"],
      }),
      {
        stat: " 1 file changed",
        full: `diff --git a/${longPath} b/${longPath}\n+lazy load\n`,
        commits: [{ sha: "abc123def456", subject: "Refactor loader", author: "engineer" }],
        status: "",
        error: null,
      },
    );
    await page.goto("/work/709");
    await page.getByRole("tab", { name: "Changes", exact: true }).click();
    await expect(page.locator(".diff-file-path")).toHaveAttribute("title", longPath);
    expect(await noHorizontalOverflow(page)).toBe(true);
  });
});
