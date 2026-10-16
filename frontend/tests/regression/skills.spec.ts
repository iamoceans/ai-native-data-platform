import { expect, test, type Page } from '@playwright/test';

test.use({ locale: "zh-CN" });

/**
 * Skills page regressions: every control on it must be the workspace's own.
 * A file picker is the one place where the browser's native control can leak
 * through, and the per-row actions must be real buttons that share one style.
 */
const preview = {
  name: 'revenue-watch',
  description: '验收用的渠道解读清单。',
  content_hash: '3836b9182a6900000000000000000000',
  compatibility: { applicable: true, unknown_metric_keys: [], invalid_dimensions: {} },
  metric_keys: ['revenue'],
  preferred_dimensions: ['channel'],
  required_inputs: [],
  references: [],
  body_preview: '# revenue-watch\n\n先核对口径，再看渠道贡献。',
  body_truncated: false,
  files: [{ path: 'SKILL.md', size: 64, sha256: 'abcd' }],
};

async function fixture(page: Page) {
 const previews: string[] = [];
 const versionPosts: string[] = [];
 await page.context().route('**/api/v1/**', async route => {
  const path = new URL(route.request().url()).pathname;
  let body: any = { items: [] };
  if (path.endsWith('/auth/me')) body = { username: 'test', roles: [], capabilities: ['analysis.create'] };
  if (path.endsWith('/skills/import-preview')) { previews.push(path); body = preview; }
  else if (/\/skills\/[^/]+\/versions$/.test(path)) { versionPosts.push(path); body = { id: 'v2', version: 2 }; }
  else if (path.endsWith('/skills')) body = { items: [
   { id: 'builtin-1', slug: 'channel-contribution', source: 'builtin', enabled: true, archived_at: null, version_count: 1, versions: [],
     latest_version: { id: 'v1', version: 1, content_hash: '2ad31776d9f8000000000000', compatibility: { applicable: true }, manifest: { description: '渠道贡献分析。' } } },
   { id: 'upload-1', slug: 'revenue-watch', source: 'upload', enabled: true, archived_at: null, version_count: 1, versions: [],
     latest_version: { id: 'v1', version: 1, content_hash: '3836b9182a69000000000000', compatibility: { applicable: true }, manifest: { description: '验收用的渠道解读清单。' } } },
  ] };
  await route.fulfill({ json: body });
 });
 await page.goto('/skills');
 await expect(page.getByTestId('skill-list')).toBeVisible();
 return { previews, versionPosts };
}

const demoFile = (name: string) => ({ name, mimeType: 'application/zip', buffer: Buffer.from('demo') });

test('puts the file picker in workspace controls and never shows the native one', async ({page}) => {
 const { previews } = await fixture(page);

 // No file control is exposed to the browser's own rendering.
 await expect(page.locator('input[type=file]:not([hidden])')).toHaveCount(0);
 const picker = page.getByTestId('skill-pick-file');
 await expect(picker).toHaveText('选择文件');
 await expect(picker).toHaveClass(/\bghost\b/);
 await expect(page.getByTestId('skill-file-name')).toHaveText('未选择文件');

 // The picked file is reported next to the button, in the shared mono style.
 await page.getByTestId('skill-file').setInputFiles(demoFile('revenue-watch.zip'));
 await expect(page.getByTestId('skill-preview-result')).toBeVisible();
 await expect(page.getByTestId('skill-file-name')).toHaveText('revenue-watch.zip');
 await expect(page.getByTestId('skill-file-name')).toHaveClass(/\bpicked\b/);
 expect(previews).toHaveLength(1);

 // Picking the same file again must still re-run the validation.
 await page.getByTestId('skill-file').setInputFiles(demoFile('revenue-watch.zip'));
 await expect.poll(() => previews.length).toBe(2);
});

