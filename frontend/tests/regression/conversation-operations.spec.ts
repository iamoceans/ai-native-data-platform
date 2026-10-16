import { expect, test, type Page } from "@playwright/test";
import type { ConversationMessage } from "../../src/api/endpoints";

test.use({ locale: "en-US" });

async function platform(page: Page, options: { messages?: number; failed?: boolean; result?: boolean; retryDelay?: number; retryFailures?: number } = {}) {
  const retries: { path: string; key?: string }[] = [];
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const count = options.messages ?? 0;
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    let body: unknown = { items: [] };
    if (path.endsWith("/auth/me")) body = { username: "reader", roles: [], capabilities: ["analysis.create"] };
    const sessions = ["first", "second"].map(id => ({ id, title: id, context_version: 1, active_turn_id: null,
      resolved_context: { metric_key: "revenue", current_start: "2026-09-11" }, skill_config: {}, updated_at: "2026-10-16" }));
    if (path.endsWith("/sessions")) body = { items: sessions };
    const session = sessions.find(s => path.endsWith(`/sessions/${s.id}`));
    if (session) body = session;
    if (path.endsWith("/metrics")) body = { items: [{ metric_key: "revenue", name: "Revenue", allowed_dimensions: ["country"] }] };
    if (path.endsWith("/messages")) {
      const before = Number(url.searchParams.get("cursor") ?? count + 1);
      const start = Math.max(1, before - 40);
      const items = Array.from({ length: Math.max(0, before - start) }, (_, i): ConversationMessage => ({
        id: `m${start + i}`, sequence: start + i, role: "user", content: { kind: "user", text: `message ${start + i}` },
        created_at: "2026-10-16T00:00:00Z", content_schema_version: 1, turn_id: null,
      }));
      if (options.failed) items.push({ id: "failed", sequence: 1, role: "user", content: { kind: "user", text: "original question" },
        created_at: "2026-10-16T00:00:00Z", content_schema_version: 1, turn_id: "failed-turn", turn_status: "FAILED" });
      if (options.result) items.push({ id: "result", sequence: 1, role: "assistant", content: { kind: "analysis_result", analysis_id: "result", status: "COMPLETED", claims: [{ id: "c", text: "A stored claim" }] },
        created_at: "2026-10-16T00:00:00Z", content_schema_version: 1, turn_id: null });
      body = { items, next_cursor: start > 1 ? String(start) : null };
    }
    if (path.endsWith("/analyses/result")) body = { analysis_id: "result", status: "COMPLETED", question: "question", steps: [],
      budget: { input_tokens: 120, max_input_tokens: 1000, output_tokens: 20, max_output_tokens: 500, queries: 2, max_queries: 12 },
      state: { model_id: "configured-model" }, final_report: { chart_ids: [] } };
    if (path.endsWith("/retry")) {
      retries.push({ path, key: request.headers()["idempotency-key"] });
      if (options.retryDelay) await new Promise(resolve => setTimeout(resolve, options.retryDelay));
      if (retries.length <= (options.retryFailures ?? 0)) {
        await route.fulfill({ status: 503, json: { error: { code: "INTERNAL_ERROR", message: "response unavailable", retryable: true } } });
        return;
      }
      body = { id: "new-turn", session_id: "first", status: "QUEUED" };
    }
    if (path.endsWith("/events")) { await route.fulfill({ contentType: "text/event-stream", body: "" }); return; }
    await route.fulfill({ json: body });
  });
  await page.goto("/ask/first");
  return { retries, errors };
}

test("reads the latest window first and loads all 130 messages in chronological order", async ({ page }) => {
  const { errors } = await platform(page, { messages: 130 });
  const rows = page.getByTestId("message-user");
  await expect(rows).toHaveCount(40);
  await expect(rows.first()).toContainText("message 91");
  await expect(rows.last()).toContainText("message 130");
  for (const count of [80, 120, 130]) {
    await page.getByTestId("load-older-messages").click();
    await expect(rows).toHaveCount(count);
  }
  await expect(page.getByTestId("load-older-messages")).toHaveCount(0);
  expect(await rows.locator(".message-text").allTextContents()).toEqual(Array.from({ length: 130 }, (_, index) => `message ${index + 1}`));
  await page.getByTestId("session-second").click();
  await expect(rows).toHaveCount(40);
  expect(errors).toEqual([]);
});

