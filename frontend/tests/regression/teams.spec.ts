import { expect, test } from '@playwright/test';
test.use({ locale: 'en-US' });

test('creates memory teams, assigns and removes a member, preserves drafts on conflict', async ({ page }) => {
  const teams: {id:string;name:string}[]=[];
  const user={id:'user-a',username:'analyst',active:true,roles:[],created_at:'2026-10-16',team_id:null as string|null};
  let conflict=true;
  await page.route('**/api/v1/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    let body:any={items:[]};
    if(path.endsWith('/auth/me')) body={id:'admin',username:'admin',roles:['admin'],capabilities:['admin.manage']};
    if(path.endsWith('/admin/users')) body={items:[user]};
    if(path.endsWith('/admin/roles')) body=[];
    if(path.endsWith('/admin/teams')) {
      if(req.method()==='POST') {
        if(conflict) { conflict=false; await route.fulfill({status:409,json:{error:{code:'CONFLICT',message:'name exists'}}}); return; }
        const team={id:'t1',name:req.postDataJSON().name}; teams.push(team);body=team;
      } else body={items:teams};
    }
    if(path.endsWith('/admin/users/user-a/team')) { user.team_id=req.postDataJSON().team_id;body=user; }
    await route.fulfill({json:body});
  });
  await page.goto('/admin/permissions');
  await page.getByTestId('new-team-name').fill('Analytics');
  await page.getByTestId('create-team').click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByTestId('new-team-name')).toHaveValue('Analytics');
  await page.getByTestId('create-team').click();
  await expect(page.getByTestId('new-team-name')).toHaveValue('');
  await expect(page.getByTestId('team-analyst').locator('option')).toHaveCount(2);
  await page.getByTestId('team-analyst').selectOption('t1');
  await expect(page.getByTestId('team-analyst')).toHaveValue('t1');
  await page.getByTestId('team-analyst').selectOption('');
  await expect(page.getByTestId('team-analyst')).toHaveValue('');
});
