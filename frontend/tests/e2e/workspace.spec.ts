import { expect, test } from "@playwright/test";

/**
 * M1 acceptance journey: login -> run SQL on a real PostgreSQL source ->
 * results visible -> history entry -> cancellation path.
 *
 * Requires a running core stack and a seeded demo/fixture source:
 *   E2E_BASE_URL=http://127.0.0.1:3000 npm run test:e2e
 * Environment knobs:
 *   E2E_ADMIN_USER / E2E_ADMIN_PASSWORD (default admin / dev-admin-password-123)
 *   E2E_DATASOURCE_NAME (default source-postgres)
 *   E2E_TABLE (default fixture_metrics)
 */
const enabled = Boolean(process.env.E2E_BASE_URL);

async function selectDatasource(page: import("@playwright/test").Page, name: string) {
  const option = page.locator('[data-testid="datasource-select"] option', { hasText: name });
  await expect(option.first()).toBeAttached();
  const value = await option.first().getAttribute("value");
  await page.getByTestId("datasource-select").selectOption(value as string);
}

test.describe("SQL workspace", () => {
  test.skip(!enabled, "set E2E_BASE_URL to run the end-to-end journey");

  test("login, run a query, inspect results and history", async ({ page }) => {
    const datasourceName = process.env.E2E_DATASOURCE_NAME ?? "source-postgres";
    const table = process.env.E2E_TABLE ?? "fixture_metrics";

    // Signed in by global setup (one login per run, rate limit aware).
    await page.goto("/sql");
    await expect(page.getByTestId("datasource-select")).toBeVisible();
    await selectDatasource(page, datasourceName);

    await page
      .getByTestId("sql-editor")
      .fill(`SELECT country, COUNT(*) AS rows, SUM(revenue_usd) AS revenue FROM ${table} GROUP BY country ORDER BY country`);
    await page.getByTestId("max-rows").fill("100");
    await page.getByTestId("run-query").click();

    // The result table appears once the worker publishes the result.
    await expect(page.locator(".result-table tbody tr").first()).toBeVisible({ timeout: 30_000 });
    const headerText = await page.locator(".result-table thead").innerText();
    expect(headerText).toContain("country");
    expect(headerText).toContain("revenue");

    // Evidence drawer exposes the validated SQL.
    await page.locator(".evidence summary").click();
    await expect(page.locator(".evidence pre")).toContainText("LIMIT");

    // History shows the query with a terminal state.
    await page.getByRole("link", { name: "History" }).click();
    await expect(page.locator(".result-table tbody tr").first()).toBeVisible();
    await expect(page.locator(".badge.ok").first()).toBeVisible();
  });

  test("rejects a write statement with a visible error", async ({ page }) => {
    const datasourceName = process.env.E2E_DATASOURCE_NAME ?? "source-postgres";

    await page.goto("/sql");
    await expect(page.getByTestId("datasource-select")).toBeVisible();
    await selectDatasource(page, datasourceName);

    await page.getByTestId("sql-editor").fill("DELETE FROM fixture_metrics");
    await page.getByTestId("run-query").click();
    await expect(page.getByTestId("submit-error")).toContainText("SQL_FORBIDDEN");
  });
});


/**
 * M6 journey: governed analysis -> chart artifact -> drilldown child analysis.
 * Requires the deployed stack plus the demo data loaded in Doris and an
 * `ads_revenue` metric (the A09 harness or `make demo-load` provides both).
 * Environment knobs: E2E_ANALYSIS_METRIC (default ads_revenue),
 * E2E_BASELINE_START/E2E_BASELINE_END/E2E_CURRENT_START/E2E_CURRENT_END.
 */
