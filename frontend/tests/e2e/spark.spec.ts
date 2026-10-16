import { expect, test } from '@playwright/test';

test.skip(!process.env.E2E_BASE_URL || !process.env.E2E_SPARK_DATASOURCE, 'requires a registered Spark fixture');

test('Spark over verified TLS executes and renders scalar parameters through the gateway', async ({ page }) => {
  const name=process.env.E2E_SPARK_DATASOURCE!;
  await page.goto('/sql');
  const option=page.locator('[data-testid="datasource-select"] option').filter({hasText:name});
  await expect(option).toHaveCount(1);
  const datasourceId=await option.getAttribute('value');
  await page.getByTestId('datasource-select').selectOption(datasourceId!);
  await page.getByTestId('sql-editor').fill('SELECT id, label FROM ops.browser_fixture');
  await page.getByTestId('run-query').click();
  await expect(page.locator('.result-table')).toContainText('secure-spark',{timeout:30000});
  const csrf=(await page.context().cookies()).find(cookie=>cookie.name==='ainative_csrf')!.value;
  const label="a'; SELECT secret FROM forbidden; --";
  const submitted=await page.request.post('/api/v1/queries',{headers:{'X-CSRF-Token':csrf},data:{
    datasource_id:datasourceId,sql:'SELECT :label AS echoed FROM ops.browser_fixture WHERE id = :id',parameters:{label,id:7}}});
  expect(submitted.status()).toBe(202);
  const result=await submitted.json();
  await page.goto(`/sql?query=${result.query_id}`);
  await expect(page.locator('.result-table')).toContainText(label,{timeout:30000});
});
