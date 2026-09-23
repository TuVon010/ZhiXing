import { test, expect } from '@playwright/test';

test('邮件感知、纠偏与待办候选形成可操作闭环（离线演示）', async ({ page }, info) => {
  test.setTimeout(90000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  const importDemo = page.getByRole('button', { name: '导入演示邮件', exact: true });
  if (await importDemo.isVisible()) await importDemo.click();
  await page.getByLabel('当前邮箱').selectOption({ label: '演示邮箱 · demo@example.com' });
  await page.locator('nav').getByRole('button', { name: '收件箱', exact: true }).click();
  const response = await page.request.get('/api/mail/messages?status=inbox');
  const messageId = (await response.json()).items[0].id as string;
  await page.locator('.mail-row').first().click();
  await page.getByRole('button', { name: '分析 / 重新分析' }).click();
  await expect.poll(async () => {
    const result = await page.request.get(`/api/mail/messages/${messageId}/perception`);
    return (await result.json()).status;
  }, { timeout: 15000 }).toBe('ready');
  await page.locator('.mail-row').first().click();
  await expect(page.getByRole('heading', { name: 'AI 感知结果' })).toBeVisible();
  await page.getByRole('button', { name: '查看感知 Trace' }).click();
  await expect(page.getByRole('heading', { name: '邮件感知 Trace' })).toBeVisible();
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByText('纠正感知结果').click();
  await page.getByRole('button', { name: '标记垃圾' }).click();
  await expect(page.getByRole('status')).toContainText('本地过滤箱');
  await page.locator('nav').getByRole('button', { name: '过滤箱', exact: true }).click();
  await expect(page.locator('.mail-row').first()).toBeVisible();
  await page.locator('.mail-row').first().click();
  await page.getByText('纠正感知结果').click();
  await page.getByRole('button', { name: '标记正常' }).click();
  await page.locator('nav').getByRole('button', { name: '待办与跟进', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认加入' }).first()).toBeVisible();
  await page.getByRole('button', { name: '确认加入' }).first().click();
  await expect.poll(async () => {
    const response = await page.request.get('/api/mail/followups');
    return (await response.json()).items.find((item: any) => item.body.source_message === messageId)?.status;
  }).toBe('active');
  await page.screenshot({ path: info.outputPath('perception-followup.png'), fullPage: true });
  expect(errors).toEqual([]);
});
