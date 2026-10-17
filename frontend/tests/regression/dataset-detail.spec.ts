import { expect, test, type Page } from "@playwright/test";

test.use({ locale: "en-US" });

async function mockCatalog(page: Page, denied = false) {
  const requests: string[] = [];
  const writes: unknown[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    requests.push(path);
    let body: unknown = { items: [] };
    if (path.endsWith("/auth/me"))
      body = { username: "reader", roles: ["analyst"], capabilities: [] };
    if (path.endsWith("/datasets"))
      body = { items: ["first", "second"].map((id) => ({
        id, schema_name: "demo", object_name: id, object_type: "TABLE", sync_status: "SYNCED",
      })) };
    const match = path.match(/\/datasets\/([^/]+)(?:\/(schema|lineage))?$/);
    if (match) {
      const [, id, resource] = match;
      if (denied || id === "missing") {
        await route.fulfill({ status: denied ? 403 : 404, json: { error: {
          code: denied ? "PERMISSION_DENIED" : "DATASET_NOT_FOUND",
          message: denied ? "Access denied" : "Dataset does not exist",
        } } });
        return;
      }
      body = resource === "schema"
        ? { source: "registry", columns: [{ name: `${id}_column`, type: "text", nullable: true }] }
        : resource === "lineage"
          ? { status: "no_upstream", nodes: [], labels: [], filtered_nodes: 0 }
          : { id, schema_name: "demo", object_name: id, schema_hash: "1234567890abcdef", grain: [], metric_keys: [] };
    }
    if (request.method() === "POST") {
      writes.push(request.postDataJSON());
      body = { id: "request", status: "REQUESTED" };
    }
    await route.fulfill({ json: body });
  });
  return { requests, writes };
}

test("catalog links open a durable dataset URL and survive reload", async ({ page }) => {
  const { requests } = await mockCatalog(page);
  await page.goto("/catalog");
  await page.getByRole("link", { name: /demo.first/ }).click();
  await expect(page).toHaveURL(/\/datasets\/first$/);
  await expect(page.getByRole("heading", { name: "demo.first" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "first_column" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "demo.first" })).toBeVisible();
  expect(requests.filter((p) => p.endsWith("/datasets/first")).length).toBeGreaterThanOrEqual(2);
  await page.getByRole("link", { name: "Back to catalog", exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await expect(page.getByRole("heading", { name: "demo.first" })).toHaveCount(0);
});

test("direct links work and browser history selects the right dataset", async ({ page }) => {
  await mockCatalog(page);
  await page.goto("/datasets/first");
  await expect(page.getByRole("cell", { name: "first_column" })).toBeVisible();
  await page.getByTestId("request-reason").fill("only for the first dataset");
  await page.getByRole("link", { name: /demo.second/ }).click();
  await expect(page.getByRole("heading", { name: "demo.second" })).toBeVisible();
  await expect(page.getByTestId("request-reason")).toHaveValue("");
  await expect(page.getByRole("cell", { name: "first_column" })).toHaveCount(0);
  await page.goBack();
  await expect(page.getByRole("heading", { name: "demo.first" })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole("heading", { name: "demo.second" })).toBeVisible();
});

for (const denied of [false, true]) {
  test(`direct link reports ${denied ? "denied" : "missing"} datasets without secondary reads`, async ({ page }) => {
    const { requests } = await mockCatalog(page, denied);
    await page.goto(`/datasets/${denied ? "first" : "missing"}`);
    await expect(page.getByTestId("dataset-error")).toBeVisible();
    await expect(page.getByTestId("request-access")).toHaveCount(0);
    await expect(page.getByText("Loading schema…", { exact: true })).toHaveCount(0);
    expect(requests.some((p) => /\/datasets\/[^/]+\/(schema|lineage)$/.test(p))).toBe(false);
  });
}

test("access requests use the current dataset and old notices do not follow navigation", async ({ page }) => {
  const { writes } = await mockCatalog(page);
  await page.goto("/datasets/first");
  await page.getByTestId("request-reason").fill("first reason");
  await page.getByTestId("request-access").click();
  await expect(page.getByTestId("request-notice")).toBeVisible();
  await page.getByRole("link", { name: /demo.second/ }).click();
  await expect(page.getByTestId("request-notice")).toHaveCount(0);
  await page.getByTestId("request-reason").fill("second reason");
  await page.getByTestId("request-access").click();
  await expect(page.getByTestId("request-notice")).toBeVisible();
  expect(writes).toEqual([
    { dataset_id: "first", reason: "first reason" },
    { dataset_id: "second", reason: "second reason" },
  ]);
  await page.getByTestId("language-switcher").selectOption("zh-CN");
  await expect(page.getByRole("link", { name: "返回数据目录", exact: true })).toBeVisible();
});
