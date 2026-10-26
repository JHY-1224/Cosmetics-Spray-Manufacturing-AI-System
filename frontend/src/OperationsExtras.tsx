import {DataTable} from './DataTable';
import {useEffect,useState} from 'react';
import {Alert,App,Button,Card,Checkbox,Descriptions,Form,InputNumber,Modal,Select,Space,Tag} from 'antd';
import {api} from './api';
import {useEditingGuard} from './WorkspaceContext';
type R=Record<string,any>;
export function OperationsExtras({masters,permissions,onComplete,refresh,query=''}:{masters:R[],permissions:string[],onComplete:()=>void,refresh:number,query?:string}){
 const {message}=App.useApp();const [report,setReport]=useState<R|null>(null),[error,setError]=useState(''),[open,setOpen]=useState(false),[busy,setBusy]=useState(false);
 const [industrial,setIndustrial]=useState<R|null>(null);
 const [form]=Form.useForm();const lines=masters.filter(r=>r.kind==='line');
 useEditingGuard(open);
 useEffect(()=>{const c=new AbortController();api('/operations/alerts?'+query,undefined,undefined,{signal:c.signal}).then(setReport).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();},[refresh,query]);
 useEffect(()=>{setIndustrial(null);if(!permissions.includes('admin'))return;const c=new AbortController();api('/industrial/status?'+query,undefined,undefined,{signal:c.signal}).then(r=>{if(!c.signal.aborted)setIndustrial(r.runtime_metrics);}).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();},[refresh,query,permissions.join(',')]);
 async function save(){const v=await form.validateFields();const line=lines.find(l=>l.id===v.line_id)!;setBusy(true);try{
 const {line_id,...policy}=v;await api('/lines/'+line_id+'/anomaly-policy',{version:line.version,policy});setOpen(false);message.success('异常窗口策略已保存');onComplete();
 }catch(e:any){message.error(e.message);}finally{setBusy(false);}}
 return <div className="section-bottom">{error&&<Alert type="error" message={error}/>}<Space><h3>运行告警与异常策略</h3>{permissions.includes('master')&&<Button onClick={()=>{form.resetFields();setOpen(true);}}>配置连续窗口触发</Button>}</Space>
 {industrial&&<Card title="数字任务与外部命令运行账本" size="small" className="section-bottom">{industrial.availability==='measured'?<><Descriptions size="small" column={2} items={[{key:'queued',label:'数字任务排队',children:industrial.digital_tasks.by_status.queued??0},{key:'running',label:'数字任务执行中',children:industrial.digital_tasks.by_status.running??0},{key:'tasklease',label:'数字任务过期租约',children:industrial.digital_tasks.expired_leases},{key:'pending',label:'待源系统确认',children:industrial.business_commands.awaiting_source_confirmation},{key:'commandlease',label:'业务命令过期租约',children:industrial.business_commands.expired_leases},{key:'oldest',label:'最早未完成命令已等待',children:industrial.business_commands.oldest_pending_age_seconds===null?'无待处理命令':industrial.business_commands.oldest_pending_age_seconds+' 秒'}]}/><p className="muted">当前筛选范围内的实际账本；数字任务成功不表示外部系统已应用。未知、已受理或部分结果须按原业务键核对。</p>{industrial.business_commands.awaiting_source_confirmation>0&&<Alert type="warning" showIcon message="存在尚未确认的外部业务结果，请到数字任务工作区核对原命令"/>}</>:<Alert type="info" message={industrial.reason}/>}</Card>}
 <DataTable<any> tableId="operationsextras-1" size="small" rowKey={(r:R)=>r.code+r.target_id} dataSource={report?.alerts??[]} locale={{emptyText:'当前规则未发现告警'}} columns={[{title:'级别',dataIndex:'severity',render:v=><Tag color={v==='error'?'red':'orange'}>{v==='error'?'异常':'预警'}</Tag>},{title:'事项',dataIndex:'message'},{title:'对象',dataIndex:'target_id'}]}/>
 <p className="muted">{report?.notification_channel}；保留 {report?.retention?.days??'—'} 天后可通过运维归档命令生成校验副本，原始数据保留。</p>
 <Modal title="产线连续窗口异常策略" open={open} confirmLoading={busy} onOk={save} onCancel={()=>setOpen(false)}>
 <Form form={form} layout="vertical" initialValues={{enabled:true,min_rate:0.1,min_samples:10,min_ng_records:2,window_seconds:900,dedup_seconds:3600,max_lateness_seconds:86400,same_batch:true}}>
 <Form.Item name="line_id" label="策略产线" rules={[{required:true}]}><Select options={lines.map(l=>({value:l.id,label:l.name}))} onChange={id=>{const p=lines.find(l=>l.id===id)?.data?.anomaly_threshold;if(p)form.setFieldsValue(p);}}/></Form.Item>
 <Form.Item name="enabled" valuePropName="checked"><Checkbox>启用自动异常触发</Checkbox></Form.Item>
 <Form.Item name="min_rate" label="最低不良率（0至1）" rules={[{required:true}]}><InputNumber min={0} max={1} step={0.01}/></Form.Item>
 <Form.Item name="min_samples" label="窗口最低检测件数" rules={[{required:true}]}><InputNumber min={1}/></Form.Item>
 <Form.Item name="min_ng_records" label="窗口最低 NG 记录数" rules={[{required:true}]}><InputNumber min={1}/></Form.Item>
 <Form.Item name="window_seconds" label="连续统计窗口（秒；0为单条记录）" rules={[{required:true}]}><InputNumber min={0} max={86400}/></Form.Item>
 <Form.Item name="dedup_seconds" label="同类异常合并周期（秒）" rules={[{required:true}]}><InputNumber min={1} max={86400}/></Form.Item>
 <Form.Item name="max_lateness_seconds" label="自动触发允许迟到（秒）" rules={[{required:true}]}><InputNumber min={1} max={604800}/></Form.Item>
 <Form.Item name="same_batch" valuePropName="checked"><Checkbox>仅合并同一生产批次</Checkbox></Form.Item>
 <Alert message="只合并同产线、同粒度、同来源属性的有效记录；更正历史不会重复计数。建议先用参考数据核验阈值再启用。"/>
 </Form></Modal></div>;
}
