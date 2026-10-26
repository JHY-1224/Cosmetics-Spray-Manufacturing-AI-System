import {DataTable,RecordSelect} from './DataTable';
import {useState} from 'react';
import {Alert,App,Button,Descriptions,Form,Input,Modal,Select,Space,Tag,Typography,Upload} from 'antd';
import {api} from './api';
import {useEditingGuard} from './WorkspaceContext';

type R=Record<string,any>;
const names:R={queued:'等待同步',received:'已接收待确认',applied:'已应用',rejected:'已拒绝',expired:'已过期',conflict:'内容冲突'};
const actions:R={publish:'发布',retire:'停用',rollback:'回退'};
export function DistributionPanel({role,permissions,knowledge,routes,sites,refresh,query=''}:{role:string,permissions:string[],knowledge:R[],routes:R[],sites:R[],refresh:number,query?:string}){
 const {message}=App.useApp();
 const [tableRefresh,setTableRefresh]=useState(0),[error,setError]=useState(''),[busy,setBusy]=useState(false),[edit,setEdit]=useState(false);
 const [packageText,setPackageText]=useState(''),[importing,setImporting]=useState(false),[preview,setPreview]=useState<R|null>(null),[active,setActive]=useState<R|null>(null),[decision,setDecision]=useState(''),[reason,setReason]=useState('');
 const [form]=Form.useForm();
 const can=permissions.includes('knowledge_review');
 const configKind=Form.useWatch('config_kind',form)??'knowledge';
 const resources:R={knowledge:'knowledge',route:'routes',definition:'industrial-definitions',formulation:'industrial-formulations',policy:'industrial-policies',bom:'industrial-boms'};
 const records=([...knowledge.map(r=>({...r,kind:'knowledge'})),...routes.map(r=>({...r,kind:'route',title:r.name}))] as R[]).filter(r=>r.reviewer_id&&['published','retired','rolled_back'].includes(r.status));
 async function load(){setError('');setTableRefresh(n=>n+1);}
 useEditingGuard(edit||importing||!!decision);
 async function run(fn:()=>Promise<any>){setBusy(true);try{const result=await fn();await load();return result;}catch(e:any){message.error(e.message);return null;}finally{setBusy(false);}}
 const download=(row:R)=>{const url=URL.createObjectURL(new Blob([JSON.stringify(row.package,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='distribution-'+row.id+'.json';a.click();URL.revokeObjectURL(url);};
 async function inspect(row:R){setActive(row);setPreview(null);if(role==='center'){setPreview({delivery:row.package.body.delivery,changes:row.package.body.record,center:true});return;}const result=await run(()=>api('/distributions/'+row.id+'/preview',{}));if(result)setPreview(result);}
 return <><Alert type="info" showIcon message={role==='center'?'中心分发已审核版本，站点接收后须由授权人员确认；状态以站点回执为准。':'本站保留签名、差异与操作记录；接收后不会自动生效，应用也不会自动绑定产线或控制设备。'}/>
 {error&&<Alert type="error" message={error}/>}<Space style={{margin:'16px 0'}}>{role==='center'&&can&&<Button type="primary" onClick={()=>{form.resetFields();setEdit(true);}}>新建批量分发</Button>}{role==='site'&&can&&<Button onClick={()=>{setPackageText('');setPreview(null);setImporting(true);}}>导入签名分发包</Button>}<Button onClick={load}>刷新分发状态</Button></Space>
 <DataTable<any> tableId="distributionpanel-1" resource="distributions" remoteQuery={query} refreshToken={refresh+tableRefresh} rowKey="id"  scroll={{x:1000}} columns={[
 {title:'版本',render:(_,r:R)=><>{r.package.body.record.title??r.package.body.record.name}<br/><small>{r.kind} · v{r.package.body.record.revision} · 分发序列 {r.generation}</small></>},
 {title:'目标站点',dataIndex:'target_node'},{title:'操作',dataIndex:'action',render:v=>actions[v]},
 {title:'状态',dataIndex:'status',render:v=><Tag color={v==='applied'?'green':v==='received'?'blue':'orange'}>{names[v]??v}</Tag>},
 {title:'有效期',dataIndex:'expires_at',render:v=>new Date(v).toLocaleString('zh-CN')},
 {title:'回执',render:(_,r:R)=>r.result.reason??r.result.status??'尚未收到站点回执'},
 {title:'办理',render:(_,r:R)=><Space wrap><Button onClick={()=>inspect(r)}>查看与预览</Button><Button onClick={()=>download(r)}>导出包</Button>{role==='center'&&can&&['rejected','expired','conflict'].includes(r.status)&&<Button onClick={()=>{form.setFieldsValue({config_kind:r.kind,record:r.record_id,target_nodes:[r.target_node],action:r.action,reason:'重新分发：'+(r.package.body.delivery.reason??'人工复核后重发')});setEdit(true);}}>重新分发</Button>}</Space>}
 ]}/>
 <Modal open={edit} title="创建中心分发任务" confirmLoading={busy} onCancel={()=>setEdit(false)} onOk={async()=>{const v=await form.validateFields();const kind=v.config_kind??'knowledge',record_id=v.record;const result=await run(()=>api('/distributions',{kind,record_id,target_nodes:v.target_nodes,action:v.action,expires_in_hours:24,reason:v.reason,expected_hash:v.expected_hash??''}));if(result){setEdit(false);message.success('已排队，等待真实站点接收与办理');}}}>
 <Form form={form} layout="vertical" initialValues={{action:'publish',config_kind:'knowledge'}}>
 <Form.Item name="config_kind" label="配置类型" rules={[{required:true}]}><Select options={Object.entries({knowledge:"知识与规则",route:"工艺路线",definition:"方法、标准、检验计划与资格",formulation:"材料混配配方",policy:"预授权策略",bom:"版本BOM"}).map(([value,label])=>({value,label}))} onChange={()=>form.setFieldValue("record",undefined)}/></Form.Item><Form.Item name="record" label="已审核配置版本" rules={[{required:true}]}><RecordSelect key={configKind} resource={resources[configKind]} labelField={configKind==="knowledge"?"title":"name"}/></Form.Item>
 <Form.Item name="target_nodes" label="目标节点" rules={[{required:true}]}><Select mode="multiple" options={sites.filter(s=>s.active).map(s=>({value:s.node_id,label:s.name+' ('+s.node_id+')'}))}/></Form.Item>
 <Form.Item name="action" label="分发操作" rules={[{required:true}]}><Select options={Object.entries(actions).map(([value,label])=>({value,label}))}/></Form.Item>
 <Form.Item name="expected_hash" label="预期本站内容哈希（可选）"><Input/></Form.Item>
 <Form.Item name="reason" label="变更及回退依据" rules={[{required:true,min:4}]}><Input.TextArea/></Form.Item>
 <Alert message="有效期24小时；不一致时拒绝覆盖。停用会影响后续SOP有效性；回退旧版本也需本站确认并单独绑定。"/>
 </Form></Modal>
 <Modal open={importing} title="导入中心签名分发包" width={800} onCancel={()=>setImporting(false)} footer={<Space><Button onClick={()=>setImporting(false)}>关闭</Button><Button loading={busy} onClick={async()=>{try{const result=await run(()=>api('/distributions/import/preview',JSON.parse(packageText)));if(result)setPreview(result);}catch{message.error('JSON 格式无效');}}}>校验并预览</Button><Button type="primary" loading={busy} disabled={!preview?.verified} onClick={async()=>{const result=await run(()=>api('/distributions/import',JSON.parse(packageText)));if(result){setImporting(false);setPreview(null);message.success('已导入待确认，配置尚未生效');}}}>接收待确认</Button></Space>}>
 <Upload accept=".json" showUploadList={false} beforeUpload={async file=>{setPackageText(await file.text());setPreview(null);return false;}}><Button>读取 JSON 分发包</Button></Upload>
 <Input.TextArea aria-label="签名分发包 JSON" rows={10} value={packageText} onChange={e=>{setPackageText(e.target.value);setPreview(null);}}/>
 {preview?.verified&&<><Alert type={preview.conflict?'error':'success'} message={preview.conflict?'签名有效，但与本地内容冲突，不能应用':'来源、完整性、目标范围与有效期校验通过'}/><Typography.Paragraph><pre>{JSON.stringify(preview.diff,null,2)}</pre></Typography.Paragraph></>}
 </Modal>
 <Modal open={!!active} title="分发记录与差异预览" width={900} onCancel={()=>{setActive(null);setPreview(null);}} footer={<Space><Button onClick={()=>{setActive(null);setPreview(null);}}>关闭</Button>{role==='site'&&can&&active?.status==='received'&&<><Button onClick={()=>{setReason('');setDecision('reject');}}>拒绝此分发</Button><Button type="primary" disabled={!preview?.verified||preview.conflict||!!preview.dependency_errors?.length} onClick={()=>{setReason('');setDecision('apply');}}>确认本站应用</Button></>}</Space>}>
 {active&&<Descriptions column={1} items={[{key:'hash',label:'签名包哈希',children:active.package.sha256},{key:'action',label:'操作',children:actions[active.action]},{key:'status',label:'状态',children:names[active.status]},{key:'result',label:'本站回执',children:JSON.stringify(active.result)}]}/>}
 {preview?.dependency_errors?.length>0&&<Alert type="warning" message="依赖缺失或版本不一致；原件、材料、路线等依赖核对完成前禁止应用。"/>}{preview?.conflict&&<Alert type="error" message="本地内容发生冲突，系统拒绝覆盖，请人工核对。"/>}
 <pre style={{maxHeight:400,overflow:'auto'}}>{JSON.stringify(preview??{message:'预览未通过，请核对上方错误及有效期'},null,2)}</pre>
 </Modal>
 <Modal title={decision==='apply'?'确认应用配置':'拒绝配置分发'} open={!!decision} confirmLoading={busy} onCancel={()=>setDecision('')} onOk={async()=>{if(reason.trim().length<4){message.error('请填写至少4字的办理依据');return;}const result=await run(()=>api('/distributions/'+active!.id+'/decide',{decision,reason}));if(result){setActive(result);setDecision('');message.success('本站办理已保存，联网同步后中心收到回执');}}}>
 <Input.TextArea aria-label="本站办理依据" value={reason} onChange={e=>setReason(e.target.value)}/></Modal>
 </>;
}
