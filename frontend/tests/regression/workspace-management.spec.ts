import { expect, test, type Page } from '@playwright/test';

test.use({ locale: 'en-US' });
async function fixture(page: Page, options: { conflict?: boolean; savedCount?: number } = {}) {
  let authenticated = true; let failPassword = true;
  const passwordCalls: any[] = []; const runs: any[] = []; const edits: any[] = [];
  const session = { id: 'first', title: 'Original', context_version: 1, archived_at: null as string | null, active_turn_id: null, updated_at: '2026-10-16', resolved_context: {}, skill_config: {} };
  let saved = Array.from({ length: options.savedCount ?? 0 }, (_, i) => ({ id: `q${i}`, title: `Template ${i}`, datasource_id: 'source', sql: `SELECT ${i}`, created_at: '2026-10-16' }));
  await page.route('**/api/v1/**', async route => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname;
    let body: any = { items: [] };
    if (path.endsWith('/auth/me')) {
      if (!authenticated) { await route.fulfill({ status: 401, json: { error: { code: 'UNAUTHENTICATED', message: 'signed out' } } }); return; }
      body = { id: 'owner', username: 'reader', roles: ['analyst'], capabilities: ['analysis.create'], settings_version: 1, locale: 'en', timezone: 'UTC' };
    }
    if (path.endsWith('/auth/me/access')) body = { profile: { id: 'owner' }, active: true, created_at: '2026-10-18T00:00:00Z', last_login_at: null, checked_at: '2026-10-18T00:00:00Z', roles: [], items: [], domains: [], datasource_count: 0, total: 0 };
    if (path.endsWith('/sessions')) body = { items: !session.archived_at || url.searchParams.get('include_archived') ? [session] : [] };
    if (path.endsWith('/sessions/first')) {
      if (req.method() === 'PATCH') {
        edits.push(req.postDataJSON());
        if (options.conflict) { await route.fulfill({ status: 409, json: { error: { code: 'CONTEXT_VERSION_CONFLICT', message: 'scope changed' } } }); return; }
        const patch = req.postDataJSON(); if (patch.title) session.title = patch.title;
        if (patch.archived !== undefined) session.archived_at = patch.archived ? '2026-10-16' : null;
      }
      body = session;
    }
    if (path.endsWith('/datasources')) body = { items: [{ id: 'source', name: 'Warehouse', kind: 'postgres', enabled: true }] };
    if (path.endsWith('/saved-queries')) {
      if (req.method() === 'POST') { const item = { ...req.postDataJSON(), id: 'saved-new', created_at: '2026-10-16' }; saved = [item, ...saved]; body = item; }
      else { const offset = Number(url.searchParams.get('cursor') ?? 0); body = { items: saved.slice(offset, offset + 20), next_cursor: offset + 20 < saved.length ? String(offset + 20) : null }; }
    }
    if (path.match(/\/saved-queries\/[^/]+$/) && req.method() === 'DELETE') { saved = saved.filter(item => item.id !== path.split('/').at(-1)); await route.fulfill({ status: 204 }); return; }
    if (path.endsWith('/queries') && req.method() === 'POST') runs.push(req.postDataJSON());
    if (path.endsWith('/auth/password')) {
      passwordCalls.push(req.postDataJSON());
      if (failPassword) { await route.fulfill({ status: 422, json: { error: { code: 'VALIDATION_ERROR', message: 'current password is incorrect' } } }); return; }
      authenticated = false; await route.fulfill({ status: 204 }); return;
    }
    await route.fulfill({ json: body });
  });
  return { runs, edits, passwordCalls, allowPassword: () => { failPassword = false; } };
}

test('renames, archives and restores a conversation with an explicit archive browser', async ({ page }) => {
  const { edits } = await fixture(page); await page.goto('/ask/first');
  await page.getByTestId('rename-session-first').click();
  await page.getByTestId('session-title-input').fill('Renamed conversation');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByTestId('conversation-title')).toHaveText('Renamed conversation');
  expect(edits[0].expected_context_version).toBe(1);
  await page.getByTestId('archive-session-first').click();
  await expect(page.getByTestId('session-first')).toHaveCount(0);
  await expect(page.getByTestId('conversation-input')).toBeDisabled();
  await page.getByTestId('include-archived-sessions').check();
  await expect(page.getByTestId('session-first')).toContainText('Archived');
  await page.getByTestId('archive-session-first').click();
  await expect(page.getByTestId('conversation-input')).toBeEnabled();
});

test('a rename conflict preserves the edit and original conversation', async ({ page }) => {
  await fixture(page, { conflict: true }); await page.goto('/ask/first');
  await page.getByTestId('rename-session-first').click();
  await page.getByTestId('session-title-input').fill('unsaved title');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByTestId('session-title-input')).toHaveValue('unsaved title');
  await expect(page.getByTestId('conversation-title')).toHaveText('Original');
});

test('saved SQL loads without execution, follows pages and can be deleted', async ({ page }) => {
  const { runs } = await fixture(page, { savedCount: 25 }); await page.goto('/sql');
  await page.getByTestId('datasource-select').selectOption('source');
  await page.getByTestId('sql-editor').fill('SELECT 42');
  await page.getByTestId('saved-queries').locator('summary').click();
  await page.getByTestId('saved-query-title').fill('My query');
  await page.getByTestId('save-query').click();
  await expect(page.getByTestId('saved-query-notice')).toBeVisible();
  await page.getByTestId('sql-editor').fill('SELECT 99');
  await page.getByTestId('load-saved-saved-new').click();
  await expect(page.getByTestId('sql-editor')).toHaveValue('SELECT 42');
  expect(runs).toEqual([]);
  await page.getByTestId('more-saved-queries').click();
  await expect(page.getByTestId('saved-query-q24')).toBeVisible();
  await page.getByTestId('delete-saved-saved-new').click();
  await expect(page.getByTestId('saved-query-saved-new')).toHaveCount(0);
});

test('password confirmation guards submission; failure retains fields and success signs out', async ({ page }) => {
  const { allowPassword, passwordCalls } = await fixture(page); await page.goto('/settings');
  await page.getByTestId('password-current').fill('old-password-value');
  await page.getByTestId('password-new').fill('new-password-value');
  await page.getByTestId('password-confirm').fill('mismatch');
  await expect(page.getByTestId('password-submit')).toBeDisabled();
  expect(passwordCalls).toEqual([]);
  await page.getByTestId('password-confirm').fill('new-password-value');
  await page.getByTestId('password-submit').click();
  await expect(page.getByTestId('password-error')).toBeVisible();
  await expect(page.getByTestId('password-current')).toHaveValue('old-password-value');
  allowPassword(); await page.getByTestId('password-submit').click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByTestId('login-password')).toHaveValue('');
});
