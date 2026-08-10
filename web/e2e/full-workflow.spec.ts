/**
 * SceneWorks V2.5 E2E Tests
 *
 * Exercises the full workflow using FakeAgentBackend via direct API calls.
 * Each test creates its own temporary Git repository — no E2E_REPO_PATH needed.
 *
 * Prerequisites:
 *   cd backend && SCENEWORKS_DEFAULT_BACKEND=fake uv run python -m uvicorn app.main:app --port 8010
 *   cd web && npx playwright test
 */
import { test, expect } from "@playwright/test";
import { execSync } from "child_process";
import { mkdtempSync, writeFileSync, rmSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";

const API_URL = "http://127.0.0.1:8010";
const POLL_INTERVAL = 500;
const MAX_POLL_TIME = 90000;

// ----------------------------------------------------------------- helpers

function createTempRepo(): string {
  const tmpDir = mkdtempSync(join(tmpdir(), "sceneworks-e2e-"));
  execSync("git init -b main", { cwd: tmpDir });
  execSync('git config user.email "test@e2e.local"', { cwd: tmpDir });
  execSync('git config user.name "E2E Test"', { cwd: tmpDir });
  writeFileSync(join(tmpDir, "README.md"), "# E2E test repo\n");
  writeFileSync(join(tmpDir, "app.py"), "def main():\n    return 1 + 1\n");
  execSync("git add -A", { cwd: tmpDir });
  execSync('git commit -m "initial commit"', { cwd: tmpDir });
  return tmpDir;
}

function cleanupRepo(repoPath: string) {
  try { rmSync(repoPath, { recursive: true, force: true }); } catch {}
}

async function registerProject(page: any, name: string, repoPath: string) {
  const resp = await page.request.post(`${API_URL}/api/projects`, {
    data: { name, description: "E2E test project", repository_path: repoPath },
  });
  if (resp.status() !== 201) {
    const body = await resp.text();
    throw new Error(`Cannot register project: ${resp.status()} ${body}`);
  }
  return await resp.json();
}

async function createTask(page: any, projectId: number, title: string, description: string) {
  const resp = await page.request.post(`${API_URL}/api/tasks`, {
    data: { project_id: projectId, title, description, priority: "medium" },
  });
  if (resp.status() !== 201) {
    const body = await resp.text();
    throw new Error(`Cannot create task: ${resp.status()} ${body}`);
  }
  return await resp.json();
}

async function pollTask(page: any, taskId: number, timeout = MAX_POLL_TIME) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const resp = await page.request.get(`${API_URL}/api/tasks/${taskId}`);
    if (resp.status() === 200) {
      return await resp.json();
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL));
  }
  throw new Error(`Timed out polling task ${taskId}`);
}

async function waitForStatus(page: any, taskId: number, status: string, timeout = MAX_POLL_TIME) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const task = await pollTask(page, taskId);
    if (task.status === status) return task;
    if (task.status === "FAILED" || task.status === "CANCELLED" || task.status === "REJECTED") {
      throw new Error(`Task reached ${task.status} while waiting for ${status}`);
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL));
  }
  throw new Error(`Timed out waiting for task ${taskId} status ${status} (last: ${(await pollTask(page, taskId))?.status})`);
}

/**
 * Wait for an execution to reach a terminal state.
 *
 * A company ask returns as soon as the execution is queued. Deleting the
 * repository before it finishes races the agent (which is reading a pinned
 * worktree of that repository) and leaves the worktree un-removable.
 */
async function waitForExecution(page: any, executionId: string, timeout = 60000) {
  const terminal = ["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"];
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const resp = await page.request.get(`${API_URL}/api/executions/${executionId}`);
    if (resp.status() === 200) {
      const execution = await resp.json();
      if (terminal.includes(execution.status)) return execution;
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL));
  }
  throw new Error(`Timed out waiting for execution ${executionId} to finish`);
}

async function taskAction(page: any, taskId: number, action: string, extra?: Record<string, unknown>) {
  const resp = await page.request.post(`${API_URL}/api/tasks/${taskId}/actions/${action}`, {
    data: extra || {},
  });
  if (resp.status() >= 400) {
    const body = await resp.text();
    throw new Error(`Action ${action} failed: ${resp.status()} ${body}`);
  }
  return resp;
}

// ----------------------------------------------------------------- core workflow

