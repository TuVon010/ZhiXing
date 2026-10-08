import { test, expect } from '@playwright/test';

test('人工标注、账号隔离、评测报告与撤回（离线演示）', async ({ page }, info) => {
  test.setTimeout(90000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  const seeded = await page.evaluate(async () => {
    const r = await fetch('/api/mail/test-scenarios', { method: 'POST',
      headers: { 'X-ZhiXing-Local': '1', 'Content-Type': 'application/json' }, body: '{}' });
    return r.ok;
  });
  expect(seeded).toBeTruthy();
  await page.reload();
  const showTest = page.getByLabel('显示测试数据');
  if (!await showTest.isChecked()) await showTest.check();
  const account = 'mail-agent-scenarios-v1';
  await page.getByLabel('当前邮箱').selectOption(account);
  await page.locator('nav').getByRole('button', { name: '收件箱', exact: true }).click();
  const response = await page.request.get(`/api/mail/messages?status=inbox&account_id=${account}&include_test=true`);
  const target = (await response.json()).items.find((r: any) => r.body.subject === '实验报告修改');
  expect(target).toBeTruthy();
  await page.locator('.mail-row').filter({ has: page.getByText(target.body.subject, { exact: true }) }).click();
  await page.getByRole('button', { name: '立即分析', exact: true }).click();
  await expect.poll(async () => (await (await page.request.get(`/api/mail/messages/${target.id}/perception`)).json()).status,
    { timeout: 15000 }).toBe('ready');
  await page.locator('.mail-row').filter({ has: page.getByText(target.body.subject, { exact: true }) }).click();
  await page.getByText('调整 AI 判断', { exact: true }).click();
  await page.getByLabel('邮件性质').selectOption('normal');
  await page.getByRole('button', { name: '确认所选判断（抽查）', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('抽查标注');
  const base = `/api/mail/accounts/${account}/classification`;
  const labels = await (await page.request.get(base)).json();
  const label = labels.items.find((r: any) => r.body.message_id === target.id);
  expect(label.body.labels.spam_label).toBe('normal');
  expect(label.body.labels.category).toBe('work');
  await page.locator('nav').getByRole('button', { name: '设置与计价', exact: true }).click();
  const panel = page.locator('.classification-settings');
  await expect(panel.getByRole('heading', { name: '分类反馈与效果验证' })).toBeVisible();
  await panel.getByText('查看确认标注（最近 30 封）', { exact: true }).click();
  await expect(panel.getByText(target.body.subject, { exact: true })).toBeVisible();
  await panel.getByLabel('评测分组').selectOption(label.body.split);
  await panel.getByLabel('最多邮件数').fill('1');
  await panel.getByRole('button', { name: '开始三组对比', exact: true }).click();
  await expect.poll(async () => (await (await page.request.get(base)).json()).evaluations[0]?.status,
    { timeout: 15000 }).toBe('unverified');
  await panel.getByRole('button', { name: '刷新标注与评测', exact: true }).click();
  await panel.getByRole('button', { name: '查看对比报告', exact: true }).first().click();
  await expect(panel.getByRole('heading', { name: '分类对比报告' })).toBeVisible();
  await expect(panel.getByText('演示模式未执行真实 API 评测')).toBeVisible();
  await panel.getByRole('button', { name: '查看评测 Trace', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: '分类评测 Trace' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('classification-trace.png'), fullPage: true });
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await panel.getByRole('button', { name: '保存完整报告到本地', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('完整报告已保存');
  await page.screenshot({ path: info.outputPath('classification-report.png'), fullPage: true });
  await panel.getByRole('button', { name: '移出标注集', exact: true }).click();
  await expect.poll(async () => (await (await page.request.get(base)).json()).total).toBe(0);
  // Withdrawing a training/evaluation label must not undo the user's mail correction.
  expect((await (await page.request.get(`/api/mail/messages/${target.id}`)).json()).body.perception_overrides.spam_label).toBe('normal');
  const foreign = await page.request.post(`/api/mail/accounts/not-this-account/classification/labels/${label.id}/withdraw`,
    { data: {}, headers: { 'X-ZhiXing-Local': '1' } });
  expect(foreign.ok()).toBeFalsy();
  await page.getByLabel('当前邮箱').selectOption('');
  await expect(panel.getByText('请先选择一个邮箱。标注、参考案例和分类版本按邮箱隔离。')).toBeVisible();
  await expect(panel.getByRole('heading', { name: '分类对比报告' })).toHaveCount(0);
  expect(errors).toEqual([]);
});
