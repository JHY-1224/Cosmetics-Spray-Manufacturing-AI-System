import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
const screenshots=process.env.QUALITY_E2E_SCREENSHOTS??'../reports';
const passwords=JSON.parse(readFileSync(process.env.QUALITY_E2E_CREDENTIALS??'../var/demo-credentials.json','utf8'));
const name='模型建议浏览器闭环 '+Date.now();
const evidenceName='browser-evidence-'+Date.now()+'.txt';
const localTime=()=>new Date().toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}).replace(' ','T');

test('真实模型建议带入草稿、独立审批、逐项执行和复测结案',async({page})=>{
 test.setTimeout(180000);
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
 await expect(page.getByText('模型结果已校验，待人工核对')).toBeVisible({timeout:100000});
 await page.getByRole('button',{name:'方案起草依据',exact:true}).click();
 await expect(page.getByText('含已校验模型建议',{exact:true})).toBeVisible();
 await expect(page.getByText('实际 Token',{exact:true})).toBeVisible();
 await page.screenshot({path:screenshots+'/model-assistance.png',fullPage:true,animations:'disabled'});
 await page.getByRole('button',{name:'带入整改草稿并人工核对'}).click();
 await expect(page.getByLabel('任务说明',{exact:true}).first()).toBeVisible();
 const actionCount=await page.getByLabel('任务说明',{exact:true}).count();
 expect(actionCount).toBeGreaterThan(0);
 await expect(page.getByLabel('任务说明',{exact:true}).first()).not.toHaveValue('');
 await expect(page.getByLabel('仅保存草稿，稍后独立提交审批')).toBeChecked();
 await expect(page.getByLabel('最低样本量')).toBeEmpty();
 await page.getByLabel('复测方法').fill('按合成测试标准逐件复核');await page.getByLabel('最低样本量').fill('2');await page.getByLabel('允许不良率（0至1）').fill('0');await page.getByLabel('执行后稳定时间（分钟）').fill('0');await page.getByLabel('观察窗口（分钟）').fill('0');await save();
 await page.getByRole('tab',{name:'方案与审批'}).click();
 await page.getByRole('button',{name:'提交审批',exact:true}).click();
 await expect(page.getByText('操作已保存').last()).toBeVisible();
 await login('approver');await openCase();await page.getByRole('tab',{name:'方案与审批'}).click();await page.getByRole('button',{name:'审批／驳回'}).click();
 await page.getByLabel('审核依据').fill('核对证据、动作范围和复测计划，批准人工执行');await save();
 await login('executor');await openCase();await page.getByRole('tab',{name:'方案与审批'}).click();await page.getByRole('button',{name:'登记执行'}).click();
 for(let i=0;i<actionCount;i++){await page.getByLabel('实际完成情况',{exact:true}).nth(i).focus();await page.getByLabel('实际完成情况',{exact:true}).nth(i).press('ArrowDown');await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('已完成',{exact:true}).click();await page.locator('.ant-modal:visible .ant-modal-title').click();await expect(page.locator('.ant-select-dropdown:visible')).toHaveCount(0);await page.getByLabel('逐项实际记录',{exact:true}).nth(i).fill('已完成人工核查任务 '+(i+1)+'，仅用于合成软件验收');}await page.getByLabel('实际执行时间').fill(localTime());await page.getByLabel('现场对象标识（逗号分隔）').fill('LOAD-BROWSER');await page.getByLabel('实际动作').fill('现场人工核验前处理并清理，留存记录');
 await choose('现场证据',evidenceName);await save();
 await login('quality');await openCase();await page.getByRole('tab',{name:'方案与审批'}).click();await page.getByRole('button',{name:'登记复测'}).click();
 const stamp=localTime();await page.getByLabel('复测开始时间').fill(stamp);await page.getByLabel('复测结束时间').fill(stamp);await page.getByLabel('实际样本数量').fill('2');await page.getByLabel('不良件数量').fill('0');
 await choose('现场证据',evidenceName);await page.getByText('已核实方案要求的稳定条件').click();await page.getByText('其他质量约束均已满足').click();await page.getByLabel('检验说明').fill('完成两件合成样本的逐件检验');await save();
 await page.getByRole('button',{name:'结案归档'}).click();await page.getByLabel('结案依据').fill('复测数量、稳定条件和质量约束符合审批方案，归档并等待独立放行');await save();
 await expect(page.getByRole('button',{name:'质量放行',exact:true})).toBeVisible();
 await page.getByRole('tab',{name:'证据与记录',exact:true}).click();await expect(page.getByLabel('逐项整改执行结果').getByText('已完成',{exact:true})).toHaveCount(actionCount);await expect(page.getByLabel('案件证据侧栏')).toContainText('根因确认：尚未确认');await expect(page.getByLabel('案件证据侧栏')).toContainText('正式放行：尚未放行');
 await page.screenshot({path:screenshots+'/model-closed.png',fullPage:true});
});

