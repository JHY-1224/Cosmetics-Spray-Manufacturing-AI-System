import {useEffect,useState} from 'react';
import {Alert,Button,Card,Form,Input,InputNumber,Select} from 'antd';
import {RecordSelect} from './DataTable';
import {api} from './api';

function Parameter({index}:{index:number}){
 const form=Form.useFormInstance(),sop=Form.useWatch(['parameter_rows',index,'sop_id'],form);
 const [rules,setRules]=useState<Record<string,any>>({}),[error,setError]=useState('');
 useEffect(()=>{setRules({});setError('');if(!sop)return;const controller=new AbortController();api('/evidence-records/knowledge/'+encodeURIComponent(sop),undefined,undefined,{signal:controller.signal}).then(r=>setRules(r.record.rules?.parameters??{})).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return()=>controller.abort();},[sop]);
 return <>{error&&<Alert type="error" message={error}/>}<Form.Item name={[index,'sop_id']} label="已批准SOP" rules={[{required:true}]}><RecordSelect resource="knowledge" filters={{kind:'sop',status:'published'}} labelField="title"/></Form.Item><Form.Item name={[index,'metric']} label="批准参数及范围" rules={[{required:true}]}><Select options={Object.entries(rules).map(([value,r])=>({value,label:`${value} · ${r.min}～${r.max} ${r.unit}`}))} onChange={metric=>form.setFieldValue(['parameter_rows',index,'unit'],rules[metric]?.unit)}/></Form.Item><Form.Item name={[index,'value']} label="本次人工批准值" rules={[{required:true}]}><InputNumber/></Form.Item><Form.Item name={[index,'unit']} label="SOP规定单位" rules={[{required:true}]}><Input readOnly/></Form.Item></>;
}

export function RecipeParameters(){
 return <><Alert showIcon message="逐项选择正式SOP、参数和单位。服务端会核对所有适用SOP的共同约束。"/><Form.List name="parameter_rows" rules={[{validator:(_,rows)=>new Set((rows??[]).map((r:any)=>r.metric)).size===(rows??[]).length?Promise.resolve():Promise.reject(new Error('同一参数不能重复定义'))}]}>{(fields,{add,remove},meta)=><>{fields.map(({key,name})=><Card key={key} title={'工艺参数 '+(name+1)} extra={<Button onClick={()=>remove(name)}>移除</Button>}><Parameter index={name}/></Card>)}<Button onClick={()=>add({})}>增加批准参数</Button><Form.ErrorList errors={meta.errors}/></>}</Form.List></>;
}
