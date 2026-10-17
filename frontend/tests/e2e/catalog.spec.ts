import { expect, test, type Page } from "@playwright/test";

/**
 * Live catalog journey: the filters must narrow the real, permission-filtered
 * catalog without inventing rows. Data-independent assertions only - the counts
 * come from the page's own facet options, and every visible row has to match the
 * selected filter.
 *
 * Requires a running core stack and a refreshed catalog:
 *   E2E_BASE_URL=http://127.0.0.1:3000 npm run test:e2e
 */
const enabled = Boolean(process.env.E2E_BASE_URL);

const countLine = (page: Page) => page.getByTestId("catalog-count");

/** "shown of total" as the page states it, next to the rows it actually renders. */
async function readCounts(page: Page) {
  const text = await countLine(page).innerText();
  const match = /(\d+)\D+(\d+)/.exec(text);
  if (!match) throw new Error(`unreadable count line: ${text}`);
  return { shown: Number(match[1]), total: Number(match[2]) };
}

test.describe("Catalog filters", () => {
  test.skip(!enabled, "set E2E_BASE_URL to run the end-to-end journey");

  test("narrows by datasource, schema and business domain", async ({ page }) => {
    await page.goto("/catalog");
    await expect(page.locator(".dataset-list li").first()).toBeVisible();
    const rows = page.locator(".dataset-list li");
    const everything = await readCounts(page);
    expect(await rows.count()).toBe(everything.shown);

    // Datasource: every remaining row names the selected source, and the count
    // line agrees with the facet's own count.
    const sourceOption = page.locator('[data-testid="filter-datasource"] option').nth(1);
    const sourceLabel = (await sourceOption.innerText()).trim();
    const [, sourceName, sourceCount] = /^(.+?) · \w+ \((\d+)\)$/.exec(sourceLabel)!;
    await page.getByTestId("filter-datasource").selectOption(await sourceOption.getAttribute("value") as string);
    await expect(rows.first()).toBeVisible();
    expect(await rows.count()).toBe(Number(sourceCount));
    expect(await readCounts(page)).toEqual({ shown: Number(sourceCount), total: Number(sourceCount) });
    for (const meta of await page.locator(".dataset-meta").allInnerTexts()) {
      expect(meta).toContain(sourceName);
    }

    // Business domain: rows carry exactly the selected declaration.
    await page.getByTestId("filter-datasource").selectOption("");
    const domainOption = page.locator('[data-testid="filter-domain"] option').nth(1);
    const domain = (await domainOption.innerText()).replace(/\s*\(\d+\)$/, "");
    await page.getByTestId("filter-domain").selectOption(await domainOption.getAttribute("value") as string);
    await expect(rows.first()).toBeVisible();
    const chips = await page.getByTestId("row-domain").allInnerTexts();
    expect(chips.length).toBe(await rows.count());
    expect(new Set(chips)).toEqual(new Set([domain]));

    // Clearing restores the unfiltered catalog.
    await page.getByTestId("clear-filters").click();
    await expect(rows).toHaveCount(everything.shown);
    expect(await readCounts(page)).toEqual(everything);
    await expect(page.getByTestId("clear-filters")).toBeDisabled();
  });

  test("the undeclared-domain filter lists only tables without a declaration", async ({
    page,
  }) => {
    await page.goto("/catalog");
    const rows = page.locator(".dataset-list li");
    const option = page.locator('[data-testid="filter-domain"] option[value="none"]');
    await expect(option).toBeAttached();
    const count = Number(/\((\d+)\)/.exec(await option.innerText())![1]);
    await page.getByTestId("filter-domain").selectOption("none");
    await expect(rows.first()).toBeVisible();
    expect(await rows.count()).toBe(count);
    await expect(page.getByTestId("row-domain")).toHaveCount(0);
  });

  test("says so when no dataset matches the filters", async ({ page }) => {
    await page.goto("/catalog");
    await page.getByTestId("catalog-search").fill("no-such-table-anywhere");
    await expect(page.locator(".dataset-list li")).toHaveCount(0);
    await expect(countLine(page)).toContainText("0");
    await expect(page.locator(".catalog")).toContainText("No datasets match");
  });
});