test('renders per-skill actions as buttons sharing one style, reachable by keyboard', async ({page}) => {
 const { versionPosts } = await fixture(page);
 const actions = ['skill-toggle-revenue-watch', 'skill-archive-revenue-watch', 'skill-new-version-revenue-watch'];
 const styles = [];
 for (const id of actions) {
  const button = page.getByTestId(id);
  await expect(button).toBeVisible();
  expect(await button.evaluate(el => el.tagName)).toBe('BUTTON');
  styles.push(await button.evaluate(el => {
   const style = getComputedStyle(el);
   return [style.borderTopWidth, style.borderTopColor, style.backgroundColor, style.paddingTop].join('|');
  }));
 }
 // 停用 / 归档 / 新版本 are the same control, not one of them plain text.
 expect(new Set(styles).size).toBe(1);

 // The keyboard can reach it and it opens the picker.
 const newVersion = page.getByTestId('skill-new-version-revenue-watch');
 await newVersion.focus();
 await expect(newVersion).toBeFocused();
 const chooser = page.waitForEvent('filechooser');
 // Targeting the locator re-establishes focus before the trusted key action,
 // including after earlier multi-tab tests moved the browser's active page.
 await newVersion.press('Enter');
 await (await chooser).setFiles(demoFile('revenue-watch.zip'));
 await expect.poll(() => versionPosts.length).toBe(1);
 await expect(page.getByTestId('skill-notice')).toContainText('已新增版本 v2');
});

test('lays out the preview facts and the archived filter without browser defaults', async ({page}) => {
 await fixture(page);
 await page.getByTestId('skill-file').setInputFiles(demoFile('SKILL.md'));
 await expect(page.getByTestId('skill-preview-result')).toBeVisible();

 // Label and value sit on one row instead of the default definition-list indent.
 const offsets = await page.evaluate(() => [...document.querySelectorAll('.kv dt')].map(dt => {
  const dd = dt.nextElementSibling as HTMLElement;
  return Math.abs(dt.getBoundingClientRect().top - dd.getBoundingClientRect().top);
 }));
 expect(offsets.length).toBeGreaterThan(0);
 expect(Math.max(...offsets)).toBeLessThan(6);

 // 显示已归档 keeps its checkbox and text on one line.
 const check = page.locator('.inline-check');
 expect(await check.evaluate(el => getComputedStyle(el).flexDirection)).toBe('row');
 const box = await page.getByTestId('skill-include-archived').boundingBox();
 const label = await check.boundingBox();
 expect(label!.height).toBeLessThan(30);
 // The box is centred on that single line rather than stacked above the text.
 expect(Math.abs((box!.y + box!.height / 2) - (label!.y + label!.height / 2))).toBeLessThan(3);
 await page.getByTestId('skill-include-archived').check();
 await expect(page.getByTestId('skill-include-archived')).toBeChecked();
});

test('keeps the upload row usable on a narrow screen', async ({page}) => {
 await fixture(page);
 await page.setViewportSize({ width: 390, height: 800 });
 await page.getByTestId('skill-file').setInputFiles(demoFile('a-very-long-skill-bundle-name.zip'));
 await expect(page.getByTestId('skill-preview-result')).toBeVisible();

 // The stacked form row leaves the picker on the left edge, never centred.
 const pick = await page.locator('.file-pick').boundingBox();
 const preview = await page.getByTestId('skill-preview').boundingBox();
 expect(Math.abs(pick!.x - preview!.x)).toBeLessThan(2);
 // A long file name is ellipsized inside the card; the page body does not
 // scroll sideways. (Scoped to `main`: the shell's own top navigation does not
 // wrap at this width, which is unrelated to this card.)
 expect((await page.getByTestId('skill-file-name').boundingBox())!.width).toBeLessThanOrEqual(320);
 expect(await page.evaluate(() => {
  const main = document.querySelector('main') as HTMLElement;
  return main.scrollWidth <= main.clientWidth;
 })).toBe(true);
 // Every control stays inside the card's content box (a wrapped column row
 // used to size itself to max-content and hang past the border).
 const edges = await page.evaluate(() => {
  const row = document.querySelector('.form-row') as HTMLElement;
  const card = row.closest('.card') as HTMLElement;
  const style = getComputedStyle(card);
  return {
   rowRight: Math.max(...[...row.children].map(el => el.getBoundingClientRect().right)),
   contentRight: card.getBoundingClientRect().right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth),
  };
 });
 expect(edges.rowRight).toBeLessThanOrEqual(edges.contentRight + 0.5);
});
