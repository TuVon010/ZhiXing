import { test, expect } from '@playwright/test';

test('14 封合成邮件贯通账号隔离、感知、跟进和 Trace', async ({ page }, info) => {
  test.setTimeout(90000);
  await page.goto('/');
  await page.locator('nav').getByRole('button', { name: '邮箱账号', exact: true }).click();
  await page.getByRole('button', { name: '创建 Agent 测试邮箱（14 封）' }).click();
  const account = await page.request.get('/api/mail/accounts');
  const testAccount = (await account.json()).items.find((item: any) => item.body.test_account);
  expect(testAccount).toBeTruthy();
  await expect(page.getByLabel('当前邮箱')).toHaveValue(testAccount.id);
  await page.locator('nav').getByRole('button', { name: '收取与导入', exact: true }).click();
  await expect(page.getByText(/已入库 14 封/)).toBeVisible();
  await page.getByRole('button', { name: '分析待处理邮件（最多 20 封）' }).click();
  await expect.poll(async () => {
    const response = await page.request.get(`/api/mail/accounts/${testAccount.id}/perception/summary`);
    return (await response.json()).ready;
  }, { timeout: 30000 }).toBeGreaterThanOrEqual(10);
  await page.locator('nav').getByRole('button', { name: '待办与跟进', exact: true }).click();
  await expect(page.getByRole('tab', { name: /邮件跟进/ })).toBeVisible();
  await page.getByRole('tab', { name: /邮件跟进/ }).click();
  await expect(page.getByRole('button', { name: '起草回复' }).first()).toBeVisible();
  await page.locator('nav').getByRole('button', { name: '收件箱', exact: true }).click();
  await page.locator('.mail-row').first().click();
  await expect(page.getByRole('heading', { name: 'AI 感知结果' })).toBeVisible();
  await page.getByRole('button', { name: '查看感知 Trace' }).click();
  await expect(page.getByRole('heading', { name: '邮件感知 Trace' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('synthetic-agent-flow.png'), fullPage: true });
});
