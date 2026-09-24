import { test, expect } from '@playwright/test';

test('已入库邮件可在导入页显式批量分析并看到产出（离线演示）', async ({ page }, info) => {
  await page.goto('/');
  await page.request.get('/api/session');
  const fixture = await page.request.post('/api/mail/demo', {
    headers: { 'x-zhixing-local': '1' }, data: {},
  });
  expect(fixture.ok()).toBeTruthy();
  const accountId = (await fixture.json()).account_id as string;
  await page.reload();
  await page.getByLabel('当前邮箱').selectOption(accountId);
  await page.locator('nav').getByRole('button', { name: '收取与导入', exact: true }).click();
  await expect(page.getByText(/已入库 1 封.*待分析 1 封/)).toBeVisible();
  await page.getByRole('button', { name: '分析待处理邮件（最多 20 封）' }).click();
  await expect.poll(async () => {
    const status = await page.request.get(`/api/mail/accounts/${accountId}/perception/summary`);
    return (await status.json()).ready;
  }, { timeout: 15000 }).toBe(1);
  await expect(page.getByText(/已入库 1 封.*已产出 1 封/)).toBeVisible();
  await page.locator('nav').getByRole('button', { name: '收件箱', exact: true }).click();
  await page.locator('.mail-row').first().click();
  await expect(page.getByRole('heading', { name: 'AI 感知结果' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('import-analysis.png'), fullPage: true });
});
