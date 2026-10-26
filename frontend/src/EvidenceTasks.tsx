import {DataTable} from './DataTable';
import {useState} from 'react';
import {App,Button,Input,Modal,Select,Space,Tag} from 'antd';
import {api} from './api';
type R=Record<string,any>;
export function EvidenceTasks({detail,files,canEdit,onComplete}:{detail:R,files:R[],canEdit:boolean,onComplete:()=>void}){
 const {message}=App.useApp();const [task,setTask]=useState<R|null>(null),[reason,setReason]=useState(''),[ids,setIds]=useState<string[]>([]),[busy,setBusy]=useState(false);
 const allowed=new Set([...(detail.context.evidence_ids??[]),...(detail.context.inspection_snapshots??[]).flatMap((i:R)=>i.result?.image_ids??[])]);
 async function decide(action:string){if(reason.trim().length<4){message.error('请填写至少4字的办理说明');return;}if(action==='complete'&&!ids.length){message.error('请选择已通过案件补证流程关联的证据');return;}setBusy(true);try{
 await api('/cases/'+detail.id+'/evidence-tasks/'+task!.id,{version:detail.version,action,reason,evidence_ids:ids});setTask(null);message.success('补证任务办理已记录');onComplete();
 }catch(e:any){message.error(e.message);}finally{setBusy(false);}}
 return <><h4>待补证任务</h4><DataTable<R> tableId="evidencetasks-1" size="small" rowKey="id" dataSource={detail.diagnosis.evidence_tasks??[]} columns={[{title:'核验事项',dataIndex:'description'},{title:'状态',dataIndex:'status',render:v=><Tag>{({pending:'待办理',completed:'已补证',cancelled:'已取消'} as R)[v]??v}</Tag>},{title:'操作',render:(_,r)=>canEdit&&r.status==='pending'&&!['closed','cancelled'].includes(detail.status)&&<Button onClick={()=>{setTask(r);setReason('');setIds([]);}}>办理补证</Button>}]} locale={{emptyText:'本次调查暂无补证任务'}}/>
 <Modal title="办理诊断补证任务" open={!!task} onCancel={()=>setTask(null)} footer={<Space><Button disabled={busy} onClick={()=>decide('cancel')}>说明后取消任务</Button><Button type="primary" loading={busy} onClick={()=>decide('complete')}>证据核实完成</Button></Space>}>
 <p>{task?.description}</p><p>新文件须先通过案件“补充证据”关联，原审批按业务规则失效。完成任务不会自动确认根因或审批方案。</p>
 <Select aria-label="任务关联证据" mode="multiple" showSearch optionFilterProp="label" style={{width:'100%'}} value={ids} onChange={setIds} options={files.filter(f=>allowed.has(f.id)).map(f=>({value:f.id,label:f.filename}))}/>
 <Input.TextArea aria-label="补证办理说明" style={{marginTop:16}} value={reason} onChange={e=>setReason(e.target.value)} placeholder="记录核验方式、结果及局限"/></Modal></>;
}
