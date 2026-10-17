import { expect, test, type Page } from "@playwright/test";

test.use({ locale: "en-US" });

async function mockPlatform(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const requests: { path: string; language?: string; body: unknown }[] = [];
  await page.context().route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    requests.push({
      path,
      language: request.headers()["accept-language"],
      body: request.postData(),
    });
    let body: unknown = { items: [] };
    if (path.endsWith("/auth/me"))
      body = {
        username: "测试管理员",
        roles: ["admin"],
        capabilities: ["admin.manage", "analysis.create"],
      };
    if (path.endsWith("/datasources"))
      body = {
        items: [
          {
            id: "source",
            name: "原始数据源",
            kind: "postgres",
            enabled: true,
            health_status: "HEALTHY",
            connection_config: {},
            capabilities: {},
          },
        ],
      };
    if (path.endsWith("/admin/roles")) body = [];
    if (path.endsWith("/sessions/first"))
      body = {
        id: "first",
        title: "用户会话标题",
        context_version: 1,
        active_turn_id: null,
        resolved_context: {},
        skill_config: {},
        updated_at: "2026-10-14T10:00:00Z",
      };
    if (path.endsWith("/analyses/report"))
      body = {
        analysis_id: "report",
        status: "COMPLETED",
        question: "用户原始问题",
        checkpoint_version: 1,
        policy_revision: 1,
        steps: [],
        state: {},
        final_report: {
          status: "COMPLETED",
          claims: [
            { id: "c1", kind: "observed_change", text: "原始证据结论 123.45" },
          ],
          limitations: [],
        },
      };
    if (path.endsWith("/analyses/report/memory"))
      body = {
        used: [],
        learned: [],
        reinforced: [],
        extraction: { status: "extracted", model_id: "test" },
      };
    if (path.endsWith("/queries") && request.method() === "POST") {
      await route.fulfill({
        status: 403,
        json: {
          error: {
            code: "SQL_FORBIDDEN",
            message: "Internal SQL diagnostic",
            retryable: false,
            details: {},
          },
        },
      });
      return;
    }
    if (path.endsWith("/auth/login")) {
      await route.fulfill({
        status: 401,
        json: {
          error: {
            code: "UNAUTHENTICATED",
            message: "invalid credentials",
            retryable: false,
            details: {},
          },
        },
      });
      return;
    }
    if (path.endsWith("/events")) {
      await route.fulfill({ contentType: "text/event-stream", body: "" });
      return;
    }
    await route.fulfill({ json: body });
  });
  return { errors, requests };
}

