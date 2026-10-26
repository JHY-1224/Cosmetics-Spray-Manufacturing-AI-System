import {test,expect,Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
const screenshots=process.env.QUALITY_E2E_SCREENSHOTS??'../reports';

const scopeFixture=process.env.QUALITY_DELIVERY_SCOPE_FIXTURE;
const passwords=scopeFixture?{}:JSON.parse(readFileSync(process.env.QUALITY_E2E_CREDENTIALS??'../var/demo-credentials.json','utf8'));
async function login(page:Page,role='engineer',url='/',password=passwords[role]){
 await page.goto(url);await page.getByLabel('账号',{exact:true}).fill(role);await page.getByLabel('密码',{exact:true}).fill(password);
 await page.getByRole('button',{name:/登\s*录/}).click();await expect(page.getByRole('heading',{name:'品质总览',exact:true})).toBeVisible();
}
async function choose(page:Page,label:string,text:string){
 const root=label.startsWith('筛选')?page:page.locator('.ant-modal:visible');
 if(label.startsWith('筛选')){const control=page.getByRole('combobox',{name:label,exact:true});await control.focus();await control.press('ArrowDown');}else await root.getByLabel(label,{exact:true}).click();
 if(label==='现场证据')await root.getByLabel(label,{exact:true}).fill(text);
 await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(text,{exact:true}).click();
 if(!label.startsWith('筛选'))await page.locator('.ant-modal:visible .ant-modal-title').click();
}
async function settled(page:Page){await expect(page.locator('.ant-spin-spinning')).toHaveCount(0);}

if(!scopeFixture){
test('晚到的文件列表不能覆盖刚上传的新证据',async({page})=>{
 let release!:()=>void;
 const gate=new Promise<void>(resolve=>release=resolve);
 let retained=false;
 await page.route('**/api/files**',async route=>{
  if(route.request().method()!=='GET'||new URL(route.request().url()).pathname!=='/api/files'){await route.continue();return;}
  const response=await route.fetch();retained=true;await gate;await route.fulfill({response});
 });
 try{
  await login(page);await expect.poll(()=>retained).toBe(true);
  const name='late-list-evidence-'+Date.now()+'.txt';
  await page.getByRole('menuitem',{name:'检测与检验',exact:true}).click();
  await page.locator('input[type=file]').setInputFiles({name,mimeType:'text/plain',buffer:Buffer.from('Synthetic delayed list fixture')});
  await expect(page.getByText('证据已保存，可在表单中选择')).toBeVisible();release();await settled(page);
  await page.getByRole('menuitem',{name:'异常案件',exact:true}).click();await page.getByRole('button',{name:'新建异常',exact:true}).click();
  await choose(page,'现场证据',name);await expect(page.locator('.ant-modal .ant-select-selection-item').filter({hasText:name})).toBeVisible();
 }finally{release();}
});

test('范围URL、刷新后退、延迟请求隔离和导出保持一致',async({page})=>{
 await login(page);const lines=await (await page.request.get('/api/masters?kind=line')).json();
 const plastic=lines.find((l:any)=>l.data.process==='plastic_spray'),glass=lines.find((l:any)=>l.data.process==='glass_vacuum');
 await page.getByRole('menuitem',{name:'异常案件',exact:true}).click();
 await choose(page,'筛选工厂','factory-a');await choose(page,'筛选产线',plastic.name);await settled(page);
 const plasticCases=await (await page.request.get('/api/cases?line_id='+plastic.id)).json();
 const title=plasticCases[0].title;await expect(page.getByRole('button',{name:title,exact:true})).toBeVisible();
 await expect(page).toHaveURL(new RegExp('line_id='+plastic.id));await page.reload();await settled(page);
 await expect(page.getByRole('button',{name:title,exact:true})).toBeVisible();
 const href=await page.getByRole('link',{name:'导出授权范围'}).getAttribute('href');expect(href).toContain('line_id='+plastic.id);
 const csv=await (await page.request.get(href!)).text();expect(csv).toContain(plastic.id);expect(csv).not.toContain(glass.id);
 await page.route('**/api/cases?**',async route=>{if(new URL(route.request().url()).searchParams.get('line_id')===glass.id)await new Promise(r=>setTimeout(r,700));await route.continue();});
 await choose(page,'筛选产线',glass.name);await expect(page.getByRole('button',{name:title,exact:true})).toHaveCount(0);await settled(page);
 await page.goBack();await settled(page);await expect(page).toHaveURL(new RegExp('line_id='+plastic.id));
 await expect(page.getByRole('button',{name:title,exact:true})).toBeVisible();
 await page.screenshot({path:screenshots+'/delivery-filter.png',fullPage:true,animations:'disabled'});
});

for(const restricted of [false,true]){
test('提交已落库但响应及查询丢失，'+(restricted?'授权变化后只确认提交状态':'查询原结果')+'后关闭表单且不重复建案',async({page})=>{
 await login(page);await settled(page);const evidenceName='recovery-'+Date.now()+'.txt';
 await page.getByRole('menuitem',{name:'检测与检验',exact:true}).click();
 await page.locator('input[type=file]').setInputFiles({name:evidenceName,mimeType:'text/plain',buffer:Buffer.from('Synthetic recovery evidence only')});
 await expect(page.getByText('证据已保存，可在表单中选择')).toBeVisible();
 await page.getByRole('menuitem',{name:'异常案件',exact:true}).click();
 const name='响应丢失恢复夹具 '+Date.now();let committed='',blockLookup=true;const keys:string[]=[];
 await page.route('**/api/commands/*',async route=>{if(blockLookup)await route.abort('failed');else if(restricted)await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'committed_restricted',message:'原提交已完成；当前授权不能读取旧结果，请按原对象核对，不要重复提交'})});else await route.continue();});
 await page.route('**/api/cases',async route=>{
  if(route.request().method()!=='POST'){await route.continue();return;}
  keys.push(route.request().headers()['idempotency-key']);const response=await route.fetch();expect(response.status()).toBe(200);
  committed=(await response.json()).id;await route.abort('failed');
 });
 await page.getByRole('button',{name:'新建异常',exact:true}).click();await page.getByLabel('异常标题').fill(name);
 await choose(page,'所属产线','塑料瓶喷涂一线');await choose(page,'工艺','塑料瓶喷涂');await page.getByLabel('目标缺陷').fill('颗粒');await choose(page,'现场证据',evidenceName);
 await page.getByRole('button',{name:'确 定',exact:true}).click();
 await expect(page.getByText('有提交结果待确认',{exact:true})).toBeVisible();expect(committed).toBeTruthy();expect(keys).toHaveLength(1);
 const stored=await page.evaluate(()=>sessionStorage.getItem('quality.pending-operations.v1'));
 expect(stored).toContain(keys[0]);expect(stored).not.toContain(name);
 blockLookup=false;
 if(restricted){
  const me=await (await page.request.get('/api/auth/me')).json();
  await page.route('**/api/auth/me',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...me,lines:['synthetic-narrowed-line']})}));
 }
 // Query from the global recovery banner while retaining the original form's unknown state.
 await page.getByRole('button',{name:'查询原提交结果',exact:true}).click();
 await expect(page.getByText('有提交结果待确认',{exact:true})).toHaveCount(0);
 await expect(page.locator('.ant-modal:visible')).toHaveCount(0);
 const records=await (await page.request.get('/api/cases?q='+encodeURIComponent(name))).json();expect(records.map((r:any)=>r.id)).toEqual([committed]);
 expect(keys).toHaveLength(1);
 if(restricted)await expect(page.getByText('原提交已完成；当前授权不能读取旧结果，请按原对象核对，不要重复提交',{exact:true})).toBeVisible();
 await page.screenshot({path:screenshots+'/delivery-recovered'+(restricted?'-restricted':'')+'.png',fullPage:true,animations:'disabled'});
});
}