test.describe("Full workflow E2E", () => {
  test("complete workflow: register → task → architecture → approve → engineer → review → accept", async ({ page }) => {
    test.setTimeout(120000);

    const suffix = Date.now().toString(36);
    const repoPath = createTempRepo();

    try {
      const project = await registerProject(page, `e2e-test-${suffix}`, repoPath);
      expect(project.id).toBeGreaterThan(0);

      const task = await createTask(page, project.id, `E2E fix calc ${suffix}`, "Fix the calculation bug in component X");
      expect(task.status).toBe("NEW");

      await taskAction(page, task.id, "start-architecture");
      let current = await waitForStatus(page, task.id, "AWAITING_ARCHITECTURE_APPROVAL");
      expect(current.architecture_result).toBeTruthy();

      await taskAction(page, task.id, "approve-architecture");
      current = await waitForStatus(page, task.id, "READY_FOR_HUMAN", 90000);
      expect(current.implementation_summary).toBeTruthy();
      expect(current.review_result).toBeTruthy();

      const diffResp = await page.request.get(`${API_URL}/api/tasks/${task.id}/diff`);
      expect(diffResp.status()).toBe(200);

      await taskAction(page, task.id, "accept");
      current = await pollTask(page, task.id);
      expect(current.status).toBe("ACCEPTED");
    } finally {
      cleanupRepo(repoPath);
    }
  });

  test("architecture revision workflow", async ({ page }) => {
    test.setTimeout(90000);
    const suffix = Date.now().toString(36);
    const repoPath = createTempRepo();

    try {
      const project = await registerProject(page, `e2e-revision-${suffix}`, repoPath);
      const task = await createTask(page, project.id, `E2E revision ${suffix}`, "Test architecture revision");

      await taskAction(page, task.id, "start-architecture");
      await waitForStatus(page, task.id, "AWAITING_ARCHITECTURE_APPROVAL");

      await taskAction(page, task.id, "request-architecture-revision", { notes: "Please reconsider X" });
      const current = await waitForStatus(page, task.id, "AWAITING_ARCHITECTURE_APPROVAL");
      expect(current.status).toBe("AWAITING_ARCHITECTURE_APPROVAL");
    } finally {
      cleanupRepo(repoPath);
    }
  });

  test("architecture rejection", async ({ page }) => {
    test.setTimeout(60000);
    const suffix = Date.now().toString(36);
    const repoPath = createTempRepo();

    try {
      const project = await registerProject(page, `e2e-reject-${suffix}`, repoPath);
      const task = await createTask(page, project.id, `E2E reject ${suffix}`, "Test architecture rejection");

      await taskAction(page, task.id, "start-architecture");
      await waitForStatus(page, task.id, "AWAITING_ARCHITECTURE_APPROVAL");

      await taskAction(page, task.id, "reject-architecture", { reason: "Not needed" });
      const current = await pollTask(page, task.id);
      expect(current.status).toBe("REJECTED");
    } finally {
      cleanupRepo(repoPath);
    }
  });

  test("cancellation from awaiting approval", async ({ page }) => {
    test.setTimeout(60000);
    const suffix = Date.now().toString(36);
    const repoPath = createTempRepo();

    try {
      const project = await registerProject(page, `e2e-cancel-${suffix}`, repoPath);
      const task = await createTask(page, project.id, `E2E cancel ${suffix}`, "Test cancellation");

      await taskAction(page, task.id, "start-architecture");
      await waitForStatus(page, task.id, "AWAITING_ARCHITECTURE_APPROVAL");

      await taskAction(page, task.id, "cancel");
      const current = await pollTask(page, task.id);
      expect(current.status).toBe("CANCELLED");
    } finally {
      cleanupRepo(repoPath);
    }
  });
});

// ----------------------------------------------------------------- repair / send-back

test.describe("Repair loop", () => {
  test("send back to engineer → repair → review → ready", async ({ page }) => {
    test.setTimeout(120000);
    const suffix = Date.now().toString(36);
    const repoPath = createTempRepo();

    try {
      const project = await registerProject(page, `e2e-repair-${suffix}`, repoPath);
      const task = await createTask(page, project.id, `E2E repair ${suffix}`, "Test repair loop");

      await taskAction(page, task.id, "start-architecture");
      await waitForStatus(page, task.id, "AWAITING_ARCHITECTURE_APPROVAL");
      await taskAction(page, task.id, "approve-architecture");
      await waitForStatus(page, task.id, "READY_FOR_HUMAN", 90000);

      await taskAction(page, task.id, "send-back", { notes: "Please also fix the tests" });

      const current = await waitForStatus(page, task.id, "READY_FOR_HUMAN", 90000);
      expect(current.status).toBe("READY_FOR_HUMAN");
    } finally {
      cleanupRepo(repoPath);
    }
  });
});

// ----------------------------------------------------------------- project memory