const routes = [
  ["/ask", "New conversation", "新对话"],
  ["/sql", "SQL workspace", "SQL 工作台"],
  ["/catalog", "Catalog", "数据目录"],
  ["/history", "Query history", "查询历史"],
  ["/skills", "Skills", "技能"],
  ["/admin/datasources", "Datasources", "数据源"],
  ["/admin/permissions", "Permissions", "权限管理"],
  ["/analyses/report", "用户原始问题", "用户原始问题"],
] as const;
for (const [path, en, zh] of routes) {
  test(`switches both languages on ${path}`, async ({ page }) => {
    const { errors } = await mockPlatform(page);
    await page.goto(path);
    await expect(
      page.getByRole("heading", { name: en, exact: true, level: 1 }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Ask data", exact: true }),
    ).toBeVisible();
    await page.getByTestId("language-switcher").selectOption("zh-CN");
    await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
    // The product name is not translated: the same string in both locales.
    await expect(page).toHaveTitle("Oceanus");
    await expect(page.locator(".topbar .brand")).toHaveText("Oceanus");
    await expect(
      page.getByRole("heading", { name: zh, exact: true, level: 1 }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "智能问数", exact: true }),
    ).toBeVisible();
    if (path === "/analyses/report")
      await expect(page.locator(".claim p")).toHaveText("原始证据结论 123.45");
    await page.getByTestId("language-switcher").selectOption("en");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page).toHaveTitle("Oceanus");
    await expect(page.locator(".topbar .brand")).toHaveText("Oceanus");
    await expect(
      page.getByRole("heading", { name: en, exact: true, level: 1 }),
    ).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test("persists language across navigation and reload while retaining SQL and errors", async ({
  page,
}) => {
  const { requests } = await mockPlatform(page);
  await page.goto("/sql");
  const sql = "SELECT '中文原始内容' AS value";
  await page.getByTestId("sql-editor").fill(sql);
  await page.getByTestId("datasource-select").selectOption("source");
  await page.getByTestId("language-switcher").selectOption("zh-CN");
  await expect(page.getByTestId("sql-editor")).toHaveValue(sql);
  await expect(page.getByTestId("datasource-select")).toHaveValue("source");
  await page.getByTestId("run-query").click();
  await expect(page.getByTestId("submit-error")).toContainText(
    "此 SQL 不符合只读安全策略。",
  );
  await page.getByTestId("language-switcher").selectOption("en");
  await expect(page.getByTestId("submit-error")).toContainText(
    "This SQL statement violates the read-only policy.",
  );
  await expect(page.getByTestId("submit-error")).toContainText("SQL_FORBIDDEN");
  expect(
    requests.find((r) => r.path.endsWith("/queries") && r.body)?.language,
  ).toBe("zh-CN");
  await page.getByTestId("language-switcher").selectOption("zh-CN");
  await page.getByRole("link", { name: "历史记录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "查询历史" })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("language-switcher")).toHaveValue("zh-CN");
  await expect(
    page.locator("select").last().locator("option[value=RUNNING]"),
  ).toHaveText("运行中");
});

test("login translates live, preserves credentials, and handles authentication errors", async ({
  page,
}) => {
  await mockPlatform(page);
  await page.goto("/login");
  await page.getByTestId("login-username").fill("my-user");
  await page.getByTestId("login-password").fill("test-password");
  await page.getByTestId("language-switcher").selectOption("zh-CN");
  await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
  await expect(page.getByTestId("login-username")).toHaveValue("my-user");
  await expect(page.getByTestId("login-password")).toHaveValue("test-password");
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("login-error")).toContainText(
    "请重新登录后继续。",
  );
  await page.getByTestId("language-switcher").selectOption("en");
  await expect(page.getByTestId("login-error")).toContainText(
    "Please sign in to continue.",
  );
});

test("synchronizes language changes between tabs", async ({
  page,
  context,
}) => {
  await mockPlatform(page);
  await page.goto("/sql");
  const other = await context.newPage();
  await other.goto("/history");
  await page.getByTestId("language-switcher").selectOption("zh-CN");
  await expect(other.getByRole("heading", { name: "查询历史" })).toBeVisible();
  await expect(other.getByTestId("language-switcher")).toHaveValue("zh-CN");
});

test("uses browser Chinese and survives unavailable storage", async ({
  browser,
}) => {
  const context = await browser.newContext({ locale: "zh-TW" });
  const page = await context.newPage();
  await page.addInitScript(() => {
    Object.defineProperty(Storage.prototype, "getItem", {
      value: () => {
        throw new Error("disabled");
      },
    });
    Object.defineProperty(Storage.prototype, "setItem", {
      value: () => {
        throw new Error("disabled");
      },
    });
  });
  const { errors } = await mockPlatform(page);
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
  await page.getByTestId("language-switcher").selectOption("en");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  expect(errors).toEqual([]);
  await context.close();
});

test("keeps the language control and navigation usable at 390px", async ({ page }) => {
  await mockPlatform(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/sql");
  for (const locale of ["zh-CN", "en"]) {
    await page.getByTestId("language-switcher").selectOption(locale);
    await expect(page.getByTestId("language-switcher")).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await page.screenshot({ path: "/tmp/platform-i18n-mobile.png", fullPage: true });
});

test("keeps the language label and its select on one line in the top bar", async ({
  page,
}) => {
  await mockPlatform(page);
  await page.goto("/sql");
  const switcher = page.locator(".topbar .language-switcher");
  await expect(switcher).toBeVisible();
  // The global `label` rule stacks the control under its label; this toolbar
  // item overrides that direction and must stay a single line.
  expect(
    await switcher.evaluate((el) => getComputedStyle(el).flexDirection),
  ).toBe("row");
  const label = await switcher.locator("span").boundingBox();
  const select = await page.getByTestId("language-switcher").boundingBox();
  expect(
    Math.abs(label!.y + label!.height / 2 - (select!.y + select!.height / 2)),
  ).toBeLessThan(3);
  const box = await switcher.boundingBox();
  expect(box!.height).toBeLessThan(40);
  expect(Math.abs(select!.x - label!.x - label!.width)).toBeLessThan(20);
});
