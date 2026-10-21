import { expect, test, type Page } from '@playwright/test';

test.use({ locale: 'en-US' });

async function fixture(page: Page, options: { delayed?: boolean; error?: string; firstError?: boolean } = {}) {
  let release!: () => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  let pending = false;
  let error = options.error;
  await page.route('**/api/v1/**', async route => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname;
    let body: unknown = { items: [] };
    if (path.endsWith('/auth/me')) body = { id: 'owner', username: 'reader', roles: ['analyst'], capabilities: [], locale: 'en', timezone: 'UTC', settings_version: 1 };
    if (path.endsWith('/datasources')) body = { items: [{ id: 'source', name: 'Warehouse', kind: 'postgres', enabled: true }] };
    if (path.endsWith('/queries') && req.method() === 'POST') body = { query_id: 'q2' };
    const query = path.match(/\/queries\/(q[12])$/);
    if (query) body = { query_id: query[1], status: 'SUCCEEDED', attempt: 1, created_at: '2026-10-22T00:00:00Z', finished_at: '2026-10-22T00:00:01Z', validated_sql: 'SELECT label', error: null };
    const result = path.match(/\/queries\/(q[12])\/results$/);
    if (result) {
      const next = url.searchParams.has('cursor');
      if (next && options.delayed && result[1] === 'q1') { pending = true; await waiting; }
      if (error && (next || options.firstError)) {
        await route.fulfill({ status: error === 'SERVICE_NOT_READY' ? 503 : error === 'RESULT_EXPIRED' ? 410 : 403,
          json: { error: { code: error, message: 'result unavailable', retryable: error === 'SERVICE_NOT_READY' } } });
        return;
      }
      body = { query_id: result[1], result: { columns: [{ id: 'c0', name: 'label', type: 'text' }], rows: [[`${result[1]}-${next ? 'late' : 'first'}`]], row_count: 1, truncated: false, next_cursor: next ? null : 'next' } };
    }
    await route.fulfill({ json: body }).catch(cause => { if (!options.delayed) throw cause; });
  });
  return { release: () => release(), pending: () => pending, allow: () => { error = undefined; } };
}

async function runSecond(page: Page) {
  await page.getByTestId('datasource-select').selectOption('source');
  await page.getByTestId('sql-editor').fill('SELECT label FROM new_query');
  await page.getByTestId('run-query').click();
  await expect(page).toHaveURL(/query=q2/);
  await expect(page.getByRole('cell', { name: 'q2-first', exact: true })).toBeVisible();
}

const more = (page: Page) => page.getByRole('button', { name: /Load more/ });

test('ignores an old query page after another query is selected', async ({ page }) => {
  const state = await fixture(page, { delayed: true });
  await page.goto('/sql?query=q1');
  await expect(page.getByRole('cell', { name: 'q1-first', exact: true })).toBeVisible();
  await more(page).click(); await expect.poll(state.pending).toBe(true);
  await runSecond(page); state.release();
  await more(page).click();
  await expect(page.getByRole('cell', { name: 'q2-late', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'q1-late', exact: true })).toHaveCount(0);
});

test('browser back and forward select the query from the URL', async ({ page }) => {
  await fixture(page); await page.goto('/sql?query=q1');
  await expect(page.getByRole('cell', { name: 'q1-first', exact: true })).toBeVisible();
  await runSecond(page); await page.goBack();
  await expect(page).toHaveURL(/query=q1/);
  await expect(page.getByRole('cell', { name: 'q1-first', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'q2-first', exact: true })).toHaveCount(0);
  await page.goForward();
  await expect(page.getByRole('cell', { name: 'q2-first', exact: true })).toBeVisible();
});

for (const code of ['PERMISSION_DENIED', 'RESULT_EXPIRED']) {
  test(`clears cached rows and cursor after pagination returns ${code}`, async ({ page }) => {
    const unhandled: string[] = []; page.on('pageerror', error => unhandled.push(error.message));
    await fixture(page, { error: code }); await page.goto('/sql?query=q1');
    await expect(page.getByRole('cell', { name: 'q1-first', exact: true })).toBeVisible();
    await more(page).click();
    await expect(page.getByTestId('result-error')).toContainText(code);
    await expect(page.getByRole('cell', { name: 'q1-first', exact: true })).toHaveCount(0);
    await expect(more(page)).toHaveCount(0); expect(unhandled).toEqual([]);
  });
}

for (const firstError of [false, true]) {
  test(`transient ${firstError ? 'first-page' : 'pagination'} error offers a working retry`, async ({ page }) => {
    const state = await fixture(page, { error: 'SERVICE_NOT_READY', firstError });
    await page.goto('/sql?query=q1');
    if (!firstError) {
      await expect(page.getByRole('cell', { name: 'q1-first', exact: true })).toBeVisible(); await more(page).click();
    }
    await expect(page.getByTestId('result-error')).toContainText('SERVICE_NOT_READY'); state.allow();
    await page.getByRole('button', { name: 'Retry result loading', exact: true }).click();
    await expect(page.getByRole('cell', { name: firstError ? 'q1-first' : 'q1-late', exact: true })).toBeVisible();
    if (!firstError) await expect(page.getByRole('cell', { name: 'q1-first', exact: true })).toBeVisible();
    await expect(page.getByTestId('result-error')).toHaveCount(0);
  });
}
