import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test.use({ locale: 'en-US' });
test.skip(!process.env.E2E_BASE_URL, 'set E2E_BASE_URL for live workspace verification');

test('rename/archive/restore and saved SQL persist through the real service', async ({ page }) => {
  await page.goto('/ask');
  await page.getByTestId('new-conversation').click();
  await expect(page).toHaveURL(/\/ask\/[0-9a-f-]{36}$/);
  const sessionId = page.url().split('/').at(-1)!;
  const title = `Managed-${randomUUID().slice(0, 8)}`;
  await page.getByTestId(`rename-session-${sessionId}`).click();
  await page.getByTestId('session-title-input').fill(title);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByTestId('conversation-title')).toHaveText(title);
  await page.getByTestId(`archive-session-${sessionId}`).click();
  await expect(page.getByTestId('conversation-input')).toBeDisabled();
  await page.getByTestId('include-archived-sessions').check();
  await expect(page.getByTestId(`session-${sessionId}`)).toContainText('Archived');
  await page.getByTestId(`archive-session-${sessionId}`).click();
  await expect(page.getByTestId('conversation-input')).toBeEnabled();
  await page.reload();
  await expect(page.getByTestId('conversation-title')).toHaveText(title);
  await page.goto('/sql');
  await page.getByTestId('datasource-select').selectOption({ index: 1 });
  await page.getByTestId('sql-editor').fill('SELECT 42 AS saved_example');
  await page.getByTestId('saved-queries').locator('summary').click();
  await page.getByTestId('saved-query-title').fill(title);
  const response = page.waitForResponse(result => result.url().endsWith('/api/v1/saved-queries') && result.request().method() === 'POST');
  await page.getByTestId('save-query').click();
  const saved = await (await response).json();
  await page.getByTestId('sql-editor').fill('SELECT 99');
  await page.getByTestId(`load-saved-${saved.id}`).click();
  await expect(page.getByTestId('sql-editor')).toHaveValue('SELECT 42 AS saved_example');
  await expect(page.getByTestId('query-error')).toHaveCount(0);
  await page.getByTestId(`delete-saved-${saved.id}`).click();
  await expect(page.getByTestId(`saved-query-${saved.id}`)).toHaveCount(0);
});
