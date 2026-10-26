import {useEffect,useState} from 'react';
import {Alert,Card,Descriptions,Empty,Select,Space,Tag,Timeline} from 'antd';
import {api} from './api';

export function ProcessContext({lines,selectedLine,onSelect,refresh}:{lines:any[];selectedLine:string;onSelect:(id:string)=>void;refresh:number}){
 const [report,setReport]=useState<any>(null),[error,setError]=useState('');
 useEffect(()=>{setReport(null);setError('');if(!selectedLine)return;const c=new AbortController();api('/process-context/'+selectedLine,undefined,undefined,{signal:c.signal}).then(setReport).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();},[selectedLine,refresh]);
 return <Card title="实际产线与采集位置" className="section-bottom" extra={<Select aria-label="工艺图产线" placeholder="选择实际产线" value={selectedLine||undefined} onChange={onSelect} options={lines.map(r=>({value:r.id,label:r.name}))} style={{minWidth:200}}/>}>
 {error&&<Alert type="error" message={error}/>}<Alert className="section-bottom" showIcon message="参考模板发布并绑定后才是实际路线。未配置测点显示未采集，通信正常不代表工艺正常。"/>
 {!selectedLine?<Empty description="选择产线以查看批准路线及点表"/>:!report?<Empty description="正在读取产线资料"/>:!report.route?<Empty description="这条产线尚未绑定已发布路线，请先完成工艺确认"/>:<><Descriptions size="small" items={[{key:'route',label:'工艺版本',children:report.route.name+' v'+report.route.revision},{key:'site',label:'工厂',children:report.line.site_id}]}/>
 <Timeline className="process-timeline" items={report.steps.map((step:any)=>({color:step.sources.length?'blue':'gray',children:<div className="process-node"><div><strong>{step.name}</strong><Space>{step.quality_gate&&<Tag>质量关口</Tag>}<Tag>{step.collection_state==='configured'?'已登记来源':'未采集'}</Tag></Space></div>{step.sources.map((s:any)=><details key={s.id}><summary>{s.name} · {s.mapping_version} · {s.health.status??'尚未验证'}</summary><p>通信：{s.health.last_connected_at??'尚无记录'}；源数据：{s.health.latest_source_time??'缺失'}</p>{s.points.map((p:any)=><p key={p.name}>{p.meaning} · {p.value_type==='setpoint'?'设定':p.value_type==='measured'?'实测':p.value_type==='uncollected'?'未采集':p.value_type} · {p.unit||'无单位依据'}</p>)}</details>)}{!!step.candidate_points?.length&&<details><summary>候选点表（未采集，待现场确认）</summary>{step.candidate_points.map((p:any)=><p key={p.name}>{p.meaning} · 候选来源：{p.candidate_source} · {p.unit_candidate||'单位待确认'} · 未采集</p>)}</details>}</div>}))}/>
 <p className="muted">空气温度不等于瓶体温度；炉次、挂架与位置必须来自现场身份记录。当前页面未进行设备控制。</p></>}
 </Card>;
}
