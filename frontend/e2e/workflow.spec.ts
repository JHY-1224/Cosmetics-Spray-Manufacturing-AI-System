import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const screenshots=process.env.QUALITY_E2E_SCREENSHOTS??'../reports';
const passwords=JSON.parse(readFileSync(process.env.QUALITY_E2E_CREDENTIALS??'../var/demo-credentials.json','utf8'));
const name='浏览器验证异常 '+Date.now();
const evidenceName='browser-evidence-'+Date.now()+'.txt';
const localTime=()=>new Date().toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}).replace(' ','T');

test('用不同岗位真实办理异常、审批、执行、复测和结案',async({page})=>{
 async function login(role:string){
  await page.context().clearCookies();await page.goto('/');
  await page.getByLabel('账号',{exact:true}).fill(role);await page.getByLabel('密码',{exact:true}).fill(passwords[role]);
  await page.getByRole('button',{name:/登\s*录/}).click();
  await expect(page.getByRole('heading',{name:'品质总览',exact:true})).toBeVisible();
 }
 async function choose(label:string,text:string){
  await page.getByLabel(label,{exact:true}).click();
  if(label==='现场证据')await page.getByLabel(label,{exact:true}).fill(text);
  await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).click();
  await page.locator('.ant-modal:visible .ant-modal-title').click();
 }
 async function save(){await page.getByRole('button',{name:'确 定',exact:true}).click();await expect(page.locator('.ant-modal:visible')).toHaveCount(0);}
 async function openCase(){await page.getByRole('menuitem',{name:'异常案件'}).click();await page.getByRole('button',{name,exact:true}).click();}
 await login('engineer');
 await page.getByRole('menuitem',{name:'检测与检验'}).click();
 await page.locator('input[type=file]').setInputFiles({name:evidenceName,mimeType:'text/plain',buffer:Buffer.from('Browser measured evidence, software test only')});
 await expect(page.getByText('证据已保存，可在表单中选择')).toBeVisible();
 await page.getByRole('menuitem',{name:'异常案件'}).click();await page.getByRole('button',{name:'新建异常'}).click();
 await page.getByLabel('异常标题').fill(name);await choose('所属产线','塑料瓶喷涂一线');await choose('工艺','塑料瓶喷涂');
 await page.getByLabel('目标缺陷').fill('颗粒');await choose('现场证据',evidenceName);await save();
 await page.getByRole('button',{name,exact:true}).click();await page.getByRole('button',{name:'启动证据诊断'}).click();
 await expect(page.getByText('诊断已记录')).toBeVisible();
 await page.getByRole('button',{name:'提交整改与复测方案'}).click();await page.getByLabel('任务说明',{exact:true}).fill('现场核实清洁与前处理记录并留存测量证据');await page.getByRole('button',{name:/增加整改任务/}).click();await page.getByLabel('任务说明',{exact:true}).nth(1).fill('独立核实材料批次并保留原始单据');
 await page.getByLabel('复测方法').fill('按合成测试标准逐件复核');await page.getByLabel('最低样本量').fill('2');await page.getByLabel('允许不良率（0至1）').fill('0');await save();
 await login('approver');await openCase();await page.getByRole('tab',{name:'方案与审批'}).click();await page.getByRole('button',{name:'审批／驳回'}).click();
 await page.getByLabel('审核依据').fill('核对证据、动作范围和复测计划，批准人工执行');await save();
 await login('executor');await openCase();await page.getByRole('tab',{name:'方案与审批'}).click();await page.getByRole('button',{name:'登记执行'}).click();
 for(let i=0;i<2;i++){await page.getByLabel('实际完成情况',{exact:true}).nth(i).focus();await page.getByLabel('实际完成情况',{exact:true}).nth(i).press('ArrowDown');await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('已完成',{exact:true}).click();await page.locator('.ant-modal:visible .ant-modal-title').click();await expect(page.locator('.ant-select-dropdown:visible')).toHaveCount(0);await page.getByLabel('逐项实际记录',{exact:true}).nth(i).fill('已完成人工核查任务 '+(i+1)+'，仅用于合成软件验收');}await page.getByLabel('实际执行时间').fill(localTime());await page.getByLabel('现场对象标识（逗号分隔）').fill('LOAD-BROWSER');await page.getByLabel('实际动作').fill('现场人工核验前处理并清理，留存记录');
 await choose('现场证据',evidenceName);await save();
 await login('quality');await openCase();await page.getByRole('tab',{name:'方案与审批'}).click();await page.getByRole('button',{name:'登记复测'}).click();
 const stamp=localTime();await page.getByLabel('复测开始时间').fill(stamp);await page.getByLabel('复测结束时间').fill(stamp);await page.getByLabel('实际样本数量').fill('2');await page.getByLabel('不良件数量').fill('0');
 await choose('现场证据',evidenceName);await page.getByText('已核实方案要求的稳定条件').click();await page.getByText('其他质量约束均已满足').click();await page.getByLabel('检验说明').fill('完成两件合成样本的逐件检验');await save();
 await page.getByRole('button',{name:'结案归档'}).click();await page.getByLabel('结案依据').fill('复测数量、稳定条件和质量约束符合审批方案，归档并等待独立放行');await save();
 await expect(page.getByRole('button',{name:'质量放行',exact:true})).toBeVisible();
 await page.getByRole('tab',{name:'证据与记录',exact:true}).click();await expect(page.getByLabel('逐项整改执行结果').getByText('已完成',{exact:true})).toHaveCount(2);await expect(page.getByLabel('案件证据侧栏')).toContainText('根因确认：尚未确认');await expect(page.getByLabel('案件证据侧栏')).toContainText('正式放行：尚未放行');
 await page.screenshot({path:screenshots+'/workbench-closed.png',fullPage:true});
});

