import { expect, test, type Page } from "@playwright/test";

test.use({ locale: "en-US" });

async function mockPlatform(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const requests: { path: string; language?: string; body: unknown }[] = [];
  let profile = {
    id: "admin",
    username: "测试管理员",
    display_name: "",
    locale: null as "zh-CN" | "en" | null,
    timezone: null as string | null,
    settings_version: 1,
    roles: ["admin"],
    capabilities: ["admin.manage", "analysis.create"],
  };
  await page.context().route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    requests.push({
      path,
      language: request.headers()["accept-language"],
      body: request.postData(),
    });
    let body: unknown = { items: [] };
    if (path.endsWith("/auth/me")) {
      if (request.method() === "PATCH") {
        const { expected_settings_version, ...fields } = request.postDataJSON();
        profile = {
          ...profile,
          ...fields,
          settings_version: expected_settings_version + 1,
        };
      }
      body = profile;
    }
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
/** Signed-in users change the language in Settings; the top bar has no control. */
async function setLocale(page: Page, locale: "zh-CN" | "en") {
  await page.goto("/settings");
  await page.getByTestId("settings-locale").selectOption(locale);
  await page.getByTestId("settings-save").click();
  await expect(page.getByTestId("settings-saved")).toBeVisible();
}
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
    // The signed-in shell has no language control: Settings owns it.
    await setLocale(page, "zh-CN");
    await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
    // The product name is not translated: the same string in both locales.
    await expect(page).toHaveTitle("Oceanus");
    await expect(page.locator(".topbar .brand")).toHaveText("Oceanus");
    await page.goto(path);
    await expect(
      page.getByRole("heading", { name: zh, exact: true, level: 1 }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "智能问数", exact: true }),
    ).toBeVisible();
    if (path === "/analyses/report")
      await expect(page.locator(".claim p")).toHaveText("原始证据结论 123.45");
    await setLocale(page, "en");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page).toHaveTitle("Oceanus");
    await expect(page.locator(".topbar .brand")).toHaveText("Oceanus");
    await page.goto(path);
    await expect(
      page.getByRole("heading", { name: en, exact: true, level: 1 }),
    ).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test("persists language across navigation and reload while retaining SQL and errors", async ({
  page,
  context,
}) => {
  const { requests } = await mockPlatform(page);
  await page.goto("/sql");
  const sql = "SELECT '中文原始内容' AS value";
  await page.getByTestId("sql-editor").fill(sql);
  await page.getByTestId("datasource-select").selectOption("source");
  // The preference is account-scoped and set in Settings, so it arrives from
  // another tab: this workspace must re-render without losing its draft.
  const other = await context.newPage();
  await other.goto("/settings");
  await other.getByTestId("settings-locale").selectOption("zh-CN");
  await other.getByTestId("settings-save").click();
  await expect(other.getByTestId("settings-saved")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.getByTestId("sql-editor")).toHaveValue(sql);
  await expect(page.getByTestId("datasource-select")).toHaveValue("source");
  await page.getByTestId("run-query").click();
  await expect(page.getByTestId("submit-error")).toContainText(
    "此 SQL 不符合只读安全策略。",
  );
  expect(
    requests.find((r) => r.path.endsWith("/queries") && r.body)?.language,
  ).toBe("zh-CN");
  await other.getByTestId("settings-locale").selectOption("en");
  await other.getByTestId("settings-save").click();
  await expect(other.getByTestId("settings-saved")).toBeVisible();
  await expect(page.getByTestId("submit-error")).toContainText(
    "This SQL statement violates the read-only policy.",
  );
  await expect(page.getByTestId("submit-error")).toContainText("SQL_FORBIDDEN");
  await other.getByTestId("settings-locale").selectOption("zh-CN");
  await other.getByTestId("settings-save").click();
  await expect(other.getByTestId("settings-saved")).toBeVisible();
  await other.close();
  await page.getByRole("link", { name: "历史记录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "查询历史" })).toBeVisible();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(
    page.locator("select").last().locator("option[value=RUNNING]"),
  ).toHaveText("运行中");
});

test("the sign-in screen follows the guest language and localizes authentication errors", async ({
  page,
}) => {
  await mockPlatform(page);
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByTestId("login-username").fill("my-user");
  await page.getByTestId("login-password").fill("test-password");
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("login-error")).toContainText(
    "Please sign in to continue.",
  );
  // The sign-in screen carries no language control: a stored guest preference
  // outranks the browser language until an account preference takes over.
  await page.addInitScript(() => {
    try {
      localStorage.setItem("ainative.locale", "zh-CN");
    } catch {
      /* The initial about:blank document has no storage. */
    }
  });
  await page.reload();
  await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
  await expect(page.getByTestId("language-switcher")).toHaveCount(0);
  await page.getByTestId("login-username").fill("my-user");
  await page.getByTestId("login-password").fill("test-password");
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("login-error")).toContainText(
    "请重新登录后继续。",
  );
});

test("synchronizes language changes between tabs", async ({
  page,
  context,
}) => {
  await mockPlatform(page);
  await page.goto("/sql");
  const other = await context.newPage();
  await other.goto("/settings");
  await other.getByTestId("settings-locale").selectOption("zh-CN");
  await other.getByTestId("settings-save").click();
  await expect(other.getByTestId("settings-saved")).toBeVisible();
  await expect(page.getByRole("heading", { name: "SQL 工作台" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
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
  expect(errors).toEqual([]);
  await context.close();
});

test("keeps the sign-in card inside the viewport at 390px", async ({ page }) => {
  await mockPlatform(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  const card = page.locator(".auth-card");
  await expect(card).toBeVisible();
  const box = await card.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
