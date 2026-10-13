import { test, expect, type Page } from '@playwright/test';

async function fixture(page: Page) {
 const pageErrors: string[] = [];
 page.on('pageerror', error => pageErrors.push(error.message));
 page.on('close', () => expect(pageErrors).toEqual([]));
 const scope = { metric_key: 'revenue', baseline_start: '2026-09-01', baseline_end: '2026-09-07', current_start: '2026-09-08', current_end: '2026-09-14', dimensions: ['platform'], filters: { platform: 'ios' }, data_complete_state: 'complete' };
 const sessions: Record<string, any> = {
  first: { id: 'first', title: 'first', context_version: 1, active_turn_id: null, resolved_context: scope, skill_config: {}, updated_at: '2026-10-14' },
  second: { id: 'second', title: 'second', context_version: 5, active_turn_id: null, resolved_context: { ...scope, metric_key: 'clicks', dimensions: ['region'] }, skill_config: {}, updated_at: '2026-10-14' },
 };
 const turns: any[] = []; const patches: any[] = [];
 const control = { rejectBinding: false, turnDelay: 0, bindingDelay: 0, rejectTurn: false, conversationalDisabled: false };
 const analyses: any[] = [];
 await page.context().route('**/api/v1/**', async route => {
  const path = new URL(route.request().url()).pathname;
  let body: any = { items: [] };
  if (path.endsWith('/auth/me')) body = { username: 'test', roles: [], capabilities: [] };
  if (path.endsWith('/metrics')) body = { items: ['revenue', 'clicks'].map(metric_key => ({ metric_key, name: metric_key, allowed_dimensions: ['platform','region'] })) };
  if (path.endsWith('/skills')) body = { items: [{ id:'upload', slug:'personal', source:'upload', enabled:true, archived_at:null, latest_version:{ id:'v2', version:2 }, versions:[{id:'v1',version:1},{id:'v2',version:2}] }] };
  if (path.endsWith('/sessions')) body = { items: Object.values(sessions) };
  const match = path.match(/sessions\/(first|second)$/);
  if (match) {
   const session = sessions[match[1]];
   if (route.request().method() === 'PATCH' && control.bindingDelay) await new Promise(resolve => setTimeout(resolve, control.bindingDelay));
   if (route.request().method() === 'PATCH' && control.rejectBinding) { await route.fulfill({status:409,json:{error:{code:'CONTEXT_VERSION_CONFLICT',message:'会话已更新，请重试',retryable:true,details:{}}}}); return; }
   if (route.request().method() === 'PATCH') { const patch = route.request().postDataJSON(); patches.push(patch); session.skill_config = patch.skill_config; session.context_version++; }
   body = session;
  }
  if (path.endsWith('/turns') && route.request().method() === 'POST') {
   turns.push(route.request().postDataJSON());
   if (control.rejectTurn || control.conversationalDisabled) {
    if (control.turnDelay) await new Promise(resolve => setTimeout(resolve, control.turnDelay));
    await route.fulfill({status:control.conversationalDisabled ? 503 : 409,json:{error:{code:control.conversationalDisabled ? 'CONVERSATIONAL_DISABLED' : 'CONTEXT_VERSION_CONFLICT',message:'原会话请求失败',retryable:false,details:{}}}});
    return;
   }
   // Resolve asynchronously after the mutation has reset the draft from old context.
   setTimeout(() => { sessions.first.resolved_context = { ...scope, current_start:'2026-09-15', current_end:'2026-09-21', dimensions:['region'], filters:{region:'SG'} }; sessions.first.context_version = 2; sessions.first.active_turn_id = null; }, 200);
   sessions.first.active_turn_id = 'turn';
   body = { id:'turn', status:'RESOLVING', session_id:'first', created_at:'2026-10-14' };
   if (control.turnDelay) await new Promise(resolve => setTimeout(resolve, control.turnDelay));
  }
  if (path.endsWith('/turns/turn')) body = { id:'turn', status:'RESOLVING', session_id:'first', created_at:'2026-10-14' };
  if (path.endsWith('/analyses') && route.request().method() === 'POST') { analyses.push(route.request().postDataJSON()); body = {analysis_id:'legacy'}; }
  if (path.endsWith('/analyses/legacy')) body = {analysis_id:'legacy',status:'QUEUED',steps:[],state:{},question:'legacy analysis',checkpoint_version:0,policy_revision:1,final_report:null};
  if (path.endsWith('/events')) { await route.fulfill({ status:200, contentType:'text/event-stream', body:'' }); return; }
  await route.fulfill({ json:body });
 });
 await page.goto('/ask/first');
 await expect(page.getByTestId('scope-current-start')).toHaveValue('2026-09-08');
 return { turns, patches, sessions, control, analyses };
}

