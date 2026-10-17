import { expect, test } from "@playwright/test";

test.use({ locale: "en-US" });

for (const start of ["/ask/s", "/analyses/a"]) {
  test(`revocation clears cached evidence on ${start}`, async ({ page }) => {
    let revoked = false;
    let redactedReads = 0;
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    const session = { id: "s", title: "Review", context_version: 1, active_turn_id: null,
      resolved_context: {}, skill_config: { mode: "off" }, updated_at: "2026-10-18T00:00:00Z" };
    const chart = { id: "c", schema_version: 1, spec: { schema_version: 1, kind: "bar",
      title: "Confidential contribution", data_ref: { type: "calculation", id: "calc" },
      encoding: { x: { field: "key", type: "category" }, y: { field: "delta", type: "quantitative", unit: "USD" } },
      interaction: { tooltip: true, zoom: false, drilldown_dimensions: [] }, evidence_ids: ["q"], empty_state: "none" },
      data: [{ key: "sample", dimension_values: ["sample"], delta: "999", net_change_share: "1" }],
      total_points: 1, offset: 0, limit: 200, truncated: false, dropped_points: 0 };
    await page.clock.install();
    await page.route("**/api/v1/**", async route => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = { items: [] };
      if (path.endsWith("/auth/me")) body = { id: "owner", username: "owner", roles: [], capabilities: ["analysis.create"], locale: "en" };
      if (path.endsWith("/sessions")) body = { items: [session] };
      if (path.endsWith("/sessions/s")) body = session;
      if (path.endsWith("/messages")) {
        if (revoked) redactedReads++;
        body = { items: [{ id: "m", sequence: 1, role: "assistant", content: { kind: "analysis_result",
          analysis_id: "a", status: "COMPLETED", claims: revoked ? [] : [{ id: "claim", text: "Revenue 123" }], limitations: [],
          ...(revoked ? { permission_revoked: true, report_available: false, evidence_state: "permission_revoked" } : {}) },
          created_at: "2026-10-18T00:00:00Z", content_schema_version: 1, turn_id: null }] };
      }
      if (path.endsWith("/analyses/a")) {
        if (revoked) redactedReads++;
        body = { id: "a", status: "COMPLETED", question: "fixture", budget: {}, steps: [],
          state: revoked ? { permission_revoked: true } : {},
          final_report: revoked ? null : { claims: [{ id: "claim", text: "Revenue 123" }], chart_ids: ["c"] } };
      }
      if (path.endsWith("/evidence")) body = { sql: "fixture confidential SQL" };
      if (path.endsWith("/charts/c")) {
        if (revoked) { await route.fulfill({ status: 403, json: { error: { code: "PERMISSION_DENIED", message: "revoked" } } }); return; }
        body = chart;
      }
      await route.fulfill({ json: body });
    });
    await page.goto(start);
    await expect(page.getByTestId("chart-panel")).toBeVisible();
    await expect(page.getByText("Revenue 123", { exact: true })).toBeVisible();
    if (start.startsWith("/analyses")) {
      await page.getByRole("button", { name: "Load evidence" }).click();
      await expect(page.getByText(/fixture confidential SQL/)).toBeVisible();
    }
    revoked = true;
    await page.clock.fastForward(16000);
    await expect.poll(() => redactedReads).toBeGreaterThan(0);
    await expect(page.getByTestId("permission-revoked")).toBeVisible();
    await expect(page.getByTestId("chart-panel")).toHaveCount(0);
    await expect(page.getByText("Revenue 123", { exact: true })).toHaveCount(0);
    await expect(page.getByTestId("analysis-diagnostics")).toHaveCount(0);
    await expect(page.getByTestId("analysis-memory")).toHaveCount(0);
    if (start.startsWith("/ask")) {
      await page.getByTestId("analysis-deeplink").click();
      await expect(page.getByTestId("permission-revoked")).toBeVisible();
      await expect(page.getByTestId("chart-panel")).toHaveCount(0);
    }
    revoked = false;
    chart.data[0].key = "restored sample";
    chart.data[0].dimension_values = ["restored sample"];
    await page.clock.fastForward(16000);
    await expect(page.getByTestId("chart-panel")).toBeVisible();
    await expect(page.getByTestId("chart-panel")).toContainText("restored sample");
    await expect(page.getByText(/fixture confidential SQL/)).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}
