import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test.use({ locale: 'en-US' });
test.skip(!process.env.E2E_BASE_URL, 'set E2E_BASE_URL for live service verification');

test('account settings persist on the real API and stay separate from the administrator', async ({ page, browser }) => {
  const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'ainative_csrf')!.value;
  const roles = await (await page.request.get('/api/v1/admin/roles')).json();
  const username = `settings-${randomUUID().slice(0, 8)}`;
  const password = `Settings-test-${randomUUID()}`;
  const created = await page.request.post('/api/v1/admin/users', { headers: { 'X-CSRF-Token': csrf },
    data: { username, password, role_ids: [roles.find((role: { name: string }) => role.name === 'viewer').id] } });
  expect(created.status()).toBe(201);
  await page.goto('/login');
  await page.getByTestId('login-username').fill(username);
  await page.getByTestId('login-password').fill(password);
  await page.getByTestId('login-submit').click();
  await page.getByTestId('nav-settings').click();
  await expect(page.getByTestId('settings-username')).toHaveText(username);
  await page.getByTestId('settings-display-name').fill('Live settings reader');
  await page.getByTestId('settings-locale').selectOption('zh-CN');
  await page.getByTestId('settings-timezone').fill('Asia/Singapore');
  await page.getByTestId('settings-save').click();
  await expect(page.getByTestId('settings-saved')).toHaveText('设置已保存');
  await page.reload();
  await expect(page.getByTestId('settings-display-name')).toHaveValue('Live settings reader');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  await page.screenshot({ path: '/tmp/aind-settings-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/aind-settings-mobile.png', fullPage: true });
  const fresh = await browser.newContext({ baseURL: process.env.E2E_BASE_URL, locale: 'en-US' });
  try {
    const device = await fresh.newPage();
    await device.goto('/login');
    await device.getByTestId('login-username').fill(username);
    await device.getByTestId('login-password').fill(password);
    await device.getByTestId('login-submit').click();
    await device.getByTestId('nav-settings').click();
    await expect(device.locator('html')).toHaveAttribute('lang', 'zh-CN');
    await expect(device.getByTestId('settings-timezone')).toHaveValue('Asia/Singapore');
    await expect(device.getByTestId('settings-display-name')).toHaveValue('Live settings reader');
    const replacement = `Changed-test-${randomUUID()}`;
    await page.getByTestId('password-current').fill(password);
    await page.getByTestId('password-new').fill(replacement);
    await page.getByTestId('password-confirm').fill(replacement);
    await page.getByTestId('password-submit').click();
    await expect(page).toHaveURL(/\/login$/);
    // The other device's session was revoked as part of the same transaction.
    expect((await device.request.get('/api/v1/auth/me')).status()).toBe(401);
    await page.getByTestId('login-username').fill(username);
    await page.getByTestId('login-password').fill(replacement);
    await page.getByTestId('login-submit').click();
    await page.getByTestId('nav-settings').click();
    await expect(page.getByTestId('settings-username')).toHaveText(username);
  } finally { await fresh.close(); }
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await page.getByTestId('login-username').fill(process.env.E2E_ADMIN_USER ?? 'admin');
  await page.getByTestId('login-password').fill(process.env.E2E_ADMIN_PASSWORD ?? 'dev-admin-password-123');
  await page.getByTestId('login-submit').click();
  await page.getByTestId('nav-settings').click();
  await expect(page.getByTestId('settings-username')).not.toHaveText(username);
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.getByTestId('settings-display-name')).not.toHaveValue('Live settings reader');
});
