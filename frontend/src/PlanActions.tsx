import {useEffect,useState} from 'react';
import {Alert,Button,Card,Form,Input,InputNumber,List,Select,Space,Tag} from 'antd';
import {api} from './api';
import {RecordSelect} from './DataTable';

function Parameters({index}:{index:number}){
 const form=Form.useFormInstance(),id=Form.useWatch(['actions',index,'sop_id'],form);const [loaded,setLoaded]=useState<any>(null),[error,setError]=useState('');
 useEffect(()=>{setLoaded(null);setError('');if(!id)return;const c=new AbortController();api('/evidence-records/knowledge/'+encodeURIComponent(id),undefined,undefined,{signal:c.signal}).then(r=>{if(!c.signal.aborted)setLoaded(r.record);}).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();},[id]);
 const parameters=loaded?.rules?.parameters??{};
 return <>{error&&<Alert type="error" message={error}/>}<Form.List name={[index,'parameter_rows']}>{(fields,{add,remove})=><>{fields.map(({key,name})=><Space key={key} align="start" wrap><Form.Item name={[name,'metric']} label="批准参数" rules={[{required:true}]}><Select style={{minWidth:250}} options={Object.entries(parameters).map(([value,p]:[string,any])=>({value,label:`${value} · ${p.min}～${p.max} ${p.unit}`}))}/></Form.Item><Form.Item name={[name,'value']} label="本次值" rules={[{required:true}]}><InputNumber/></Form.Item><Button onClick={()=>remove(name)}>移除参数</Button></Space>)}<Button disabled={!id||!loaded} onClick={()=>add({})}>增加批准参数</Button></>}</Form.List><small>提交时还会核对所有适用且有效 SOP 的共同约束；此选择器不替代服务端校验。</small></>;
}

export function PlanActions(){
 return <Form.List name="actions" rules={[{validator:(_,v)=>v?.length>0&&v.length<=30?Promise.resolve():Promise.reject(new Error('方案须含1～30项动作'))}]}>{(fields,{add,remove},meta)=><>
  {fields.map(({key,name})=><Card size="small" key={key} title={'整改任务 '+(name+1)} extra={<Button disabled={fields.length===1} onClick={()=>remove(name)}>移除任务</Button>} className="section-bottom">
   <Form.Item name={[name,'action_id']} hidden><Input/></Form.Item>
   <Form.Item name={[name,'type']} label="动作类型" rules={[{required:true}]}><Select options={[{value:'inspect',label:'人工核验'},{value:'measure',label:'测量补证'},{value:'sop_action',label:'批准SOP动作'}]}/></Form.Item>
   <Form.Item name={[name,'description']} label="任务说明" rules={[{required:true,min:2}]}><Input.TextArea rows={2}/></Form.Item>
   <Form.Item name={[name,'owner_role']} label="责任岗位"><Select allowClear options={Object.entries({engineer:'工艺工程师',executor:'现场执行',quality:'质量负责人',inspector:'质检',admin:'管理员'}).map(([value,label])=>({value,label}))}/></Form.Item>
   <Form.Item name={[name,'owner_id']} label="指定责任人标识（可选）" extra="须为本工厂、本产线的有效授权人员；保存时核对岗位。"><Input/></Form.Item>
   <Form.Item name={[name,'sop_id']} label="引用已发布SOP"><RecordSelect resource="knowledge" filters={{kind:'sop',status:'published'}}/></Form.Item>
   <Parameters index={name}/>
  </Card>)}
  <Button disabled={fields.length>=30} onClick={()=>add({action_id:crypto.randomUUID(),type:'inspect',description:'',owner_role:'',owner_id:'',parameter_rows:[]})}>增加整改任务（{fields.length}/30）</Button><Form.ErrorList errors={meta.errors}/>
 </>}</Form.List>;
}

export function ActionReceipts({actions,planId}:{actions:any[];planId:string}){
 return <Form.List name="action_results">{fields=><>{fields.map(({key,name})=><Card size="small" key={key} title={'任务 '+(name+1)+' · '+(actions[name]?.description??'')} className="section-bottom"><Tag>{actions[name]?.owner_role||'责任岗位待核对'}</Tag><small>批准方案 {planId.slice(0,8)}</small><Form.Item name={[name,'action_id']} hidden><Input/></Form.Item><Form.Item name={[name,'status']} label="实际完成情况" rules={[{required:true}]}><Select options={[{value:'not_started',label:'尚未执行'},{value:'partial',label:'部分执行'},{value:'failed',label:'执行失败'},{value:'completed',label:'已完成'}]}/></Form.Item><Form.Item name={[name,'notes']} label="逐项实际记录" rules={[{required:true,min:3}]}><Input.TextArea rows={2}/></Form.Item><Form.Item name={[name,'evidence_ids']} label="逐项证据"><RecordSelect resource="files" labelField="filename" mode="multiple"/></Form.Item></Card>)}</>}</Form.List>;
}

export function ActionReceiptDetails({content,actions=[],evidenceHref=(id:string)=>'/api/files/'+encodeURIComponent(id)}:{content:any;actions?:any[];evidenceHref?:(id:string)=>string}){
 const labels:Record<string,string>={not_started:'尚未执行',partial:'部分执行',failed:'执行失败',completed:'已完成'};
 if(!content.action_results?.length)return <small>历史回执未登记逐项结果，保留原始总回执。</small>;
 return <List size="small" aria-label="逐项整改执行结果" dataSource={content.action_results} renderItem={(item:any,index:number)=><List.Item><div><b>任务 {index+1} · {actions.find(a=>a.action_id===item.action_id)?.description??item.action_id}</b><p><Tag>{labels[item.status]??item.status}</Tag>{item.notes}</p><Space wrap>{item.evidence_ids?.length?item.evidence_ids.map((id:string)=><a key={id} href={evidenceHref(id)} target="_blank" rel="noreferrer">逐项证据 {id.slice(0,8)}</a>):<small>未关联逐项原件</small>}</Space></div></List.Item>}/>;
}
