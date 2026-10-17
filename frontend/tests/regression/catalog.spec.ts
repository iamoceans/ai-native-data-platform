import { expect, test, type Page } from "@playwright/test";

test.use({ locale: "zh-CN" });

/**
 * Catalog filter regressions. The list used to be one flat run of every
 * discoverable table: you could only narrow it by typing a name. It now filters
 * by datasource, schema and declared business domain, and the row shows the
 * declarations that make a table addressable in Ask data.
 */
const DATASETS = [
  {
    id: "d1",
    datasource_id: "pg",
    catalog: "internal",
    schema_name: "demo",
    object_name: "ads_revenue_daily",
    object_type: "table",
    sync_status: "SYNCED",
    schema_hash: null,
    datahub_urn: null,
    active: true,
    last_synced_at: null,
    domain: "广告变现",
  },
  {
    id: "d2",
    datasource_id: "pg",
    catalog: "internal",
    schema_name: "demo",
    object_name: "user_daily",
    object_type: "table",
    sync_status: "SYNCED",
    schema_hash: null,
    datahub_urn: null,
    active: true,
    last_synced_at: null,
    domain: "用户增长",
  },
  {
    id: "d3",
    datasource_id: "my",
    catalog: "internal",
    schema_name: "ainative_source",
    object_name: "app_release_config",
    object_type: "table",
    sync_status: "SYNCED",
    schema_hash: null,
    datahub_urn: null,
    active: true,
    last_synced_at: null,
    domain: "版本发布",
  },
  {
    id: "d4",
    datasource_id: "my",
    catalog: "internal",
    schema_name: "public",
    object_name: "fixture_heavy",
    object_type: "table",
    sync_status: "SYNCED",
    schema_hash: null,
    datahub_urn: null,
    active: true,
    last_synced_at: null,
    domain: null,
  },
];

const FACETS = {
  datasources: [
    { id: "pg", name: "核心 PG", kind: "postgres", count: 2 },
    { id: "my", name: "演示 MySQL", kind: "mysql", count: 2 },
  ],
  schemas: [
    { value: "ainative_source", count: 1 },
    { value: "demo", count: 2 },
    { value: "public", count: 1 },
  ],
  domains: [
    { value: "广告变现", count: 1 },
    { value: "用户增长", count: 1 },
    { value: "版本发布", count: 1 },
  ],
  undeclared_domain: 1,
};

