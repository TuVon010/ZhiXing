import { test, expect } from '@playwright/test';

test('检索问答按会话保存、追问并定位原邮件', async ({ page }, info) => {
  test.setTimeout(90000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('button', { name: '导入演示邮件' })).toBeVisible();
  await page.getByRole('button', { name: '导入演示邮件' }).click();
  await page.locator('nav').getByRole('button', { name: '邮件智能检索助手' }).click();
  await expect(page.getByRole('heading', { name: '从邮件中找到答案', level: 2 })).toBeVisible();

  await page.getByRole('button', { name: '只找原文' }).click();
  await page.getByLabel('邮件问题').fill('实验报告修改');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.locator('.assistant-evidence').first()).toBeVisible({ timeout: 20000 });
  await expect(page.locator('.assistant-session').first()).toContainText('实验报告修改');

  await page.getByRole('button', { name: '整理回答' }).click();
  await page.getByLabel('邮件问题').fill('其中要求我什么时候完成？');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.locator('.assistant-user-message')).toHaveCount(2, { timeout: 20000 });
  await expect(page.locator('.assistant-agent-message').last()).toContainText('离线演示', { timeout: 20000 });
  await page.reload();
  await expect(page.locator('.assistant-user-message')).toHaveCount(2);

  await page.getByRole('button', { name: '＋ 新建会话' }).click();
  await expect(page.locator('.assistant-evidence')).toHaveCount(0);
  await page.locator('.assistant-session-open').first().click();
  await expect(page.locator('.assistant-user-message')).toHaveCount(2);
  await page.locator('.assistant-evidence').first().click();
  await expect(page.locator('.mail-reader h2')).toContainText('实验报告修改');
  expect(errors).toEqual([]);
});

test('检索会话可重命名、移入回收站并恢复', async ({ page }) => {
  await page.goto('/');
  const demo = page.getByRole('button', { name: '导入演示邮件' });
  if (await demo.isVisible()) await demo.click();
  await page.locator('nav').getByRole('button', { name: '邮件智能检索助手' }).click();
  await page.getByRole('button', { name: '＋ 新建会话' }).click();
  await page.getByRole('button', { name: '只找原文' }).click();
  await page.getByLabel('邮件问题').fill('实验报告修改');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.locator('.assistant-session').first()).toBeVisible();

  await page.locator('.assistant-session.selected').getByRole('button', { name: /重命名会话/ }).click();
  await page.getByLabel('会话名称').fill('导师邮件');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.locator('.assistant-session.selected')).toContainText('导师邮件');
  await page.locator('.assistant-session.selected').getByRole('button', { name: /删除会话/ }).click();
  await expect(page.locator('.assistant-session').filter({ hasText: '导师邮件' })).toHaveCount(0);
  await page.locator('nav').getByRole('button', { name: '记录管理' }).click();
  await page.getByLabel('记录类型').selectOption('assistant_session');
  await page.getByLabel('查看回收站').check();
  await expect(page.getByText('导师邮件')).toBeVisible();
  await page.getByRole('button', { name: '恢复', exact: true }).first().click();
  await page.locator('nav').getByRole('button', { name: '邮件智能检索助手' }).click();
  await expect(page.locator('.assistant-session').first()).toContainText('导师邮件');
});
