import {test,expect,Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
const screenshots=process.env.QUALITY_E2E_SCREENSHOTS??'../reports';
const data=JSON.parse(readFileSync(process.env.QUALITY_ADVANCED_FIXTURE!,'utf8'));
async function login(page:Page,url:string,username:string){
 await page.goto(url);await page.getByLabel('账号',{exact:true}).fill(username);await page.getByLabel('密码',{exact:true}).fill(data.password);
 await page.getByRole('button',{name:/登\s*录/}).click();await expect(page.getByRole('heading',{name:'品质总览',exact:true})).toBeVisible();
}
async function choose(page:Page,label:string,text:string){
 const control=page.getByRole('combobox',{name:new RegExp(label+'$')});
 await control.focus();await control.press('ArrowDown');
 await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).click();
 await page.locator('.ant-modal:visible .ant-modal-title').click();
}

async function refreshDistributions(page:Page){
 const response=page.waitForResponse(r=>r.url().includes('/api/records/distributions?'));
 await page.getByRole('button',{name:'刷新分发状态',exact:true}).click();expect((await response).status()).toBe(200);
 await expect(page.locator('[data-table-id="distributionpanel-1"] .ant-spin-spinning')).toHaveCount(0);
}

test('中心页面分发、本站预览确认、真实回执、停用与回退',async({browser})=>{
 const c=await browser.newContext(),s=await browser.newContext();const center=await c.newPage(),site=await s.newPage();
 try{
 await login(center,data.center_url,'center-admin');await login(site,data.site_url,'site-admin');
 await center.getByRole('menuitem',{name:/配置分发$/}).click();await site.getByRole('menuitem',{name:/配置分发$/}).click();
 for(const [index,action] of ['发布','停用','回退'].entries()){
  await center.getByRole('button',{name:'新建批量分发'}).click();await choose(center,'已审核配置版本',data.knowledge_title+' · v1');
  await choose(center,'目标节点','浏览器独立站点 (browser-site)');await choose(center,'分发操作',action);await center.getByLabel('变更及回退依据').fill('浏览器真实流程验证依据');
  await center.getByRole('button',{name:'确 定',exact:true}).click();await expect(center.getByText('已排队，等待真实站点接收与办理')).toBeVisible();
  await expect.poll(async()=>{await refreshDistributions(site);return site.getByRole('row').filter({hasText:'已接收待确认'}).count();}).toBe(1);
  await site.getByRole('row').filter({hasText:'已接收待确认'}).getByRole('button',{name:'查看与预览'}).click();
  await site.getByRole('button',{name:'确认本站应用'}).click();await site.getByLabel('本站办理依据').fill('本站工程师核对签名内容与适用范围');await site.getByRole('button',{name:'确 定',exact:true}).click();
  await expect(site.getByText('本站办理已保存，联网同步后中心收到回执')).toBeVisible();
  await site.getByRole('button',{name:/关\s*闭/,exact:true}).click();await expect(site.getByRole('dialog',{name:'分发记录与差异预览'})).toBeHidden();
  await expect.poll(async()=>{await refreshDistributions(center);return center.getByRole('row').filter({hasText:'已应用'}).count();}).toBe(index+1);
 }
 await site.screenshot({path:screenshots+'/distribution-applied.png',fullPage:true});
 }finally{await c.close();await s.close();}
});
test('诊断补证任务、实际证据核实与连续窗口配置',async({page})=>{
 await login(page,data.site_url,'site-admin');await page.getByRole('menuitem',{name:/异常案件$/}).click();
 await page.getByRole('button',{name:data.case_title,exact:true}).click();await page.getByRole('button',{name:'启动证据诊断'}).click();
 await expect(page.getByText('诊断已记录')).toBeVisible();await expect(page.getByText('历史证据特征',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'办理补证'}).first().click();await choose(page,'任务关联证据',data.evidence_name);await page.getByLabel('补证办理说明').fill('核对原始测量记录，仍需人工复核');
 await page.getByRole('button',{name:'证据核实完成'}).click();await expect(page.getByText('补证任务办理已记录')).toBeVisible();await expect(page.getByText('已补证',{exact:true})).toBeVisible();await expect(page.getByRole('dialog',{name:'办理诊断补证任务'})).toBeHidden();
 await page.screenshot({path:screenshots+'/evidence-tasks.png',fullPage:true});
 await page.locator('.ant-drawer-close').click();await page.getByRole('menuitem',{name:'运行与审计'}).click();await page.getByRole('button',{name:'配置连续窗口触发'}).click();await choose(page,'策略产线',data.line_name);
 await page.getByRole('button',{name:'确 定',exact:true}).click();await expect(page.getByText('异常窗口策略已保存')).toBeVisible();
});
