import { expect, test, type Page } from "@playwright/test";

test.use({ locale: "en-US" });

async function platform(page: Page, options: { count?: number; truncated?: boolean; locale?: "en" | "zh-CN" } = {}) {
  const errors: string[] = [];
  const reads: { id: string; limit: number; offset: number }[] = [];
  let failure: string | null = null;
  let delayed = false;
  let release: (() => void) | undefined;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = { items: [] };
    if (path.endsWith("/auth/me")) body = { id: "owner", username: "owner", roles: ["viewer"], capabilities: ["query.submit"], locale: options.locale ?? "en" };
    if (path.endsWith("/datasources")) body = { items: [{ id: "db", name: "Warehouse", kind: "postgres" }] };
    if (/\/queries\/[^/]+$/.test(path)) body = { id: path.split("/").at(-1), datasource_id: "db", sql: "SELECT value", status: "SUCCEEDED", attempt: 1, created_at: "2026-10-18T00:00:00Z", finished_at: "2026-10-18T00:00:01Z" };
    if (path.endsWith("/results")) {
      const id = path.split("/").at(-2)!;
      const limit = Number(url.searchParams.get("limit"));
      const offset = Number(url.searchParams.get("cursor")?.replace("cursor-", "") ?? 0);
      reads.push({ id, limit, offset });
      if (delayed) { delayed = false; await new Promise<void>(resolve => { release = resolve; }); }
      if (failure) {
        await route.fulfill({ status: failure === "RESULT_EXPIRED" ? 410 : 403, json: { error: { code: failure, message: "fixture failure" } } });
        return;
      }
      const count = options.count ?? 123;
      body = { query_id: id, status: "SUCCEEDED", result: { id: `result-${id}`, row_count: count,
        columns: [{ id: "value", name: "value", type: "INT" }],
        rows: Array.from({ length: Math.max(0, Math.min(limit, count - offset)) }, (_, index) => [`${id}-${offset + index + 1}`]),
        truncated: options.truncated ?? false, next_cursor: offset + limit < count ? `cursor-${offset + limit}` : null,
        expires_at: "2099-01-01T00:00:00Z" }, warnings: [] };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/sql?query=first");
  return { reads, errors, fail: (code: string | null) => { failure = code; },
    delay: () => { delayed = true; }, release: () => { delayed = false; release?.(); } };
}

const numbers = (page: Page) => page.getByTestId("result-row-number");

test("numbers rows across sequential pages and refetches remembered previous cursors", async ({ page }) => {
  const { reads, errors } = await platform(page, { truncated: true });
  await expect(numbers(page)).toHaveCount(50);
  await expect(numbers(page).first()).toHaveText("1");
  await expect(page.getByTestId("result-page-count")).toHaveText("Page 1 of 3 · 123 rows");
  await expect(page.getByTestId("result-previous")).toBeDisabled();
  await expect(page.getByText(/Result was truncated/)).toBeVisible();
  await page.getByTestId("result-next").click();
  await expect(numbers(page).first()).toHaveText("51");
  await expect(numbers(page).last()).toHaveText("100");
  await page.getByTestId("result-next").click();
  await expect(numbers(page)).toHaveCount(23);
  await expect(numbers(page).first()).toHaveText("101");
  await expect(numbers(page).last()).toHaveText("123");
  await expect(page.getByTestId("result-next")).toBeDisabled();
  await expect(page.getByTestId("result-page-count")).toHaveText("Page 3 of 3 · 123 rows");
  await page.getByTestId("result-previous").click();
  await expect(numbers(page).first()).toHaveText("51");
  await page.getByTestId("result-previous").click();
  await expect(numbers(page).first()).toHaveText("1");
  expect(reads.map(read => read.offset)).toEqual([0, 50, 100, 50, 0]);
  expect(errors).toEqual([]);
});

test("resets page and cursor stack for all sizes and query URL changes", async ({ page }) => {
  await platform(page);
  await expect(numbers(page)).toHaveCount(50);
  await page.getByTestId("result-next").click();
  await expect(numbers(page).first()).toHaveText("51");
  await page.getByTestId("result-page-size").selectOption("20");
  await expect(numbers(page)).toHaveCount(20);
  await expect(numbers(page).first()).toHaveText("1");
  await expect(page.getByTestId("result-page-count")).toHaveText("Page 1 of 7 · 123 rows");
  await page.getByTestId("result-next").click();
  await expect(numbers(page).first()).toHaveText("21");
  await page.getByTestId("result-page-size").selectOption("100");
  await expect(numbers(page)).toHaveCount(100);
  await expect(numbers(page).first()).toHaveText("1");
  await expect(page.getByTestId("result-page-count")).toHaveText("Page 1 of 2 · 123 rows");
  // Change the URL without a full reload, as a history/deep-link selection does.
  await page.evaluate(() => { window.history.pushState({}, "", "/sql?query=second"); window.dispatchEvent(new PopStateEvent("popstate")); });
  await expect(page.getByTestId("result-page-size")).toHaveValue("50");
  await expect(numbers(page)).toHaveCount(50);
  await expect(page.locator(".result-table tbody td").first()).toHaveText("second-1");
  await page.goBack();
  await expect(page.locator(".result-table tbody td").first()).toHaveText("first-1");
  await expect(numbers(page).first()).toHaveText("1");
});

test("handles empty results without a phantom next page", async ({ page }) => {
  await platform(page, { count: 0 });
  await expect(page.getByText("No rows returned.")).toBeVisible();
  await expect(numbers(page)).toHaveCount(0);
  await expect(page.getByTestId("result-page-count")).toHaveText("Page 1 of 1 · 0 rows");
  await expect(page.getByTestId("result-previous")).toBeDisabled();
  await expect(page.getByTestId("result-next")).toBeDisabled();
});

for (const code of ["RESULT_EXPIRED", "PERMISSION_DENIED"]) {
  test(`hides old rows when a later or previous read fails with ${code}`, async ({ page }) => {
    const server = await platform(page);
    await expect(numbers(page)).toHaveCount(50);
    await page.getByTestId("result-next").click();
    await expect(numbers(page).first()).toHaveText("51");
    server.fail(code);
    await page.getByTestId("result-previous").click();
    await expect(page.getByTestId("result-error")).toContainText(code);
    await expect(numbers(page)).toHaveCount(0);
    await expect(page.getByTestId("result-next")).toBeDisabled();
    server.fail(null);
    await page.getByTestId("result-retry").click();
    await expect(numbers(page).first()).toHaveText("1");
    expect(server.errors).toEqual([]);
  });
}

test("ignores an old response after resizing while a page is loading", async ({ page }) => {
  const server = await platform(page);
  await expect(numbers(page)).toHaveCount(50);
  server.delay();
  await page.getByTestId("result-next").click();
  await expect(page.getByTestId("result-next")).toBeDisabled();
  await expect.poll(() => server.reads.length).toBe(2);
  await page.getByTestId("result-page-size").selectOption("20");
  await expect(numbers(page)).toHaveCount(20);
  const oldResponse = page.waitForResponse(response => response.url().includes("cursor=cursor-50"));
  server.release();
  await oldResponse;
  await expect(numbers(page).first()).toHaveText("1");
  await expect(page.getByTestId("result-page-count")).toHaveText("Page 1 of 7 · 123 rows");
  expect(server.errors).toEqual([]);
});


test("localizes pagination and keeps controls within a 390px viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await platform(page, { locale: "zh-CN" });
  await expect(page.getByTestId("result-page-count")).toHaveText("第 1 页 / 共 3 页 · 共 123 行");
  await expect(page.getByRole("combobox", { name: "每页行数" })).toHaveValue("50");
  await expect(page.getByTestId("result-next")).toHaveText("下一页");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
