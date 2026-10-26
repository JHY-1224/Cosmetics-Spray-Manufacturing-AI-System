import {useEffect,useRef,useState} from 'react';
import {Alert,App,Button,Card,Collapse,Descriptions,List,Space,Tag} from 'antd';
import {api} from './api';

const roles=[['A1','批准知识'],['A2','证据包'],['A3','五维核查'],['A4','方案起草依据'],['A5','效果核验'],['A6','质量统计']];
const modelStates:Record<string,string>={validated:'模型结果已校验，待人工核对',disabled:'模型未启用',not_run:'尚未调用模型',unavailable:'模型不可用，保留规则与人工流程',unknown:'调用结果未知，不自动重发',budget_exhausted:'模型预算不足，转人工处理',stale:'模型依据已变化，请重新核对'};

export function AssistantTools({detail,canPlan,onDraft}:{detail:any;canPlan:boolean;onDraft:(data:any)=>void}){
 const {message}=App.useApp();
 const [report,setReport]=useState<any>(null),[busy,setBusy]=useState('');
 const request=useRef<AbortController|null>(null);
 useEffect(()=>()=>request.current?.abort(),[]);
 async function run(role:string){
  request.current?.abort();const controller=new AbortController();request.current=controller;setBusy(role);
  try{const result=await api('/assistants/'+role,{case_id:detail.id,version:detail.version},undefined,{signal:controller.signal});if(!controller.signal.aborted)setReport(result);}
  catch(e:any){if(!controller.signal.aborted)message.error(e.message);}
  finally{if(!controller.signal.aborted)setBusy('');}
 }
 const latest=detail.runs?.find((r:any)=>r.id===detail.diagnosis.run_id)?.data?.model_record;
 const model=report?.data?.model;
 const status=model?.status??detail.diagnosis.model_status??'not_run';
 const record=model??latest;
 const canDraft=canPlan&&report?.role==='A4'&&report?.data?.actions?.length>0&&['accepted','diagnosed','needs_evidence','plan_draft','awaiting_approval','rejected','approved','retest_failed','manual_review','partially_executed','execution_failed'].includes(detail.status);
 return <Card title="受控辅助工具" size="small" className="section">
  <Alert showIcon type={['validated','disabled','not_run'].includes(status)?'info':'warning'} message={modelStates[status]??status} description={record?.model?`模型 ${record.model}；仅消费获准输入，候选结论仍需证据和人工核对。`:undefined}/>
  {record?.model&&<Descriptions size="small" column={2} className="section" items={[
   {key:'version',label:'提示词版本',children:record.prompt_version??'历史记录未提供'},
   {key:'duration',label:'调用耗时',children:record.duration_ms!=null?`${(record.duration_ms/1000).toFixed(2)} 秒`:'未记录'},
   {key:'tokens',label:'实际 Token',children:record.usage?.total_tokens??'未返回，不能视为零'},
   {key:'reserved',label:'预留 Token',children:record.reserved_tokens??'未记录'},
   {key:'cost',label:'估算费用',children:record.pricing_configured&&record.estimated_cost!=null?String(record.estimated_cost):'未配置真实计价；以提供商账单为准'},
   {key:'http',label:'HTTP 状态',children:record.response_received?String(record.http_status):record.http_attempted?'已发起，未收到响应':'本次未记录响应'},
  ]}/>}
  <Space wrap className="section">{roles.filter(([r])=>r!=='A4'||canPlan).map(([r,name])=><Button key={r} loading={busy===r} disabled={!!busy&&busy!==r} onClick={()=>run(r)}>{name}</Button>)}</Space>
  {report&&<div className="section">
   <Space><Tag color="blue">受控工具读取</Tag><Tag>版本 {detail.version}</Tag><Tag>{({success:'有可用证据',partial:'证据部分可用',no_data:'暂无适用数据',stale:'版本已过期'} as any)[report.status]??report.status}</Tag></Space>
   <List size="small" dataSource={report.limitations} renderItem={(v:any)=><List.Item>{v}</List.Item>}/>
   {model?.summary&&<Alert type="info" message="模型分析摘要（辅助草稿）" description={model.summary}/>}
   {report.role==='A4'&&<><Tag color={model?.draft_available?'purple':'blue'}>{model?.draft_available?'含已校验模型建议':'证据核查建议'}</Tag><List aria-label="待核对的整改建议" dataSource={report.data.actions} renderItem={(action:any)=><List.Item>{action.description}</List.Item>}/>
    {canDraft&&<Button type="primary" onClick={()=>onDraft(report.data)}>带入整改草稿并人工核对</Button>}</>}
   <Alert className="section" type="info" message="带入只填充本案编辑表单；补全复测要求后另行保存，并由另一位审核人审批。"/>
   <Space wrap>{report.references.map((r:any)=><a key={r.kind+':'+r.id} href={'/api/evidence-records/'+r.kind+'/'+encodeURIComponent(r.id)} target="_blank" rel="noreferrer">{r.kind} · {r.id.slice(0,8)}{r.version?' v'+r.version:''}</a>)}</Space>
   <Collapse items={[{key:'result',label:'展开证据、版本和结构化结果',children:<pre>{JSON.stringify({references:report.references,data:report.data},null,2)}</pre>}]}/>
  </div>}
 </Card>;
}