test.describe("Analysis and charts", () => {
  test.skip(!enabled, "set E2E_BASE_URL to run the end-to-end journey");

  test("start an analysis, read its chart and drill into a group", async ({ page }) => {
    const metric = process.env.E2E_ANALYSIS_METRIC ?? "ads_revenue";

    await page.goto("/sql");
    await expect(page.getByTestId("datasource-select")).toBeVisible();
    await page.getByRole("link", { name: "Ask" }).click();
    await expect(page.getByTestId("analysis-metric")).toBeVisible();
    await page.getByTestId("analysis-metric").selectOption(metric);
    // The picker belongs to the selected metric: wait for it, and verify the
    // checkbox is really checked before submitting. Switching metrics resets the
    // selection, so a click that races that reset would otherwise submit an
    // analysis with no breakdown (and therefore no chart).
    await expect(page.getByTestId("dimension-country")).toBeVisible();
    await page.getByTestId("baseline-start").fill("2026-09-11");
    await page.getByTestId("baseline-end").fill("2026-09-12");
    await page.getByTestId("current-start").fill("2026-09-12");
    await page.getByTestId("current-end").fill("2026-09-13");
    const countryBox = page.getByTestId("dimension-country").locator("input");
    await page.getByTestId("dimension-country").click();
    await expect(countryBox).toBeChecked();
    await page.getByTestId("start-analysis").click();

    // The analysis page polls until a terminal state.
    await expect(page.locator(".badge", { hasText: "Completed" })).toBeVisible({ timeout: 90_000 });
    const chart = page.getByTestId("chart-panel");
    await expect(chart).toBeVisible();
    await expect(chart.getByTestId("chart-bars")).toBeVisible();

    // Table equivalent view carries the same numbers.
    await chart.getByTestId("chart-toggle-table").click();
    await expect(chart.getByTestId("chart-table")).toBeVisible();
    await chart.getByTestId("chart-toggle-table").click();

    // Drilldown creates a child analysis and navigates to it.
    const parentUrl = page.url();
    await chart.getByTestId("chart-drilldown").first().click();
    await expect(page).not.toHaveURL(parentUrl, { timeout: 30_000 });
    await expect(page.getByText("下钻：")).toBeVisible();
  });
});


/**
 * M6 journey: the administration screens (spec section 24).
 *
 * Covers the two routes the spec names - /admin/datasources and
 * /admin/permissions - and the loop around them: request access from the
 * catalog, see it in the administrator queue, mark it as a mock (which must not
 * grant anything), create and revoke a real grant.
 *
 * Requires the deployed stack with a registered `source-postgres`
 * (E2E_DATASOURCE_NAME) and at least one dataset visible to the viewer role.
 */
