import { test, expect } from '@playwright/test';

test('本地记录可移入回收站并恢复，旧数据可盘点与备份', async ({ page }, info) => {
  await page.goto('/');
  const importDemo = page.getByRole('button', { name: '导入演示邮件', exact: true });
  if (await importDemo.isVisible()) await importDemo.click();
  await page.getByLabel('当前邮箱').selectOption({ label: '演示邮箱 · demo@example.com' });
  await page.locator('nav').getByRole('button', { name: '记录管理', exact: true }).click();
  await expect(page.getByRole('heading', { name: '本地记录管理' })).toBeVisible();
  await expect(page.getByRole('heading', { name: /记录列表/ })).toBeVisible();
  await page.getByRole('button', { name: '移入回收站' }).first().click();
  await expect(page.getByRole('status')).toContainText('本地回收站');
  await page.getByLabel('查看回收站').check();
  await expect(page.getByRole('button', { name: '恢复' }).first()).toBeVisible();
  await page.getByRole('button', { name: '恢复' }).first().click();
  await expect(page.getByRole('status')).toContainText('记录已恢复');
  await page.getByLabel('记录类型').selectOption('legacy_message');
  await expect(page.getByText(/旧邮件来源字段无法证明/)).toBeVisible();
  await page.getByRole('button', { name: '生成旧数据备份' }).click();
  await expect(page.getByRole('status')).toContainText('备份已保存');
  await page.screenshot({ path: info.outputPath('records-manager.png'), fullPage: true });
});
