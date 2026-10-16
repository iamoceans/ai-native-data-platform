import { expect, test, type Page } from '@playwright/test';

test.use({ locale: 'en-US' });
async function platform(page: Page) {
  const profiles: Record<string, any> = {
    alice: { id: 'alice', username: 'alice', display_name: '', locale: 'en', timezone: 'UTC', settings_version: 1, roles: ['viewer'], capabilities: [] },
    bob: { id: 'bob', username: 'bob', display_name: '', locale: 'en', timezone: null, settings_version: 1, roles: ['viewer'], capabilities: [] },
  };
  let account: string | null = 'alice'; let reject = false;
  const writes: any[] = []; const cursors: (string | null)[] = [];
  const sessions = Array.from({ length: 45 }, (_, i) => ({ id: `s${i}`, title: `Conversation ${i}`, updated_at: '2026-10-16T00:00:00Z', context_version: 1, active_turn_id: null, resolved_context: {}, skill_config: {} }));
  await page.context().route('**/api/v1/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    let body: any = { items: [] };
    if (path.endsWith('/auth/me')) {
      if (!account) { await route.fulfill({ status: 401, json: { error: { code: 'UNAUTHENTICATED', message: 'signed out' } } }); return; }
      if (request.method() === 'PATCH') {
        const patch = request.postDataJSON(); writes.push(patch);
        if (reject || patch.expected_settings_version !== profiles[account].settings_version) {
          await route.fulfill({ status: 409, json: { error: { code: 'CONFLICT', message: 'settings changed' } } }); return;
        }
        const { expected_settings_version, ...fields } = patch;
        profiles[account] = { ...profiles[account], ...fields, settings_version: expected_settings_version + 1 };
      }
      body = profiles[account];
    }
    if (path.endsWith('/auth/login')) { account = request.postDataJSON().username; body = { profile: profiles[account!], csrf_token: 'test' }; }
    if (path.endsWith('/auth/logout')) { account = null; await route.fulfill({ status: 204 }); return; }
    if (path.endsWith('/sessions')) {
      const cursor = url.searchParams.get('cursor'); cursors.push(cursor);
      const offset = Number(cursor ?? 0); body = { items: sessions.slice(offset, offset + 20), next_cursor: offset + 20 < 45 ? String(offset + 20) : null };
    }
    const session = sessions.find(item => path.endsWith(`/sessions/${item.id}`)); if (session) body = session;
    if (path.endsWith('/events')) { await route.fulfill({ contentType: 'text/event-stream', body: '' }); return; }
    await route.fulfill({ json: body });
  });
  return { profiles, writes, cursors, fail: () => { reject = true; } };
}

test('saves profile and restores account preferences across reload and a fresh device', async ({ page, browser }) => {
  const { profiles, writes } = await platform(page);
  await page.goto('/profile');
  await expect(page).toHaveURL(/\/settings$/);
  await page.getByTestId('settings-display-name').fill('Alice');
  await page.getByTestId('settings-locale').selectOption('zh-CN');
  await page.getByTestId('settings-timezone').fill('Asia/Singapore');
  await page.getByTestId('settings-save').click();
  await expect(page.getByTestId('settings-saved')).toContainText('设置已保存');
  expect(writes[0].expected_settings_version).toBe(1);
  await page.reload();
  await expect(page.getByTestId('settings-display-name')).toHaveValue('Alice');
  await expect(page.getByTestId('language-switcher')).toHaveValue('zh-CN');
  await expect(page.getByTestId('settings-timezone')).toHaveValue('Asia/Singapore');
  // A new browser storage context receives the persisted server preference.
  const fresh = await browser.newContext({ locale: 'en-US' });
  const device = await fresh.newPage();
  await fresh.route('**/api/v1/**', route => route.fulfill({ json: profiles.alice }));
  await device.goto('/settings');
  await expect(device.getByTestId('language-switcher')).toHaveValue('zh-CN');
  await expect(device.getByTestId('settings-timezone')).toHaveValue('Asia/Singapore');
  await fresh.close();
});

test('switching accounts does not inherit preferences and failed saves preserve edits', async ({ page, context }) => {
  const { fail } = await platform(page);
  await page.goto('/settings');
  await page.getByTestId('language-switcher').selectOption('zh-CN');
  await expect(page.getByTestId('language-switcher')).toHaveValue('zh-CN');
  const other = await context.newPage();
  await other.goto('/settings');
  await expect(other.getByTestId('settings-username')).toHaveText('alice');
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(other.getByTestId('login-username')).toBeVisible();
  await page.getByTestId('login-username').fill('bob');
  await page.getByTestId('login-password').fill('password');
  await page.getByTestId('login-submit').click();
  await page.getByTestId('nav-settings').click();
  await expect(page.getByTestId('settings-username')).toHaveText('bob');
  await expect(page.getByTestId('language-switcher')).toHaveValue('en');
  await expect(page.getByTestId('settings-display-name')).toHaveValue('');
  await other.goto('/settings');
  await expect(other.getByTestId('settings-username')).toHaveText('bob');
  await expect(other.getByTestId('language-switcher')).toHaveValue('en');
  await other.close();
  fail();
  await page.getByTestId('settings-display-name').fill('unsaved Bob');
  await page.getByTestId('settings-save').click();
  await expect(page.getByTestId('settings-error')).toBeVisible();
  await expect(page.getByTestId('settings-display-name')).toHaveValue('unsaved Bob');
});

test('loads all session pages and opens older history', async ({ page }) => {
  const { cursors } = await platform(page);
  await page.goto('/ask');
  await expect(page.getByTestId('session-list').locator('li')).toHaveCount(20);
  await page.getByTestId('load-more-sessions').click();
  await expect(page.getByTestId('session-list').locator('li')).toHaveCount(40);
  await page.getByTestId('load-more-sessions').click();
  await expect(page.getByTestId('session-list').locator('li')).toHaveCount(45);
  await expect(page.getByTestId('load-more-sessions')).toHaveCount(0);
  expect(cursors).toEqual([null, '20', '40']);
  await page.getByTestId('session-s44').click();
  await expect(page).toHaveURL(/\/ask\/s44$/);
  await expect(page.getByTestId('conversation-title')).toHaveText('Conversation 44');
});

test('account preference updates synchronize between tabs without losing unsaved forms', async ({ page, context }) => {
  await platform(page); await page.goto('/settings');
  const other = await context.newPage(); await other.goto('/settings');
  await other.getByTestId('settings-display-name').fill('local draft');
  await page.getByTestId('language-switcher').selectOption('zh-CN');
  await expect(other.getByTestId('language-switcher')).toHaveValue('zh-CN');
  await expect(other.getByTestId('settings-display-name')).toHaveValue('local draft');
  await other.getByTestId('settings-save').click();
  await expect(other.getByTestId('settings-error')).toBeVisible();
  await other.close();
});