test('inherits asynchronously resolved scope and resets edits when switching sessions', async ({page}) => {
 const { turns } = await fixture(page);
 await page.getByTestId('conversation-input').fill('next week');
 await page.getByTestId('conversation-send').click();
 await expect(page.getByTestId('scope-current-start')).toHaveValue('2026-09-15');
 await expect(page.getByTestId('scope-filters')).toHaveValue('region=SG');
 await page.getByTestId('conversation-input').fill('follow up');
 await expect(page.getByTestId('conversation-send')).toBeEnabled();
 await page.getByTestId('conversation-send').click();
 expect(turns[1].scope_patch).toBeNull();
 expect(turns[1].expected_context_version).toBe(2);
 await page.getByTestId('scope-current-start').fill('2026-10-01');
 await page.getByTestId('session-second').click();
 await expect(page.getByTestId('scope-metric')).toHaveValue('clicks');
 await expect(page.getByTestId('scope-current-start')).toHaveValue('2026-09-08');
});

test('allows typing a complete filter, rejects malformed input, and clears explicitly', async ({page}) => {
 const { turns } = await fixture(page);
 const filters = page.getByTestId('scope-filters');
 await filters.fill('');
 await filters.pressSequentially('platform=android');
 await expect(filters).toHaveValue('platform=android');
 await page.getByTestId('conversation-input').fill('analyze');
 await page.getByTestId('conversation-send').click();
 await expect.poll(() => turns.length).toBe(1);
 expect(turns[0].scope_patch.filters).toEqual({ platform:'android' });
 await page.getByTestId('conversation-input').fill('bad filter');
 await expect(page.getByTestId('conversation-send')).toBeEnabled();
 await filters.fill('platform=');
 await page.getByTestId('conversation-input').fill('bad filter');
 await page.getByTestId('conversation-send').click();
 await expect(page.getByTestId('conversation-error')).toContainText('筛选');
 expect(turns).toHaveLength(1);
 await filters.fill('');
 await page.getByTestId('conversation-send').click();
 await expect.poll(() => turns.length).toBe(2);
 expect(turns[1].scope_patch.clear).toEqual(['filters']);
});

test('loads enabled uploaded skill manually and binds immutable old version for automatic recall', async ({page}) => {
 const { turns, patches } = await fixture(page);
 await page.getByTestId('skill-mode').selectOption('manual');
 await page.getByTestId('skill-pick-v1').check();
 await page.getByTestId('conversation-input').fill('manual');
 await page.getByTestId('conversation-send').click();
 await expect.poll(() => turns.length).toBe(1);
 expect(turns[0].skill_selection).toEqual({ mode:'manual', version_ids:['v1'] });
 await expect(page.getByTestId('conversation-stop')).toHaveCount(0);
 await page.getByTestId('skill-mode').selectOption('auto');
 await page.getByTestId('skill-bind').click();
 await expect.poll(() => patches.length).toBe(1);
 expect(patches[0]).toEqual({ expected_context_version:2, skill_config:{mode:'auto',version_ids:['v1']} });
 await page.reload();
 await expect(page.getByTestId('skill-pick-v1')).toBeChecked();
 await expect(page.getByTestId('skill-mode')).toHaveValue('auto');
});


test('keeps user edits while untouched fields follow async scope updates', async ({page}) => {
 const { turns } = await fixture(page);
 await page.getByTestId('conversation-input').fill('next week');
 await page.getByTestId('conversation-send').click();
 await expect(page.getByTestId('conversation-input')).toHaveValue('');
 await page.getByTestId('scope-baseline-start').fill('2026-08-01');
 await expect(page.getByTestId('scope-current-start')).toHaveValue('2026-09-15');
 await expect(page.getByTestId('scope-baseline-start')).toHaveValue('2026-08-01');
 await page.getByTestId('conversation-input').fill('continue');
 await page.getByTestId('conversation-send').click();
 await expect.poll(() => turns.length).toBe(2);
 expect(turns[1].scope_patch).toEqual({baseline_start:'2026-08-01'});
});

