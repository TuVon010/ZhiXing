import { test, expect } from '@playwright/test';

test('邮件感知、Trace 与可撤销纠偏形成闭环（离线演示）', async ({ page }, info) => {
  test.setTimeout(90000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByText('离线演示模式', { exact: true })).toBeVisible();
  const seed = await page.evaluate(async () => {
    const response = await fetch('/api/mail/test-scenarios', { method: 'POST', headers: { 'X-ZhiXing-Local': '1', 'Content-Type': 'application/json' }, body: '{}' });
    return { ok: response.ok, text: await response.text() };
  });
  expect(seed.ok, seed.text).toBeTruthy();
  await page.reload();
  const toggle = page.getByLabel('显示测试数据');
  if (!(await toggle.isChecked())) await toggle.check();
  await page.getByLabel('当前邮箱').selectOption('mail-agent-scenarios-v1');
  await page.locator('nav').getByRole('button', { name: '收件箱', exact: true }).click();
  const response = await page.request.get('/api/mail/messages?status=inbox&account_id=mail-agent-scenarios-v1&include_test=true');
  const items = (await response.json()).items as any[];
  const target = items.find(item => item.body.subject === '实验报告修改') || items[0];
  const messageId = target.id as string;
  const targetRow = page.locator('.mail-row').filter({ has: page.getByText(target.body.subject, { exact: true }) });
  await targetRow.click();
  const analyze = page.getByRole('button', { name: '立即分析' });
  if (await analyze.isVisible()) await analyze.click();
  else await page.getByRole('button', { name: '重新分析', exact: true }).click();
  await expect.poll(async () => {
    const result = await page.request.get(`/api/mail/messages/${messageId}/perception`);
    return (await result.json()).status;
  }, { timeout: 15000 }).toBe('ready');
  await page.locator('.mail-row').filter({ has: page.getByText(target.body.subject, { exact: true }) }).click();
  await expect(page.getByRole('heading', { name: 'AI 感知结果' })).toBeVisible();
  await page.getByRole('button', { name: '查看 Trace', exact: true }).click();
  await expect(page.getByRole('heading', { name: '邮件感知 Trace' })).toBeVisible();
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByText('调整 AI 判断').click();
  await page.getByLabel('邮件性质').selectOption('spam');
  await page.getByRole('button', { name: '保存调整' }).click();
  await expect(page.getByRole('status')).toContainText('本封邮件');
  await page.locator('nav').getByRole('button', { name: '垃圾与过滤', exact: true }).click();
  const filteredRow = page.locator('.mail-row').filter({ has: page.getByText(target.body.subject, { exact: true }) });
  await expect(filteredRow).toBeVisible();
  await filteredRow.click();
  await page.getByText('调整 AI 判断').click();
  await page.getByLabel('邮件性质').selectOption('normal');
  await page.getByRole('button', { name: '保存调整' }).click();
  await expect.poll(async () => {
    const response = await page.request.get(`/api/mail/messages/${messageId}`);
    return (await response.json()).status;
  }).toBe('active');
  await page.locator('nav').getByRole('button', { name: '收件箱', exact: true }).click();
  await expect(page.locator('.mail-row').filter({ has: page.getByText(target.body.subject, { exact: true }) })).toBeVisible();
  expect(errors).toEqual([]);
});
