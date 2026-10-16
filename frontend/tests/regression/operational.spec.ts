import { expect, test } from '@playwright/test';
test.use({ locale: 'en-US' });

for (const mode of ['off', 'auto'] as const) {
  test(`landing question inherits the account's ${mode} skill default`, async ({ page }) => {
    const turns: any[] = [];
    const session = {
      id: 'created', title: 'New conversation', context_version: 1, active_turn_id: null,
      resolved_context: { metric_key: 'revenue', dimensions: ['country'] },
      skill_config: { mode }, updated_at: '2026-10-16T00:00:00Z',
    };
    await page.route('**/api/v1/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      let body: unknown = { items: [] };
      if (path.endsWith('/auth/me')) body = {
        id: 'owner', username: 'owner', roles: ['analyst'], capabilities: ['analysis.create'],
        locale: 'en', analysis_defaults: { metric_key: 'revenue', dimensions: ['country'], skill_mode: mode },
      };
      if (path.endsWith('/metrics')) body = { items: [{ metric_key: 'revenue', name: 'Revenue', allowed_dimensions: ['country'] }] };
      if (path.endsWith('/sessions')) body = request.method() === 'POST' ? session : { items: [] };
      if (path.endsWith('/sessions/created')) body = session;
      if (path.endsWith('/turns') && request.method() === 'POST') {
        turns.push(request.postDataJSON());
        body = { id: 'turn', session_id: 'created', status: 'QUEUED' };
      }
      await route.fulfill({ json: body });
    });
    await page.goto('/ask');
    await page.getByTestId('conversation-input').fill('Compare revenue');
    await page.getByTestId('conversation-send').click();
    await expect.poll(() => turns.length).toBe(1);
    expect(turns[0].skill_selection).toBeNull(); // server inherits session.skill_config
    await expect(page).toHaveURL(/\/ask\/created$/);
  });
}

test('saves account analysis defaults and restores them after reload', async ({ page }) => {
  let profile:any={id:'owner',username:'owner',roles:['analyst'],capabilities:['analysis.create'],locale:'en',settings_version:1};
  const writes:any[]=[];
  await page.route('**/api/v1/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    let body:any={items:[]};
    if(path.endsWith('/auth/me')) {
      if(request.method()==='PATCH') { const patch=request.postDataJSON();writes.push(patch);profile={...profile,...patch,settings_version:2}; }
      body=profile;
    }
    if(path.endsWith('/metrics')) body={items:[{metric_key:'revenue',name:'Revenue',allowed_dimensions:['country','platform']}]};
    await route.fulfill({json:body});
  });
  await page.goto('/settings');
  await page.getByTestId('settings-default-metric').selectOption('revenue');
  await page.getByTestId('settings-default-dimension-country').check();
  await page.getByTestId('settings-default-skills').selectOption('auto');
  await page.getByTestId('settings-save').click();
  await expect(page.getByTestId('settings-saved')).toBeVisible();
  expect(writes[0].analysis_defaults).toEqual({metric_key:'revenue',dimensions:['country'],skill_mode:'auto'});
  await page.reload();
  await expect(page.getByTestId('settings-default-metric')).toHaveValue('revenue');
  await expect(page.getByTestId('settings-default-dimension-country')).toBeChecked();
  await expect(page.getByTestId('settings-default-skills')).toHaveValue('auto');
});

test('rerun retries use one idempotency key and open the new report', async ({ page }) => {
  const keys:string[]=[];let fail=true;
  await page.route('**/api/v1/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    let body:any={items:[]};
    if(path.endsWith('/auth/me')) body={id:'owner',username:'owner',roles:['analyst'],capabilities:['analysis.create']};
    if(path.endsWith('/analyses/original') || path.endsWith('/analyses/child')) body={id:path.endsWith('child')?'child':'original',session_id:'s1',question:'Revenue comparison',status:'COMPLETED',state:{},budget:{},context:{},steps:[],final_report:{claims:[],limitations:[]}};
    if(path.endsWith('/analyses/original/rerun')) {
      keys.push(request.headers()['idempotency-key']);
      if(fail) { fail=false;await route.fulfill({status:503,json:{error:{code:'INTERNAL_ERROR',message:'temporary failure'}}});return; }
      body={analysis_id:'child',status:'CREATED'};
    }
    await route.fulfill({json:body});
  });
  await page.goto('/analyses/original');
  await page.getByTestId('rerun-analysis').click();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.getByTestId('rerun-analysis').click();
  await expect(page).toHaveURL(/\/analyses\/child$/);
  expect(keys).toHaveLength(2);expect(keys[0]).toBe(keys[1]);
});