async function mockCatalog(page: Page) {
  const listUrls: string[] = [];
  await page.context().route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    let body: unknown = { items: [] };
    if (path.endsWith("/auth/me"))
      body = { username: "test", roles: [], capabilities: ["analysis.create"] };
    else if (path.endsWith("/datasets/facets")) body = FACETS;
    else if (path.endsWith("/datasets")) {
      listUrls.push(url.search);
      const q = url.searchParams.get("q");
      const source = url.searchParams.get("datasource_id");
      const schema = url.searchParams.get("schema");
      const domain = url.searchParams.get("domain");
      const items = DATASETS.filter(
        (row) =>
          (!q || row.object_name.includes(q)) &&
          (!source || row.datasource_id === source) &&
          (!schema || row.schema_name === schema) &&
          (!domain || (domain === "none" ? !row.domain : row.domain === domain)),
      );
      body = { items, total: items.length, next_cursor: null };
    } else if (/\/datasets\/[\w-]+$/.test(path)) {
      const row = DATASETS.find((item) => path.endsWith(item.id))!;
      body = { ...row, columns: [] };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/catalog");
  return listUrls;
}

test("filters the catalog by datasource, schema and business domain", async ({
  page,
}) => {
  const listUrls = await mockCatalog(page);
  await expect(page.getByTestId("catalog-count")).toContainText(
    "显示 4 / 共 4 个可发现数据集",
  );

  await page.getByTestId("filter-datasource").selectOption("my");
  await expect(page.locator(".dataset-name")).toHaveText([
    "ainative_source.app_release_config",
    "public.fixture_heavy",
  ]);
  expect(listUrls.at(-1)).toContain("datasource_id=my");
  // The datasource and the object type are adjacent in the row: they used to be
  // two bare text nodes, which flex merged into one run ("演示 MySQLtable").
  await expect(page.getByTestId("row-datasource").first()).toHaveText(
    "演示 MySQL ·",
  );

  await page.getByTestId("filter-datasource").selectOption("");
  await page.getByTestId("filter-schema").selectOption("demo");
  await expect(page.locator(".dataset-name")).toHaveText([
    "demo.ads_revenue_daily",
    "demo.user_daily",
  ]);
  expect(listUrls.at(-1)).toContain("schema=demo");

  // Filters combine: demo has two tables but only one sits in 用户增长.
  await page.getByTestId("filter-domain").selectOption("用户增长");
  await expect(page.locator(".dataset-name")).toHaveText(["demo.user_daily"]);
  expect(decodeURIComponent(listUrls.at(-1)!)).toContain("domain=用户增长");
  await expect(page.getByTestId("catalog-count")).toContainText(
    "显示 1 / 共 1 个可发现数据集",
  );
});

test("offers the undeclared-domain filter and clears every filter", async ({
  page,
}) => {
  await mockCatalog(page);
  await page.getByTestId("filter-domain").selectOption("none");
  await expect(page.locator(".dataset-name")).toHaveText([
    "public.fixture_heavy",
  ]);
  await expect(page.getByTestId("row-domain")).toHaveCount(0);

  await page.getByTestId("clear-filters").click();
  await expect(page.locator(".dataset-name")).toHaveCount(4);
  await expect(page.getByTestId("catalog-search")).toHaveValue("");
  await expect(page.getByTestId("clear-filters")).toBeDisabled();
});

test("labels the row and the detail panel with the declared domain", async ({
  page,
}) => {
  await mockCatalog(page);
  const ads = page.locator("li", { hasText: "demo.ads_revenue_daily" });
  await expect(ads.getByTestId("row-domain")).toHaveText("广告变现");
  await expect(
    page.locator("li", { hasText: "public.fixture_heavy" }).getByTestId("row-domain"),
  ).toHaveCount(0);

  await page.locator("button", { hasText: "demo.ads_revenue_daily" }).click();
  await expect(page.getByTestId("context-domain")).toContainText("业务域 广告变现");
});

test("says that filters, not permissions, emptied the list", async ({ page }) => {
  await mockCatalog(page);
  await page.getByTestId("catalog-search").fill("nothing-matches");
  await expect(page.locator(".dataset-list li")).toHaveCount(0);
  await expect(page.locator(".card").first()).toContainText(
    "当前筛选条件下没有可见的数据集",
  );
  await expect(page.locator(".card").first()).not.toContainText(
    "No datasets visible",
  );
});

test("keeps the filter bar and the labelled rows inside the viewport", async ({
  page,
}) => {
  await mockCatalog(page);
  for (const width of [1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.locator(".dataset-list li").first()).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow, `horizontal overflow at ${width}px`).toBeLessThanOrEqual(0);
  }
  // The three selects stack instead of shrinking below readability, and the row
  // metadata wraps as a group: a squeezed chip renders one character per line.
  await page.setViewportSize({ width: 390, height: 900 });
  const boxes = await page.locator(".filter-bar label").evaluateAll((nodes) =>
    nodes.map((node) => node.getBoundingClientRect().width),
  );
  expect(Math.min(...boxes)).toBeGreaterThan(200);
  const chip = await page.getByTestId("row-domain").first().boundingBox();
  expect(chip!.width).toBeGreaterThan(chip!.height);
  const rowOverflow = await page.locator(".dataset").evaluateAll((nodes) =>
    nodes.map((node) => node.scrollWidth - node.clientWidth),
  );
  expect(Math.max(...rowOverflow)).toBeLessThanOrEqual(0);
});

test.describe("English locale", () => {
  test.use({ locale: "en-US" });

  test("localizes the filter labels and the result count", async ({ page }) => {
    await mockCatalog(page);
    await expect(page.getByTestId("filter-domain")).toHaveValue("");
    await expect(page.locator(".filter-bar")).toContainText("Business domain");
    await expect(page.getByTestId("clear-filters")).toHaveText("Clear filters");
    await expect(page.getByTestId("catalog-count")).toContainText(
      "Showing 4 of 4 discoverable datasets",
    );
  });
});