test("retry submits a new action once without replacing the current question draft", async ({ page }) => {
  const { retries } = await platform(page, { failed: true, retryDelay: 300 });
  await page.getByTestId("conversation-input").fill("my unsent next question");
  await page.getByTestId("retry-turn").click();
  await expect(page.getByTestId("retry-turn")).toBeDisabled();
  await expect.poll(() => retries.length).toBe(1);
  await expect(page.getByTestId("conversation-input")).toBeEnabled();
  await expect(page.getByTestId("conversation-input")).toHaveValue("my unsent next question");
  expect(retries[0].path).toBe("/api/v1/sessions/first/turns/failed-turn/retry");
  expect(retries[0].key).toBeTruthy();
});

test("retry keeps its idempotency key after an ambiguous failure and releases it after success", async ({ page }) => {
  const { retries } = await platform(page, { failed: true, retryFailures: 1 });
  await page.getByTestId("conversation-input").fill("unsent question");
  await page.getByTestId("retry-turn").click();
  await expect(page.getByTestId("conversation-error")).toBeVisible();
  await page.getByTestId("retry-turn").click();
  await expect(page.getByTestId("conversation-error")).toHaveCount(0);
  expect(retries).toHaveLength(2);
  expect(retries[0].key).toBe(retries[1].key);
  await expect(page.getByTestId("conversation-input")).toHaveValue("unsent question");
  // The fixture leaves the failed original visible so a later explicit action
  // can prove that a confirmed success starts a new operation.
  await page.getByTestId("retry-turn").click();
  await expect.poll(() => retries.length).toBe(3);
  expect(retries[2].key).not.toBe(retries[1].key);
});

test("unresolved retries retain separate keys when switching conversations", async ({ page }) => {
  const { retries } = await platform(page, { failed: true, retryFailures: 3 });
  await page.getByTestId("retry-turn").click();
  await expect(page.getByTestId("conversation-error")).toBeVisible();
  await page.getByTestId("session-second").click();
  await page.getByTestId("retry-turn").click();
  await expect(page.getByTestId("conversation-error")).toBeVisible();
  await page.getByTestId("session-first").click();
  await page.getByTestId("retry-turn").click();
  await expect.poll(() => retries.length).toBe(3);
  expect(retries[0].key).not.toBe(retries[1].key);
  expect(retries[0].key).toBe(retries[2].key);
});

test("mobile scope is a modal with Escape, focus return and preserved drafts", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { errors } = await platform(page);
  const trigger = page.getByTestId("scope-panel-toggle");
  await expect(page.getByTestId("scope-current-start")).not.toBeVisible();
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Scope and skills" });
  await expect(dialog).toBeVisible();
  await page.getByTestId("scope-current-start").fill("2026-10-01");
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(page.getByTestId("scope-current-start")).toHaveValue("2026-10-01");
  await page.getByTestId("scope-drawer-close").focus();
  await page.keyboard.press("Shift+Tab");
  expect(await page.evaluate(() => Boolean(document.activeElement?.closest("dialog")))).toBe(true);
  await page.setViewportSize({ width: 844, height: 390 });
  await expect(page.getByTestId("scope-current-start")).toBeVisible();
  await expect(page.getByTestId("scope-current-start")).toHaveValue("2026-10-01");
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe("");
  expect(errors).toEqual([]);
});

test("a result message exposes token and query budgets without leaving the conversation", async ({ page }) => {
  await platform(page, { result: true });
  const details = page.getByTestId("analysis-diagnostics");
  await details.getByText("Turn diagnostics", { exact: true }).click();
  await expect(details).toContainText("120 / 1000");
  await expect(details).toContainText("20 / 500");
  await expect(details).toContainText("2 / 12");
  await expect(details).toContainText("configured-model");
  await expect(page).toHaveURL(/\/ask\/first$/);
});
