/**
 * WP-WEB-4 reliability contracts (browser-level, mocked API).
 *
 * Guards the failure/recovery behavior and request economy of the Home and
 * Team pages:
 *  - one outage surfaces exactly one page-level error, never duplicate
 *    banners or sections pinned on "Loading…";
 *  - a page recovers once the API answers again;
 *  - the sidebar and the page share a single task-list request;
 *  - polling stops while the tab is hidden;
 *  - Team renders loading/error states instead of a broken org chart.
 */
import { test, expect } from "@playwright/test";

const API = "http://127.0.0.1:8010";

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

const ROLES = [
  ["ceo", "CEO"], ["cto", "CTO"], ["product", "Product"], ["gtm", "GTM"],
  ["architect", "Architect"], ["engineer", "Engineer"], ["reviewer", "Reviewer"],
].map(([key, name]) => ({
  key,
  display_name: name,
  description: `${name} role description`,
  backend: "fake",
  model_profile: null,
  permissions: [],
  can_modify_source: key === "engineer",
  can_commit: key === "engineer",
  responsibilities: [],
}));

async function apiDown(page: any) {
  await page.route(`${API}/**`, (route: any) => route.abort());
}

function countApiRequests(page: any) {
  const hits: { url: string; count: number }[] = [];
  page.on("request", (req: any) => {
    if (!req.url().startsWith(API)) return;
    const entry = hits.find((h) => h.url === req.url());
    if (entry) entry.count += 1;
    else hits.push({ url: req.url(), count: 1 });
  });
  return hits;
}

test.describe("Home reliability", () => {
  test("an API outage shows one error and terminates every loading state", async ({ page }) => {
    await apiDown(page);
    await page.goto("/");
    await expect(page.locator(".notice.error")).toHaveCount(1, { timeout: 10_000 });
    await expect(page.locator(".notice.error")).toContainText("Cannot reach the SceneWorks API");
    // The composer must not stack a second banner for the same outage.
    await expect(page.getByText(/Project list unavailable/)).toBeVisible();
    // No section may be stuck on Loading….
    await expect(page.getByText("Loading…")).toHaveCount(0);
    await expect(page.locator("section .empty").filter({ hasText: /Unavailable/ })).toHaveCount(3);
  });

  test("the homepage recovers when the API comes back", async ({ page }) => {
    await apiDown(page);
    await page.goto("/");
    await expect(page.locator(".notice.error")).toHaveCount(1, { timeout: 10_000 });

    await page.unroute(`${API}/**`);
    await page.route(`${API}/api/projects`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([PROJECT]) }),
    );
    await page.route(`${API}/api/tasks?limit=200`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );

    await page.waitForTimeout(16_000); // next poll tick + composer retry
    await expect(page.locator(".notice.error")).toHaveCount(0);
    await expect(page.getByText(/Nothing needs you right now/)).toBeVisible();
    await expect(page.getByPlaceholder(/Ask the team/i)).toBeVisible();
  });

  test("sidebar and page share a single task-list request per poll", async ({ page }) => {
    const hits = countApiRequests(page);
    await page.route(`${API}/api/projects`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([PROJECT]) }),
    );
    await page.route(`${API}/api/tasks**`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );

    await page.goto("/");
    await page.waitForTimeout(18_000); // mount + two poll ticks
    const taskHits = hits.filter((h) => h.url.includes("/api/tasks"));
    expect(taskHits.length).toBe(1); // one URL only — the shared snapshot
    expect(taskHits[0].url).toBe(`${API}/api/tasks?limit=200`);
    expect(taskHits[0].count).toBeLessThanOrEqual(3); // mount + 2 ticks, never doubled per tick
    expect(hits.some((h) => h.url.includes("limit=6"))).toBe(false);
  });

  test("the shared poller pauses while the tab is hidden", async ({ page }) => {
    await page.clock.install();
    const hits = countApiRequests(page);
    await page.route(`${API}/api/projects`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([PROJECT]) }),
    );
    await page.route(`${API}/api/tasks**`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );

    await page.goto("/");
    await expect(page.getByText(/Nothing needs you right now/)).toBeVisible();

    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { get: () => "hidden", configurable: true });
    });
    await page.clock.fastForward(20_000);
    const hiddenCount = hits.filter((h) => h.url.includes("/api/tasks"))[0]?.count ?? 0;
    expect(hiddenCount).toBe(1);

    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { get: () => "visible", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.waitForTimeout(500);
    const visibleCount = hits.filter((h) => h.url.includes("/api/tasks"))[0]?.count ?? 0;
    expect(visibleCount).toBeGreaterThan(1);
  });
});

test.describe("Team reliability", () => {
  test("a failed roles fetch shows an error state, never a broken org chart", async ({ page }) => {
    await apiDown(page);
    await page.goto("/company");
    // No org nodes and no bare connector pipes from a half-rendered chart.
    await expect(page.locator(".org-node")).toHaveCount(0);
    await expect(page.locator(".role-org")).toHaveCount(0);
    await expect(page.getByText(/Team unavailable/)).toBeVisible({ timeout: 10_000 });
    await expect(page.locator(".panel").filter({ hasText: "Org chart" }).getByRole("button", { name: "Retry" })).toBeVisible();
    // Silent failures must not masquerade as empty data.
    await expect(page.getByText(/Decisions unavailable/)).toBeVisible();
    await expect(page.getByText(/Project list unavailable/)).toBeVisible();
  });

  test("the team page recovers via retry and renders the org chart", async ({ page }) => {
    await apiDown(page);
    await page.goto("/company");
    await expect(page.getByText(/Team unavailable/)).toBeVisible({ timeout: 10_000 });

    await page.unroute(`${API}/**`);
    await page.route(`${API}/api/company/roles`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ROLES) }),
    );
    await page.route(`${API}/api/company/artifacts`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );
    await page.route(`${API}/api/projects`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([PROJECT]) }),
    );
    await page.route(`${API}/api/tasks**`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );

    await page.locator(".panel").filter({ hasText: "Org chart" }).getByRole("button", { name: "Retry" }).click();
    await expect(page.locator(".org-node")).toHaveCount(7);
    await expect(page.locator(".org-node .name").filter({ hasText: /^CEO$/ })).toBeVisible();
    await expect(page.getByText(/No decisions stored yet/)).toBeVisible();
  });

  test("roles loading state precedes the chart instead of an empty diagram", async ({ page }) => {
    let releaseRoles!: () => void;
    const held = new Promise<void>((resolve) => { releaseRoles = resolve; });
    await page.route(`${API}/api/company/roles`, async (route: any) => {
      await held;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ROLES) });
    });
    await page.route(`${API}/api/company/artifacts`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );
    await page.route(`${API}/api/projects`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([PROJECT]) }),
    );
    await page.route(`${API}/api/tasks**`, (route: any) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );

    await page.goto("/company");
    await expect(page.getByText("Loading team…")).toBeVisible();
    await expect(page.locator(".org-node")).toHaveCount(0);
    releaseRoles();
    await expect(page.locator(".org-node")).toHaveCount(7);
  });
});