test('十八工作区、平板键盘导航、部分失败与错误URL保持可恢复',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await login(page);
 const views=['品质总览','异常案件','检测与检验','生产追溯','质量处置','工艺路线','配方版本','生产基础数据','质量标准与判定','材料混配与称量','订单与物料协同','成本与单据核对','数字任务与预授权','知识与规则','接入管理','配置分发','站点协作','运行与审计'];
 for(const [index,name] of views.entries()){
  await page.getByRole('menuitem',{name,exact:true}).click();await expect(page.getByRole('heading',{name,exact:true})).toBeVisible();await settled(page);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`${screenshots}/delivery-workspace-${index+1}.png`,fullPage:true,animations:'disabled'});
 }
 await page.getByRole('menuitem',{name:'品质总览',exact:true}).click();await settled(page);
 await page.route('**/api/operations?**',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:{code:'synthetic_unavailable',message:'本机故障注入：运行状态暂不可用'}})}));
 await page.locator('.page-title').getByRole('button',{name:/刷\s*新/}).click();await expect(page.getByText(/部分数据暂不可用；已保留当前范围可信结果/)).toBeVisible();
 await page.screenshot({path:screenshots+'/delivery-partial-failure.png',fullPage:true,animations:'disabled'});await page.unroute('**/api/operations?**');
 await page.setViewportSize({width:768,height:1024});const menu=page.getByRole('button',{name:'打开工作区菜单'});await menu.focus();await page.keyboard.press('Enter');
 await page.locator('.ant-drawer-body').getByRole('menuitem',{name:'异常案件',exact:true}).click();await settled(page);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:screenshots+'/delivery-tablet.png',fullPage:true,animations:'disabled'});
 await page.getByRole('button',{name:'新建异常',exact:true}).click();
 await page.getByRole('button',{name:'确 定',exact:true}).click();
 await expect(page.getByText('请填写异常标题',{exact:true})).toBeVisible();
 await expect(page.locator('.ant-modal:visible')).toHaveCount(1);
 await page.screenshot({path:screenshots+'/delivery-form-validation-768.png',animations:'disabled'});
 await page.locator('.ant-modal:visible').getByRole('button',{name:'取 消',exact:true}).click();
 await expect(page.locator('.ant-modal:visible')).toHaveCount(0);
 await page.goto('/?view=cases&start=invalid-date');await expect(page.getByRole('heading',{name:'异常案件',exact:true})).toBeVisible();
 await expect(page.getByText(/部分数据暂不可用/)).toBeVisible();
 await page.route('**/api/cases?**',route=>route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:{code:'synthetic_expired',message:'会话失效，请重新登录'}})}));
 await page.locator('.page-title').getByRole('button',{name:/刷\s*新/}).click();await expect(page.getByLabel('账号',{exact:true})).toBeVisible();
 await expect(page.getByRole('heading',{name:'异常案件',exact:true})).toHaveCount(0);expect(errors).toEqual([]);
});
}else{
test('多厂真实摘要切换、产线筛选、刷新后退及过期响应隔离',async({page})=>{
 const fixture=JSON.parse(readFileSync(scopeFixture,'utf8'));await login(page,'admin',fixture.center_url,fixture.password);
 await page.getByRole('menuitem',{name:'站点协作',exact:true}).click();await settled(page);
 const all=await (await page.request.get(fixture.center_url+'/api/site-snapshots')).json();expect(new Set(all.map((r:any)=>r.site_id)).size).toBe(2);
 await choose(page,'筛选工厂','factory-a');await settled(page);
 const table=page.locator('.ant-card').filter({hasText:'站点案件闭环与证据汇总'});
 await expect(table.getByRole('cell',{name:'factory-a',exact:true})).toHaveCount(2);await expect(table.getByRole('cell',{name:'factory-b',exact:true})).toHaveCount(0);
 await page.route('**/api/site-snapshots?**',async route=>{if(new URL(route.request().url()).searchParams.get('site_id')==='factory-b')await new Promise(r=>setTimeout(r,700));await route.continue();});
 await choose(page,'筛选工厂','factory-b');await expect(table.getByRole('cell',{name:'factory-a',exact:true})).toHaveCount(0);await settled(page);
 await expect(table.getByRole('cell',{name:'factory-b',exact:true})).toHaveCount(2);
 const options=await (await page.request.get(fixture.center_url+'/api/workspace/scope-options')).json();const line=options.lines.find((r:any)=>r.site_id==='factory-b');expect(line).toBeTruthy();
 await choose(page,'筛选产线',line.name);await settled(page);await expect(table.getByRole('cell',{name:'factory-b',exact:true})).toHaveCount(1);
 await page.reload();await settled(page);await expect(table.getByRole('cell',{name:'factory-b',exact:true})).toHaveCount(1);
 await page.goBack();await settled(page);await expect(table.getByRole('cell',{name:'factory-b',exact:true})).toHaveCount(2);
 await page.screenshot({path:screenshots+'/delivery-multifactory.png',fullPage:true,animations:'disabled'});
});
}
