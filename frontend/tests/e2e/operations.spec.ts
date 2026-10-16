import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';
test.skip(!process.env.E2E_BASE_URL,'requires the live platform');
test.use({locale:'en-US'});

test('account defaults seed a real conversation and rerun preserves its original report',async ({page,browser})=>{
  const csrf=(await page.context().cookies()).find(cookie=>cookie.name==='ainative_csrf')!.value;
  const roles=await (await page.request.get('/api/v1/admin/roles')).json();
  const username=`ops-${randomUUID().slice(0,8)}`,password=`Ops-user-${randomUUID()}`;
  const created=await page.request.post('/api/v1/admin/users',{headers:{'X-CSRF-Token':csrf},data:{username,password,role_ids:[roles.find((role:any)=>role.name==='analyst').id]}});
  expect(created.status()).toBe(201);
  const context=await browser.newContext({baseURL:process.env.E2E_BASE_URL,locale:'en-US'});
  try {
    const client=await context.newPage();
    await client.goto('/login');
    await client.getByTestId('login-username').fill(username);await client.getByTestId('login-password').fill(password);
    await client.getByTestId('login-submit').click();await client.getByTestId('nav-settings').click();
    await client.getByTestId('settings-default-metric').selectOption(process.env.E2E_METRIC??'fixture_revenue');
    await client.getByTestId('settings-default-dimension-country').check();
    await client.getByTestId('settings-save').click();await expect(client.getByTestId('settings-saved')).toBeVisible();
    await client.goto('/ask');await client.getByTestId('new-conversation').click();
    await expect(client.getByTestId('scope-metric')).toHaveValue(process.env.E2E_METRIC??'fixture_revenue');
    await expect(client.getByTestId('scope-dimension-country')).toBeChecked();
    await expect(client.getByTestId('scope-baseline-start')).toHaveValue('');
    for(const [field,value] of Object.entries({'baseline-start':'2026-09-10','baseline-end':'2026-09-11','current-start':'2026-09-11','current-end':'2026-09-12'})) await client.getByTestId(`scope-${field}`).fill(value);
    await client.getByTestId('conversation-input').fill('compare revenue');await client.getByTestId('conversation-send').click();
    await expect(client.getByTestId('message-analysis_result')).toBeVisible({timeout:90000});
    const link=client.getByTestId('analysis-deeplink');const original=await link.getAttribute('href');
    const report=await (await client.request.get(`/api/v1${original}/report`)).json();
    await link.click();await client.getByTestId('rerun-analysis').click();
    await expect(client).not.toHaveURL(new RegExp(original!+'$'));
    await expect.poll(async()=>{
      const id=new URL(client.url()).pathname.split('/').at(-1);
      return (await (await client.request.get(`/api/v1/analyses/${id}`)).json()).status;
    },{timeout:90000}).toBe('PARTIAL');
    expect(await (await client.request.get(`/api/v1${original}/report`)).json()).toEqual(report);
  } finally {await context.close();}
});
