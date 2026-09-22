import {test,expect} from '@playwright/test';
test('消息 → 任务 → 持久审批 → 日程，关键页面与候选管理',async({page},testInfo)=>{
 const errors:string[]=[];page.on('pageerror',e=>{errors.push(e.message);console.log('PAGE ERROR',e.message)});page.on('console',m=>{if(m.type()==='error')console.log('CONSOLE',m.text())});
 await page.goto('/');
 await expect(page).toHaveTitle('知性 · 个人工作助理');
 await expect(page.getByAltText('知性标志')).toBeVisible();
 expect(await page.getByAltText('知性标志').evaluate((el:HTMLImageElement)=>el.complete&&el.naturalWidth>0)).toBeTruthy();
 expect((await page.request.get('/zhixing-mark.svg')).status()).toBe(200);
 await expect(page.getByText('离线演示模式')).toBeVisible();
 await page.getByRole('button',{name:'填入演示案例'}).click();
 await page.getByRole('button',{name:'开始处理'}).click();
 await expect(page.getByRole('status')).toContainText('已进入工作流');
 await page.locator('nav').getByRole('button',{name:'审批中心'}).click();
 await expect(page.getByRole('button',{name:'批准',exact:true}).first()).toBeVisible({timeout:15000});
 await page.reload();
 await page.locator('nav').getByRole('button',{name:'审批中心'}).click();
 await page.getByRole('button',{name:'批准',exact:true}).first().click();
 await expect(page.getByText('send_feishu',{exact:true})).toBeVisible({timeout:15000});
 await page.getByRole('button',{name:'拒绝',exact:true}).first().click();
 await page.locator('nav').getByRole('button',{name:'日程',exact:false}).click();
 await expect(page.getByRole('heading',{name:'组会',exact:true})).toBeVisible();
 await page.locator('nav').getByRole('button',{name:'Agent 运行'}).click();
 await page.getByRole('button',{name:'查看执行链'}).first().click();
 await expect(page.getByRole('heading',{name:'运行 Trace'})).toBeVisible();
 await expect(page.locator('.timeline')).toContainText('运行结束');
 await expect(page.locator('.trace-heading code')).toHaveText(/[a-f0-9]{32}/);
 await expect(page.getByText('关联模型调用',{exact:true})).toBeVisible();
 await expect(page.getByRole('heading',{name:'Jev 影子评审'})).toBeVisible();
 await expect(page.getByText('本次没有 Jev 记录（默认关闭或旧运行）。')).toBeVisible();
 const exported=page.waitForEvent('download');
 await page.getByRole('button',{name:'导出 Trace JSON'}).click();
 const download=await exported;
 expect(download.suggestedFilename()).toMatch(/^zhixing-trace-[a-f0-9]+\.json$/);
 await download.saveAs(testInfo.outputPath('agent-trace.json'));
 await page.screenshot({path:testInfo.outputPath('trace-preview.png'),fullPage:true});
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.locator('nav').getByRole('button',{name:'记忆',exact:false}).click();
 await page.getByRole('button',{name:'记忆候选'}).click();
 await page.getByLabel('内容',{exact:true}).fill('我偏好中文摘要');
 await page.getByRole('button',{name:'保存',exact:true}).click();
 await page.getByRole('button',{name:'确认生效'}).click();
 await expect(page.locator('.badge.published')).toBeVisible();
 for(const name of ['Skills','Parsers','渐进式信任','评测','审计日志','设置']){
  await page.locator('nav').getByRole('button',{name,exact:false}).click();
  await expect(page.getByRole('heading',{name,exact:true}).first()).toBeVisible();
 }
 await page.locator('nav').getByRole('button',{name:'工作概览'}).click();
 await page.screenshot({path:testInfo.outputPath('console-preview.png'),fullPage:true});
 expect(errors).toEqual([]);
});

test('Jev 评审展示与导出（明确使用模拟 API 记录）',async({page},testInfo)=>{
 await page.route('**/api/runs/*',async route=>{
  const response=await route.fetch();
  const body=await response.json();
  if(body.trace){
   body.jev_calls=[{id:'mock-jev-review',created_at:new Date().toISOString(),status:'completed',body:{
    run_id:body.run.id,trace_id:body.run.id,model:'模拟 Jev 模型',advisory_only:true,cost:null,latency_ms:100,
    response:{model:'模拟 Jev 模型',answers:{missing_intent:{type:'noul',noul:.85},unsupported_assumption:{type:'noul',noul:.1},needs_clarification:{type:'noul',noul:.8}}},
   }}];
  }
  await route.fulfill({response,json:body});
 });
 await page.goto('/');
 await page.getByLabel('消息内容').fill('待办：准备 Jev 模拟展示');
 await page.getByRole('button',{name:'开始处理'}).click();
 await expect(page.getByRole('status')).toContainText('已进入工作流');
 await page.locator('nav').getByRole('button',{name:'Agent 运行'}).click();
 await page.getByRole('button',{name:'查看执行链'}).first().click();
 await page.locator('summary').filter({hasText:'模拟 Jev 模型'}).click();
 await expect(page.getByText('遗漏明确请求：85.0%')).toBeVisible();
 await expect(page.getByText('辅助判断，不改变动作或审批。概率来自模型，尚不代表本项目中文场景的实测准确率。')).toBeVisible();
 const exported=page.waitForEvent('download');
 await page.getByRole('button',{name:'导出 Trace JSON'}).click();
 await (await exported).saveAs(testInfo.outputPath('mock-jev-trace.json'));
 await page.screenshot({path:testInfo.outputPath('mock-jev-review.png'),fullPage:true});
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.locator('nav').getByRole('button',{name:'设置',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Jev 辅助评审'})).toBeVisible();
 await expect(page.getByText('已关闭',{exact:true})).toBeVisible();
});
