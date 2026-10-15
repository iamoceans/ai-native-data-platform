import { expect, test, type Page } from '@playwright/test';

test.use({ locale: "zh-CN" });

/**
 * ChartSpec panel regressions. The bar is a length and the signed value is text
 * beside it: a bar used to hold its own label, so a narrow panel made the text
 * paint over the neighbouring column. Numbers keep a fixed display precision and
 * the exact Decimal stays in `title`, and the panel must say how many points the
 * bounded read window leaves out.
 */
const CHART = {
  schema_version: 1,
  id: 'chart-1',
  spec: {
    schema_version: 1,
    kind: 'bar',
    title: '广告收入 各分组变化贡献',
    data_ref: { type: 'calculation', id: 'd6b97a66-0000-0000-0000-000000000000' },
    encoding: {
      x: { field: 'key', type: 'category' },
      y: { field: 'delta', type: 'quantitative', unit: 'USD' },
      series: null,
    },
    sort: { field: 'delta', direction: 'asc' },
    interaction: { tooltip: true, zoom: false, drilldown_dimensions: ['platform'] },
    evidence_ids: ['q1', 'q2'],
    empty_state: '没有可用的分组',
  },
  data: [
    { group: null, key: 'AppLovin / android / US', dimension_values: ['AppLovin'], delta: '-2475.870515', net_change_share: '0.9843261372777194934258249453' },
    { group: null, key: 'AdMob / ios / DE', dimension_values: ['AdMob'], delta: '22.559426', net_change_share: '-0.0089860273362677551119769' },
    { group: null, key: 'Vungle / ios / US', dimension_values: ['Vungle'], delta: '-8.228817', net_change_share: null },
  ],
  total_points: 576,
  offset: 0,
  limit: 200,
  truncated: false,
  dropped_points: 0,
  empty_state: '没有可用的分组',
};

async function fixture(page: Page, chart: Record<string, unknown> = {}) {
  await page.context().route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: any = { items: [] };
    if (path.endsWith('/auth/me')) body = { username: 'test', roles: [], capabilities: ['analysis.create'] };
    else if (path.endsWith('/charts/chart-1')) body = { ...CHART, ...chart };
    else if (path.endsWith('/analyses/a1/memory')) body = { extraction: null, items: [] };
    else if (path.endsWith('/analyses/a1')) body = {
      analysis_id: 'a1', status: 'COMPLETED', steps: [], state: {},
      question: '昨天收入下降的主要原因是什么？', checkpoint_version: 2, policy_revision: 1,
      final_report: { status: 'COMPLETED', claims: [], limitations: [], chart_ids: ['chart-1'] },
    };
    await route.fulfill({ json: body });
  });
  await page.goto('/analyses/a1');
  await expect(page.getByTestId('chart-panel')).toBeVisible();
}

async function rows(page: Page) {
  return page.locator('[data-testid=chart-bars] li').evaluateAll(nodes => nodes.map(li => {
    const head = li.querySelector('.chart-row-head') as HTMLElement;
    const box = li.getBoundingClientRect();
    return {
      label: (li.querySelector('.chart-label')!.textContent || '').trim(),
      value: (li.querySelector('.chart-value')!.textContent || '').trim(),
      valueTitle: li.querySelector('.chart-value')!.getAttribute('title'),
      share: (li.querySelector('[class~="small"]')!.textContent || '').trim(),
      shareTitle: li.querySelector('.chart-share')?.getAttribute('title') ?? null,
      barText: (li.querySelector('.chart-bar')!.textContent || '').trim(),
      barWidth: +li.querySelector('.chart-bar')!.getBoundingClientRect().width.toFixed(1),
      barInside: li.querySelector('.chart-bar')!.getBoundingClientRect().right <= box.right + 1,
      overflowing: [...head.children].filter(c => c.scrollWidth > (c as HTMLElement).clientWidth + 1).length,
    };
  }));
}

test('keeps each value beside its bar, formatted, with the exact number on hover', async ({page}) => {
  await fixture(page);
  const measured = await rows(page);
  expect(measured).toHaveLength(3);

  expect(measured[0].label).toBe('AppLovin / android / US');
  expect(measured.map(row => row.value)).toEqual(['▼ -2,475.87 USD', '▲ 22.56 USD', '▼ -8.23 USD']);
  expect(measured.map(row => row.share)).toEqual(['净变化占比 98.4%', '净变化占比 -0.9%', '基准为零，无法计算占比']);
  expect(measured[0].valueTitle).toBe('-2475.870515 USD');
  expect(measured[0].shareTitle).toBe('0.9843261372777194934258249453');

  for (const row of measured) {
    // No bar carries its own label, nothing overflows its column, and a bar
    // never leaves the row it belongs to.
    expect(row.barText).toBe('');
    expect(row.overflowing).toBe(0);
    expect(row.barInside).toBe(true);
  }
  // The largest change fills the track; the smaller ones keep a visible sliver.
  expect(measured[0].barWidth).toBeGreaterThan(measured[1].barWidth);
  expect(measured[1].barWidth).toBeGreaterThanOrEqual(8);
});

test('shows the same numbers in the table view', async ({page}) => {
  await fixture(page);
  await page.getByTestId('chart-toggle-table').click();
  const table = page.getByTestId('chart-table');
  await expect(table).toBeVisible();
  const first = table.locator('tbody tr').first();
  await expect(first).toContainText('▼ -2,475.87 USD');
  await expect(first).toContainText('98.4%');
  expect(await first.locator('td').first().getAttribute('title')).toBe('-2475.870515 USD');
  expect(await first.locator('td').nth(1).getAttribute('title')).toBe('0.9843261372777194934258249453');
});

test('states the points the read window leaves out', async ({page}) => {
  await fixture(page);
  await expect(page.locator('.notice.warn')).toContainText('只显示前 3 个分组，另有 573 个未显示');
  await expect(page.locator('[data-testid=chart-panel] p.muted.small').last()).toContainText('图表 3 / 共 576 点');
});

test('reports points dropped when the chart was stored', async ({page}) => {
  await fixture(page, { total_points: 3, truncated: true, dropped_points: 12 });
  await expect(page.locator('.notice.warn')).toContainText('存储图表时已丢弃 12 个分组');
});

test('stays quiet when the panel shows every point', async ({page}) => {
  await fixture(page, { total_points: 3 });
  await expect(page.locator('.notice.warn')).toHaveCount(0);
});

test('stays readable in a narrow panel', async ({page}) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await fixture(page);
  const measured = await rows(page);
  for (const row of measured) {
    expect(row.overflowing).toBe(0);
    expect(row.barInside).toBe(true);
  }
  const panel = page.getByTestId('chart-panel');
  expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/chart-narrow.png', fullPage: true });
});
