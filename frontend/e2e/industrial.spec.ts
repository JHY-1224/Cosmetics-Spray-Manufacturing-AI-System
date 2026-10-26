import {test,expect,Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync(process.env.INDUSTRIAL_BROWSER_FIXTURE!,'utf8'));
const admin=JSON.parse(readFileSync(process.env.QUALITY_E2E_ADMIN!,'utf8'));
const screenshots=process.env.QUALITY_E2E_SCREENSHOTS!;
async function login(page:Page){
 await page.goto('/');await page.getByLabel('账号',{exact:true}).fill(admin.username);await page.getByLabel('密码',{exact:true}).fill(admin.password);
 await page.getByRole('button',{name:/登\s*录/}).click();await expect(page.getByRole('heading',{name:'品质总览',exact:true})).toBeVisible();
}
async function choose(page:Page,label:string,text:string){
 const control=page.getByLabel(label,{exact:true});await control.focus();await control.press('ArrowDown');if(await control.isEditable())await control.fill(text);
 await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:text}).first().click();
 await page.locator('.ant-modal:visible .ant-modal-title').click();await expect(page.locator('.ant-select-dropdown:visible')).toHaveCount(0);
}
async function closeDrawer(page:Page){await page.locator('.ant-drawer-open').getByRole('button',{name:'关闭',exact:true}).click();await expect(page.locator('.ant-drawer-open')).toHaveCount(0);}

test('通过页面登记新源库存，旧需求计划显示失效并重新展开',async({page})=>{
 await login(page);await page.goto('/?view=supply');await page.getByRole('tab',{name:'需求、缺口与交期'}).click();
 await page.getByRole('button',{name:'查看与办理',exact:true}).click();
 await expect(page.getByText('计划依据当前有效',{exact:true})).toBeVisible();await closeDrawer(page);
 await page.goto('/?view=mixing');await page.getByRole('button',{name:'登记源库存'}).click();
 await choose(page,'实际产线',fixture.line);
 await choose(page,'材料',fixture.material);
 for(const [label,value] of Object.entries({'仓库':fixture.stock.warehouse,'物料批次':fixture.stock.lot,'账面在库':'150','数据权威系统':fixture.stock.source_system,'来源版本':'browser-source-v2'}))await page.getByLabel(label,{exact:true}).fill(String(value));
 await choose(page,'原件证据',fixture.file_name);
 await page.getByRole('button',{name:'核对并保存',exact:true}).click();await expect(page.locator('.ant-modal:visible')).toHaveCount(0);
 await page.goto('/?view=supply');await page.getByRole('tab',{name:'需求、缺口与交期'}).click();await page.getByRole('button',{name:'查看与办理',exact:true}).click();
 await expect(page.getByText('计划依据已变化，办理前需重新核算',{exact:true})).toBeVisible();
 await page.screenshot({path:screenshots+'/stale-plan.png',fullPage:true,animations:'disabled'});await closeDrawer(page);
 await page.getByRole('button',{name:'展开需求',exact:true}).click();await choose(page,'当前订单',fixture.order);
 await choose(page,'核对的源库存版本','browser-source-v2');await page.getByRole('button',{name:'核对并保存',exact:true}).click();
 await expect(page.locator('.ant-modal:visible')).toHaveCount(0);
 await page.getByRole('button',{name:'查看与办理',exact:true}).first().click();
 await expect(page.getByText('计划依据当前有效',{exact:true})).toBeVisible();
 const demands=page.locator('.ant-drawer:visible .ant-table').first();
 const rows=demands.locator('tbody tr.ant-table-row');await expect(rows).toHaveCount(2);
 for(const row of await rows.all())await expect(row.getByRole('cell').nth(4)).toHaveText(/^0(?:\.0+|E-\d+)?$/);
 await page.screenshot({path:screenshots+'/recalculated-plan.png',fullPage:true,animations:'disabled'});
});

test('通过页面归集返工成本并追溯原收退料和发票行',async({page})=>{
 await login(page);await page.goto('/?view=cost');await page.getByRole('button',{name:'计算与核对成本'}).click();
 await choose(page,'订单',fixture.order);
 await page.getByLabel('归集与分摊口径版本',{exact:true}).fill('browser-rework-trace-v1');
 await choose(page,'原件证据',fixture.file_name);
 await page.getByRole('button',{name:'核对并保存',exact:true}).click();await expect(page.locator('.ant-modal:visible')).toHaveCount(0);
 await page.getByRole('tab',{name:'成本与差异核对'}).click();await page.getByRole('button',{name:'查看与办理',exact:true}).click();
 await expect(page.getByText('原单据行匹配依据',{exact:true})).toBeVisible();
 await expect(page.getByText('实际用量计价追溯',{exact:true})).toBeVisible();
 await expect(page.locator('.ant-drawer:visible').getByRole('cell',{name:'10.000000000',exact:true}).first()).toBeVisible();
 await expect(page.locator('.ant-drawer:visible').getByText('数量与金额一致',{exact:true})).toBeVisible();
 await expect(page.locator('.ant-drawer:visible').getByText(fixture.receipt_line,{exact:false}).first()).toBeVisible();
 await expect(page.locator('.ant-drawer:visible').getByText(fixture.invoice_line,{exact:false}).first()).toBeVisible();
 await expect(page.locator('.ant-drawer:visible').getByText(fixture.usage_id,{exact:true})).toBeVisible();
 await page.screenshot({path:screenshots+'/cost-source-trace.png',fullPage:true,animations:'disabled'});
});