test('办理基础数据、工艺审核绑定、知识发布解析、导入和连接验证',async({page})=>{
 const stamp=String(Date.now());
 async function login(role:string){
  await page.context().clearCookies();await page.goto('/');
  const admin=JSON.parse(readFileSync(process.env.QUALITY_E2E_ADMIN??'../var/demo-admin.json','utf8'));
  await page.getByLabel('账号',{exact:true}).fill(role==='admin'?admin.username:role);await page.getByLabel('密码',{exact:true}).fill(role==='admin'?admin.password:passwords[role]);
  await page.getByRole('button',{name:/登\s*录/}).click();await expect(page.getByRole('heading',{name:'品质总览',exact:true})).toBeVisible();
 }
 async function choose(label:string,text:string){await page.getByLabel(label,{exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText(text,{exact:true}).click();await page.locator('.ant-modal:visible .ant-modal-title').click();}
 async function save(){await page.getByRole('button',{name:'确 定',exact:true}).click();await expect(page.locator('.ant-modal:visible')).toHaveCount(0);}
 await login('engineer');await page.getByRole('menuitem',{name:'生产基础数据'}).click();await page.getByRole('button',{name:'新增数据'}).click();
 await choose('数据类型','产品／瓶型');await page.getByLabel('唯一编码').fill('PRODUCT-'+stamp);await page.locator('.ant-modal:visible').getByLabel('名称',{exact:true}).fill('浏览器瓶型 '+stamp);await save();
 await expect(page.getByRole('cell',{name:'浏览器瓶型 '+stamp,exact:true})).toBeVisible();
 await page.getByRole('menuitem',{name:'工艺路线'}).click();
 await page.getByLabel('搜索工艺版本').fill('塑料瓶喷涂参考路线');await page.getByRole('row').filter({hasText:'塑料瓶喷涂参考路线'}).first().getByRole('button',{name:'复制编辑'}).click();
 const routeName='浏览器工艺 '+stamp;await page.getByLabel('新版本名称').fill(routeName);await save();
 await page.getByLabel('搜索工艺版本').fill(routeName);await page.getByRole('row').filter({hasText:routeName}).getByRole('button',{name:'提交审核'}).click();await expect(page.getByText('操作已保存').last()).toBeVisible();
 await login('approver');await page.getByRole('menuitem',{name:'工艺路线'}).click();await page.getByLabel('搜索工艺版本').fill(routeName);await page.getByRole('row').filter({hasText:routeName}).getByRole('button',{name:'审核发布'}).click();await expect(page.getByText('操作已保存').last()).toBeVisible();
 await login('engineer');await page.getByRole('menuitem',{name:'工艺路线'}).click();await page.getByLabel('搜索工艺版本').fill(routeName);await page.getByRole('row').filter({hasText:routeName}).getByRole('button',{name:'绑定产线'}).click();
 await choose('产线','塑料瓶喷涂一线');await save();
 await page.getByRole('menuitem',{name:'知识与规则'}).click();await page.getByRole('button',{name:'新建知识'}).click();
 const term='浏览器缺陷定义 '+stamp;await page.getByLabel('知识标题').fill(term);await choose('知识类别','缺陷术语');await choose('适用工艺','塑料瓶喷涂');await page.getByLabel('来源定位与依据').fill('固定软件验证，非现场知识');await page.getByLabel('正文').fill('合成缺陷术语说明，不作为生产效果依据。');await save();
 await page.getByRole('row').filter({hasText:term}).getByRole('button',{name:'提交审核'}).click();await expect(page.getByText('操作已保存').last()).toBeVisible();
 await login('approver');await page.getByRole('menuitem',{name:'知识与规则'}).click();await page.getByRole('row').filter({hasText:term}).getByRole('button',{name:/发\s*布/}).click();await expect(page.getByText('操作已保存').last()).toBeVisible();
 await login('engineer');await page.getByRole('menuitem',{name:'知识与规则'}).click();
 const docName='knowledge-'+stamp+'.md';await page.locator('input[type=file]').setInputFiles({name:docName,mimeType:'text/markdown',buffer:Buffer.from('软件验证知识文档：保留来源并人工审核。')});
 await page.getByRole('row').filter({hasText:docName}).getByRole('button',{name:'解析并创建草稿'}).click();await expect(page.getByLabel('正文')).toHaveValue('软件验证知识文档：保留来源并人工审核。');await page.getByRole('button',{name:'取 消',exact:true}).click();
 await login('admin');await page.getByRole('menuitem',{name:'接入管理'}).click();await page.getByRole('button',{name:'新增连接'}).click();await page.locator('.ant-modal:visible').getByLabel('连接名称').fill('浏览器断连验证 '+stamp);await page.getByLabel('受控连接地址').fill('http://127.0.0.1:19999/records');await save();
 await page.getByRole('row').filter({hasText:'浏览器断连验证 '+stamp}).getByRole('button',{name:'采集与验证'}).click();await expect(page.getByRole('row').filter({hasText:'浏览器断连验证 '+stamp})).toContainText('暂不可用');
 await page.getByRole('menuitem',{name:'运行与审计'}).click();await page.locator('.ant-select[aria-label="导入类型"]').click();await page.locator('.ant-select-dropdown:visible').getByText('生产台账',{exact:true}).click();
 await page.locator('input[type=file]').setInputFiles({name:'ledger.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify([{kind:'material_batch',code:'IMPORT-'+stamp,name:'浏览器导入批次 '+stamp}]))});
 await page.getByRole('button',{name:'确认提交'}).click();await expect(page.getByText('导入已提交并记录审计')).toBeVisible();
 await page.getByRole('menuitem',{name:'生产基础数据'}).click();await expect(page.getByRole('cell',{name:'浏览器导入批次 '+stamp,exact:true})).toBeVisible();
 await page.screenshot({path:screenshots+'/workbench-master-data.png',fullPage:true});
});
