import { test, expect } from "@playwright/test";

test("从收件箱直接进入 AI 回复编辑器并查看生成 Trace", async ({ page }, info) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByText("离线演示模式", { exact: true })).toBeVisible();
  const seeded = await page.request.post("/api/mail/test-scenarios", {
    headers: { "X-ZhiXing-Local": "1" }, data: {},
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
  await page.reload();
  const toggle = page.getByLabel("显示测试数据");
  if (!(await toggle.isChecked())) await toggle.check();
  await page.getByLabel("当前邮箱").selectOption("mail-agent-scenarios-v1");
  await page.locator("nav").getByRole("button", { name: "收件箱", exact: true }).click();
  await page.locator(".mail-row").filter({ hasText: "接口确认" }).click();
  await page.locator(".mail-reader").getByRole("button", { name: "AI 起草回复", exact: true }).click();

  await expect(page.getByRole("heading", { name: /编辑草稿/ })).toBeVisible();
  await expect(page.getByText("AI 回复建议", { exact: true })).toBeVisible();
  await expect(page.getByLabel("邮件正文")).not.toHaveValue("");
  await expect(page.getByRole("button", { name: "核对并发送", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "查看生成 Trace", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "运行 Trace" })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "运行 Trace" })).toContainText("生成 AI 回复建议");
  await page.screenshot({ path: info.outputPath("ai-reply-draft.png"), fullPage: true });
  expect(errors).toEqual([]);
});
