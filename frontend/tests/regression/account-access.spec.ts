import { expect, test } from "@playwright/test";

for (const locale of ["en", "zh-CN"] as const) {
  test(`shows own business/database grants, expiry and requests in ${locale}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    let revoked = false, broken = false, account = "alice";
    const cursors: (string | null)[] = [];
    const profile = () => ({ id: account, username: account, roles: ["viewer"], capabilities: ["metadata.read", "query.submit"], locale });
    const item = (id: string) => ({ dataset_id: id, datasource_id: "db", datasource_name: "Warehouse", datasource_kind: "postgres", datasource_enabled: true,
      catalog: "analytics", namespace: "public", qualifier: `public.${id}`, domain: id === "revenue" ? "广告变现" : null, actions: ["discover", "query"], query_available: true,
      grants: [{ role: "viewer", action: "discover", expires_at: null }, { role: "analyst", action: "query", expires_at: "2026-10-20T00:00:00Z" }] });
    if (locale === "zh-CN") await page.setViewportSize({ width: 390, height: 844 });
    await page.clock.install();
    await page.route("**/api/v1/**", async route => {
      const url = new URL(route.request().url());
      let body: unknown = { items: [] };
      if (url.pathname.endsWith("/auth/me")) body = profile();
      if (url.pathname.endsWith("/auth/me/access")) {
        cursors.push(url.searchParams.get("cursor"));
        if (broken) { await route.fulfill({ status: 503, json: { error: { code: "SERVICE_NOT_READY", message: "unavailable" } } }); return; }
        const empty = revoked || account !== "alice";
        body = { profile: profile(), active: true, created_at: "2026-10-18T00:00:00Z", last_login_at: "2026-10-18T01:00:00Z", checked_at: "2026-10-18T02:00:00Z",
          roles: [{ name: "viewer", capabilities: ["query.submit"] }], datasource_count: empty ? 0 : 1, total: empty ? 0 : 2,
          items: empty ? [] : [item(url.searchParams.has("cursor") ? "cost" : "revenue")], next_cursor: empty || url.searchParams.has("cursor") ? null : "next",
          domains: empty ? [] : [{ domain: "广告变现", dataset_count: 1, queryable_count: 1 }, { domain: null, dataset_count: 1, queryable_count: 1 }] };
      }
      if (url.pathname.endsWith("/permission-requests")) body = {
        items: account !== "alice" ? [] : [{ id: url.searchParams.has("cursor") ? "r2" : "r1", dataset_id: "requested-table", reason: url.searchParams.has("cursor") ? "Quarter report" : "Monthly report", status: "REQUESTED", created_at: "2026-10-18T00:00:00Z" }],
        next_cursor: account !== "alice" || url.searchParams.has("cursor") ? null : "requests-next",
      };
      await route.fulfill({ json: body });
    });
    await page.goto("/profile");
    await expect(page).toHaveURL(/\/settings$/);
    const access = page.getByTestId("account-access");
    await expect(access.getByRole("heading", { name: locale === "en" ? "My data access" : "我的数据权限" })).toBeVisible();
    await expect(access.getByRole("link", { name: "public.revenue" })).toHaveAttribute("href", "/datasets/revenue");
    await expect(access).toContainText(locale === "en" ? "No expiry" : "长期有效");
    await expect(access).toContainText("analyst");
    await expect(page.getByTestId("access-domains")).toContainText("广告变现");
    await expect(page.getByTestId("access-domains")).toContainText(locale === "en" ? "No declared domain" : "未声明业务域");
    await expect(page.getByTestId("my-permission-requests")).toContainText("Monthly report");
    await expect(access).toContainText("analytics");
    await page.getByRole("button", { name: locale === "en" ? "Load more requests" : "加载更多申请" }).click();
    await expect(page.getByTestId("my-permission-requests")).toContainText("Quarter report");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await access.getByRole("button", { name: locale === "en" ? "Load more access" : "加载更多权限" }).click();
    await expect(page.getByTestId("access-dataset")).toHaveCount(2);
    expect(cursors).toContain("next");
    broken = true;
    await access.getByRole("button", { name: locale === "en" ? "Refresh access" : "刷新权限" }).click();
    await expect(access.getByRole("alert")).toBeVisible();
    await expect(page.getByTestId("access-dataset")).toHaveCount(0);
    broken = false; revoked = true;
    await access.getByRole("button", { name: locale === "en" ? "Refresh access" : "刷新权限" }).click();
    await expect(access).toContainText(locale === "en" ? "This account has no effective data grants." : "当前账号暂无有效数据授权。");
    revoked = false;
    await page.clock.fastForward(16000);
    await expect(page.getByTestId("access-dataset")).toHaveCount(1);
    account = "bob";
    await page.reload();
    await expect(page.getByTestId("settings-username")).toHaveText("bob");
    await expect(page.getByTestId("access-dataset")).toHaveCount(0);
    await expect(page.getByTestId("my-permission-requests")).not.toContainText("Monthly report");
    expect(errors).toEqual([]);
  });
}
