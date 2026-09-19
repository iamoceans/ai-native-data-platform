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
    const user = process.env.E2E_ADMIN_USER ?? "admin";
    const password = process.env.E2E_ADMIN_PASSWORD ?? "dev-admin-password-123";
    const datasourceName = process.env.E2E_DATASOURCE_NAME ?? "source-postgres";
    const table = process.env.E2E_TABLE ?? "fixture_metrics";

    await page.goto("/login");
    await page.getByTestId("login-username").fill(user);
    await page.getByTestId("login-password").fill(password);
    await page.getByTestId("login-submit").click();

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
    const user = process.env.E2E_ADMIN_USER ?? "admin";
    const password = process.env.E2E_ADMIN_PASSWORD ?? "dev-admin-password-123";
    const datasourceName = process.env.E2E_DATASOURCE_NAME ?? "source-postgres";

    await page.goto("/login");
    await page.getByTestId("login-username").fill(user);
    await page.getByTestId("login-password").fill(password);
    await page.getByTestId("login-submit").click();
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
    const user = process.env.E2E_ADMIN_USER ?? "admin";
    const password = process.env.E2E_ADMIN_PASSWORD ?? "dev-admin-password-123";
    const metric = process.env.E2E_ANALYSIS_METRIC ?? "ads_revenue";

    await page.goto("/login");
    await page.getByTestId("login-username").fill(user);
    await page.getByTestId("login-password").fill(password);
    await page.getByTestId("login-submit").click();
    // Login lands on /sql; wait for the session to be live before navigating.
    await expect(page.getByTestId("datasource-select")).toBeVisible();

    await page.getByRole("link", { name: "Ask" }).click();
    await expect(page.getByTestId("analysis-metric")).toBeVisible();
    await page.getByTestId("analysis-metric").selectOption(metric);
    await page.getByTestId("baseline-start").fill("2026-09-11");
    await page.getByTestId("baseline-end").fill("2026-09-12");
    await page.getByTestId("current-start").fill("2026-09-12");
    await page.getByTestId("current-end").fill("2026-09-13");
    await page.getByTestId("dimension-country").click();
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