test.describe("Administration", () => {
  test.skip(!enabled, "set E2E_BASE_URL to run the end-to-end journey");

  test("datasource health, catalog refresh, grants and the request queue", async ({ page }) => {
    const datasourceName = process.env.E2E_DATASOURCE_NAME ?? "source-postgres";

    await page.goto("/sql");
    await expect(page.getByTestId("datasource-select")).toBeVisible();

    // The catalog offers the access request that feeds the administrator queue.
    await page.getByRole("link", { name: "Catalog" }).click();
    await page.getByTestId("catalog-search").fill("fixture_metrics");
    await page.locator(".dataset-list .dataset").first().click();
    await page.getByTestId("request-reason").fill("E2E: revenue for the weekly report");
    await page.getByTestId("request-access").click();
    await expect(page.getByTestId("request-notice")).toContainText("REQUESTED");

    // Datasources: real connection test and a catalog refresh that reports counts.
    await page.getByTestId("nav-admin-datasources").click();
    await expect(page.getByTestId("admin-datasources")).toBeVisible();
    await page.getByTestId(`test-${datasourceName}`).click();
    await expect(page.getByTestId("datasource-test-result")).toContainText("HEALTHY");
    await page.getByTestId("refresh-secure-views").fill("");
    await page.getByTestId(`refresh-${datasourceName}`).click();
    await expect(page.getByTestId("catalog-refresh-result")).toContainText("注册");

    // Permissions: the request is visible, mock approval is labelled as a mock.
    await page.getByTestId("nav-admin-permissions").click();
    await expect(page.getByTestId("admin-permissions")).toBeVisible();
    const requestRow = page.locator('[data-testid="request-table"] tbody tr').first();
    await expect(requestRow).toBeVisible();
    const approve = requestRow.getByTestId(/^mock-approve-/);
    if (await approve.isEnabled()) {
      await approve.click();
      await expect(page.getByTestId("admin-notice")).toContainText("Mock 状态");
    }
    await expect(page.locator('[data-testid="request-table"]')).toContainText("未授权");

    // A real grant is created and revoked through the form. Assertions are made
    // on the rows matching this role/dataset pair only: the table also holds
    // grants created by other tests and by the seed, so an absolute row count
    // would be measuring the environment instead of the screen.
    const rows = page.locator('[data-testid="grant-table"] tbody tr');
    await expect(rows.first()).toBeVisible();
    const datasetSelect = page.getByTestId("grant-dataset");
    await datasetSelect.selectOption({ index: 1 });
    const datasetLabel = (await datasetSelect.locator("option:checked").innerText()).trim();
    // The exact triple: role + dataset + action. Filtering without the action
    // would also match the `discover` grant that other suites create.
    const matching = () =>
      rows.filter({ hasText: "viewer" }).filter({ hasText: datasetLabel }).filter({ hasText: "query" });
    for (let guard = 0; guard < 10; guard += 1) {
      const found = await matching().count();
      if (found === 0) break;
      await matching().first().getByRole("button", { name: "撤回" }).click();
      await expect(page.getByTestId("admin-notice")).toContainText("已撤回");
      // Wait for the list to settle before touching the next row: the refetch
      // replaces the rows, and a click aimed at a replaced row never lands.
      await expect(matching()).toHaveCount(found - 1, { timeout: 15_000 });
    }
    await expect(matching()).toHaveCount(0, { timeout: 15_000 });

    await page.getByTestId("grant-role").selectOption({ label: "viewer" });
    await page.getByTestId("grant-action").selectOption("query");
    await page.getByTestId("create-grant").click();
    await expect(page.getByTestId("admin-notice")).toContainText("真实授权");
    await expect(matching()).toHaveCount(1, { timeout: 15_000 });

    await matching().first().getByRole("button", { name: "撤回" }).click();
    await expect(page.getByTestId("admin-notice")).toContainText("已撤回");
    await expect(matching()).toHaveCount(0, { timeout: 15_000 });
  });
});


test.describe("Administration" + " capability gate", () => {
  test.skip(!enabled, "set E2E_BASE_URL to run the end-to-end journey");

  test("a viewer sees no administration entry point and no admin data", async ({ page }) => {
    const viewerName = process.env.E2E_VIEWER_USER ?? "e2e-viewer";
    const viewerPassword = process.env.E2E_VIEWER_PASSWORD ?? "e2e-viewer-password-123";

    // Starts signed in as the administrator (global setup); this journey is the
    // one that deliberately signs out and back in as somebody else.
    await page.goto("/sql");
    await expect(page.getByTestId("datasource-select")).toBeVisible();

    // Create (or reuse) a viewer account through the screen itself.
    await page.getByTestId("nav-admin-permissions").click();
    await page.getByTestId("new-user-name").fill(viewerName);
    await page.getByTestId("new-user-password").fill(viewerPassword);
    await page.getByTestId("new-user-role-viewer").check();
    await page.getByTestId("create-user").click();
    // A repeated run finds the user already there (409 CONFLICT); either outcome
    // is fine - what matters is that the account exists with the known password.
    await expect(
      page.locator('[data-testid="admin-notice"], .notice.error'),
    ).toBeVisible();

    await page.getByRole("button", { name: "Sign out" }).click();
    await page.getByTestId("login-username").fill(viewerName);
    await page.getByTestId("login-password").fill(viewerPassword);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("datasource-select")).toBeVisible();

    // No administration entry points in the navigation.
    await expect(page.getByTestId("nav-admin-datasources")).toHaveCount(0);
    await expect(page.getByTestId("nav-admin-permissions")).toHaveCount(0);

    // Going there directly shows the gate, and no admin data is rendered.
    await page.goto("/admin/permissions");
    await expect(page.getByTestId("permission-gate")).toBeVisible();
    await expect(page.getByTestId("request-table")).toHaveCount(0);
    await expect(page.getByTestId("grant-table")).toHaveCount(0);
  });
});
