import { expect, test } from '@playwright/test';
test.use({ locale: 'en-US' });

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