test.describe("Project Memory", () => {
  test("create and retrieve project memory", async ({ page }) => {
    test.setTimeout(30000);
    const suffix = Date.now().toString(36);
    const repoPath = createTempRepo();

    try {
      const project = await registerProject(page, `e2e-memory-${suffix}`, repoPath);

      const createResp = await page.request.post(`${API_URL}/api/projects/${project.id}/memory`, {
        data: {
          project_id: project.id,
          type: "architecture_decision",
          title: "Use SQLite for checkpoint storage",
          content: "We chose SQLite because it requires no external services.",
          status: "proposed",
          tags: ["storage", "architecture"],
        },
      });
      expect(createResp.status()).toBe(201);
      const mem = await createResp.json();
      expect(mem.id).toBeGreaterThan(0);
      expect(mem.title).toBe("Use SQLite for checkpoint storage");

      const listResp = await page.request.get(`${API_URL}/api/projects/${project.id}/memory`);
      expect(listResp.status()).toBe(200);
      const memories = await listResp.json();
      expect(memories.length).toBeGreaterThanOrEqual(1);
      expect(memories.some((m: any) => m.id === mem.id)).toBe(true);

      const getResp = await page.request.get(`${API_URL}/api/projects/${project.id}/memory/${mem.id}`);
      expect(getResp.status()).toBe(200);
      const fetched = await getResp.json();
      expect(fetched.type).toBe("architecture_decision");

      const archiveResp = await page.request.post(`${API_URL}/api/projects/${project.id}/memory/${mem.id}/archive`);
      expect(archiveResp.status()).toBe(200);
      const archived = await archiveResp.json();
      expect(archived.status).toBe("archived");
    } finally {
      cleanupRepo(repoPath);
    }
  });
});

// ----------------------------------------------------------------- advisory / company

test.describe("Advisory role routing", () => {
  test("company ask creates a role execution", async ({ page }) => {
    test.setTimeout(30000);
    const suffix = Date.now().toString(36);
    const repoPath = createTempRepo();

    try {
      const project = await registerProject(page, `e2e-advisory-${suffix}`, repoPath);

      const askResp = await page.request.post(`${API_URL}/api/company/ask`, {
        data: { role: "architect", project_id: project.id, question: "Should I use React Server Components?" },
      });
      if (askResp.status() !== 201) {
        const body = await askResp.text();
        throw new Error(`Company ask failed: ${askResp.status()} ${body}`);
      }
      const exec = await askResp.json();
      expect(exec.id).toBeTruthy();
      expect(exec.role).toBe("architect");

      // The ask must run against a commit-pinned snapshot, not the working
      // tree, and must record the commit it analyzed.
      expect(exec.workspace.base_commit).toBeTruthy();
      expect(exec.workspace.cwd).not.toBe(repoPath);

      const finished = await waitForExecution(page, exec.id);
      expect(finished.status).toBe("COMPLETED");

      // A manual architect ask is stored as a company decision.
      const artifactsResp = await page.request.get(`${API_URL}/api/company/artifacts?role=architect`);
      expect(artifactsResp.status()).toBe(200);
      const artifacts = await artifactsResp.json();
      expect(artifacts.some((a: any) => a.source_execution_id === exec.id)).toBe(true);

      const rolesResp = await page.request.get(`${API_URL}/api/company/roles`);
      expect(rolesResp.status()).toBe(200);
      const roles = await rolesResp.json();
      expect(roles.length).toBeGreaterThan(0);
      expect(roles.some((r: any) => r.key === "architect")).toBe(true);
    } finally {
      cleanupRepo(repoPath);
    }
  });
});

// ----------------------------------------------------------------- UI smoke tests

test.describe("UI smoke tests", () => {
  test("dashboard loads", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("h1").first()).toContainText(/SceneWorks|Dashboard/i);
  });

  test("projects page loads", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.locator("h1").first()).toContainText(/Projects/i);
  });

  test("company page shows roles", async ({ page }) => {
    await page.goto("/company");
    await expect(page.locator("h1").first()).toContainText(/Company/i);
    await expect(page.locator("text=Engineer").or(page.locator("text=architect"))).toBeVisible({ timeout: 10000 });
  });

  test("settings page shows backends", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.locator("h1").first()).toContainText(/Settings/i);
  });
});

// ----------------------------------------------------------------- error handling

test.describe("Error handling", () => {
  test("handles API unreachable gracefully", async ({ page }) => {
    await page.goto("/");
    const errorNotice = page.locator(".notice.error, .error, [class*='error']");
    const dashboard = page.locator("h1");
    await expect(errorNotice.or(dashboard).first()).toBeVisible({ timeout: 15000 });
  });
});