for (const input of ['platform=', 'platform', 'platform=android,', 'platform=ios,platform=android', 'platform=android=other']) {
 test('blocks malformed filter: ' + input, async ({page}) => {
  const { turns } = await fixture(page);
  await page.getByTestId('scope-filters').fill(input);
  await page.getByTestId('conversation-input').fill('analyze');
  await expect(page.getByRole('alert')).toContainText('筛选');
  await page.getByTestId('conversation-send').click();
  await expect(page.getByTestId('conversation-error')).toContainText('INVALID_SCOPE');
  expect(turns).toHaveLength(0);
 });
}

test('reports optimistic binding conflict without replacing the selected fixed version', async ({page}) => {
 const { patches, control } = await fixture(page);
 await page.getByTestId('skill-pick-v1').check();
 control.rejectBinding = true;
 await page.getByTestId('skill-bind').click();
 await expect(page.getByTestId('conversation-error')).toContainText('CONTEXT_VERSION_CONFLICT');
 expect(patches).toHaveLength(0);
 await expect(page.getByTestId('skill-pick-v1')).toBeChecked();
 control.rejectBinding = false;
 await page.getByTestId('skill-bind').click();
 await expect.poll(() => patches.length).toBe(1);
 expect(patches[0].skill_config.version_ids).toEqual(['v1']);
});


test('completion of a send in another session does not erase the current draft', async ({page}) => {
 const { control } = await fixture(page);
 control.turnDelay = 1200;
 await page.getByTestId('conversation-input').fill('first question');
 await page.getByTestId('conversation-send').click();
 await page.getByTestId('session-second').click();
 await expect(page.getByTestId('scope-metric')).toHaveValue('clicks');
 await page.getByTestId('scope-baseline-start').fill('2026-08-01');
 // The composer becomes available when the earlier mutation completes.
 await expect(page.getByTestId('conversation-input')).toBeEnabled();
 await expect(page.getByTestId('scope-baseline-start')).toHaveValue('2026-08-01');
});

for (const kind of ['send', 'binding'] as const) {
 test('late ' + kind + ' failure stays with its originating session', async ({page}) => {
  const { control } = await fixture(page);
  if (kind === 'send') {
   control.rejectTurn = true;
   control.turnDelay = 1200;
   await page.getByTestId('conversation-input').fill('first question');
   await page.getByTestId('conversation-send').click();
  } else {
   control.rejectBinding = true;
   control.bindingDelay = 1200;
   await page.getByTestId('skill-bind').click();
  }
  await page.getByTestId('session-second').click();
  await expect(page.getByTestId('scope-metric')).toHaveValue('clicks');
  await expect(page.getByTestId('skill-mode')).toBeEnabled();
  await expect(page.getByTestId('conversation-error')).toHaveCount(0);
 });
}

test('legacy fallback submits the same explicit filters', async ({page}) => {
 const { control, analyses } = await fixture(page);
 control.conversationalDisabled = true;
 await page.getByTestId('scope-filters').fill('platform=android');
 await page.getByTestId('conversation-input').fill('legacy analysis');
 await page.getByTestId('conversation-send').click();
 await expect.poll(() => analyses.length).toBe(1);
 expect(analyses[0].context.filters).toEqual({platform:'android'});
});

test('reverting a user edit resumes following confirmed context', async ({page}) => {
 const { turns } = await fixture(page);
 await page.getByTestId('conversation-input').fill('next week');
 await page.getByTestId('conversation-send').click();
 await expect(page.getByTestId('conversation-input')).toHaveValue('');
 await page.getByTestId('scope-current-start').fill('2026-08-01');
 await page.getByTestId('scope-current-start').fill('2026-09-08');
 await expect(page.getByTestId('scope-current-start')).toHaveValue('2026-09-15');
 await page.getByTestId('conversation-input').fill('continue');
 await page.getByTestId('conversation-send').click();
 await expect.poll(() => turns.length).toBe(2);
 expect(turns[1].scope_patch).toBeNull();
});
