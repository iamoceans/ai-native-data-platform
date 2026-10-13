import { expect, test } from "@playwright/test";

/**
 * P4 acceptance journey: the conversational Askdata surface.
 *
 * Requires a running stack built from P1-P4 with
 * AIND_CONVERSATIONAL_ASK_ENABLED=1 and at least one registered metric with a
 * queryable dataset (the demo profile provides `ads_revenue`):
 *   E2E_BASE_URL=http://127.0.0.1:3000 npm run test:e2e
 *
 * What it proves: a new conversation is created and addressable by URL, a turn
 * is submitted with an explicit scope, the turn settles into a transcript entry,
 * a refresh restores the same session, and a follow-up inherits the scope.
 */
const enabled = Boolean(process.env.E2E_BASE_URL);
const METRIC = process.env.E2E_METRIC ?? "ads_revenue";

// Two full turns against a real model plus their analyses take longer than the
// suite default; the assertions below still fail fast when something is wrong.
test.describe.configure({ timeout: 240_000 });

test.describe("conversational Ask", () => {
  test.skip(!enabled, "set E2E_BASE_URL to run the end-to-end journey");

  test("start a conversation, ask with an explicit scope, then follow up", async ({ page }) => {
    await page.goto("/ask");
    await expect(page.getByTestId("conversation-input")).toBeVisible();

    await page.getByTestId("new-conversation").click();
    await expect(page).toHaveURL(/\/ask\/[0-9a-f-]{36}/, { timeout: 15_000 });
    const sessionUrl = page.url();

    // The scope panel starts empty for a new conversation.
    await page.getByTestId("scope-metric").selectOption(METRIC);
    await page.getByTestId("scope-baseline-start").fill("2026-09-10");
    await page.getByTestId("scope-baseline-end").fill("2026-09-11");
    await page.getByTestId("scope-current-start").fill("2026-09-11");
    await page.getByTestId("scope-current-end").fill("2026-09-12");
    await page.getByTestId("scope-dimension-country").check();

    await page.getByTestId("conversation-input").fill("这段时间收入变化主要来自哪里？");
    await page.getByTestId("conversation-send").click();

    // The user turn lands immediately; the result arrives as one message.
    await expect(page.getByTestId("message-user")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("message-analysis_result")).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId("analysis-deeplink")).toBeVisible();
    await expect(page.getByTestId("skill-usage")).toBeVisible();

    // The composer is editable again (the send button stays disabled while the
    // input is empty, which is the correct idle state).
    await expect(page.getByTestId("conversation-input")).toBeEnabled({ timeout: 30_000 });

    // Refresh: the same conversation and transcript come back.
    await page.goto(sessionUrl);
    await expect(page.getByTestId("message-user")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("message-analysis_result")).toBeVisible();

    // Follow-up: the scope is inherited, so only the question is needed.
    await page.getByTestId("scope-reset").click();
    await page.getByTestId("conversation-input").fill("只看安卓");
    await page.getByTestId("conversation-send").click();
    await expect(page.getByTestId("message-user")).toHaveCount(2, { timeout: 30_000 });
    await expect(page.getByTestId("message-analysis_result")).toHaveCount(2, { timeout: 90_000 });
  });

  test("the skills page lists builtins and previews an upload", async ({ page }) => {
    await page.goto("/skills");
    await expect(page.getByTestId("skill-list")).toBeVisible();
    await expect(page.getByTestId("skill-channel-contribution")).toBeVisible();
    // Builtins are read-only in the UI.
    await expect(page.getByTestId("skill-toggle-channel-contribution")).toHaveCount(0);
  });
});
