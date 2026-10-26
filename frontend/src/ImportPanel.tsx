import {DataTable} from './DataTable';
import {useState} from 'react';
import {Alert,App,Button,Modal,Select,Space,Upload} from 'antd';
import {api} from './api';
export function ImportPanel({permissions,onComplete}:{permissions:string[],onComplete:()=>void}){
 const {message}=App.useApp();
 const choices=[...(permissions.includes('inspect')?[{value:'inspections',label:'检测结果'},{value:'lab-results',label:'仪器／人工检验'}]:[]),...(permissions.includes('master')?[{value:'masters',label:'生产台账'}]:[]),...(permissions.includes('execute')?[{value:'receipts',label:'执行回执'}]:[])];
 const [kind,setKind]=useState(choices[0]?.value??'inspections'),[preview,setPreview]=useState<any>(null),[busy,setBusy]=useState(false);
 if(!choices.length)return null;
 const prefix=kind==='inspections'?'/import':'/import/structured/'+kind;
 return <><Space wrap><Select aria-label="导入类型" value={kind} options={choices} onChange={setKind} style={{width:150}}/><Upload accept=".json,.csv" showUploadList={false} customRequest={async o=>{const form=new FormData();form.append('file',o.file as File);try{const r=await api(prefix+'/preview',form);setPreview(r);o.onSuccess?.(r);}catch(e:any){message.error(e.message);o.onError?.(e);}}}><Button>选择 CSV／JSON 预检</Button></Upload></Space><Modal title="导入预检与确认" open={!!preview} onCancel={()=>setPreview(null)} width={750} okText="确认提交" okButtonProps={{disabled:!preview?.commit_allowed}} confirmLoading={busy} onOk={async()=>{setBusy(true);try{await api(prefix+'/commit',preview.valid);message.success('导入已提交并记录审计');setPreview(null);onComplete();}catch(e:any){message.error(e.message);}finally{setBusy(false);}}}><Alert showIcon type={preview?.commit_allowed?'info':'error'} message={`共 ${preview?.count??0} 行，字段预检通过 ${preview?.valid?.length??0} 行。提交时仍校验权限、引用、版本和业务状态；任一失败整批回滚。`}/><DataTable<any> tableId="importpanel-1" rowKey={r=>String(r.row)} dataSource={preview?.errors??[]} pagination={false} columns={[{title:'错误行',dataIndex:'row'},{title:'说明',render:(_,r)=>r.message??r.error_type}]}/></Modal></>;
}
