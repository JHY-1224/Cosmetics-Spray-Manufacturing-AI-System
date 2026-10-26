import {useEffect,useState,useId,useRef} from 'react';
import {Alert,App,Button,Input,Select,Space,Tag} from 'antd';
import {pendingOperations,resolveOperation} from './api';

export type Scope={site_id:string;line_id:string;product_id:string;process:string;start:string;end:string;q:string;status:string;case_page:string};
const keys=['site_id','line_id','product_id','process','start','end','q','status','case_page'] as const;
const screens=new Set(['overview','cases','inspections','trace','dispositions','routes','recipes','masters','knowledge','adapters','distributions','sites','operations','quality-standards','mixing','supply','cost','digital']);
function read(){const p=new URLSearchParams(location.search);return {view:screens.has(p.get('view')??'')?p.get('view')!:'overview',caseId:p.get('case')??'',scope:Object.fromEntries(keys.map(k=>[k,p.get(k)??(k==='case_page'?'1':'')])) as Scope};}
export function useWorkspaceNavigation(guard?:(proceed:()=>void,cancel:()=>void)=>void){
 const [state,setState]=useState(read);const current=useRef(state),guardRef=useRef(guard);current.current=state;guardRef.current=guard;
 function url(value:typeof state){const params=new URLSearchParams();if(value.view!=='overview')params.set('view',value.view);if(value.caseId)params.set('case',value.caseId);for(const key of keys)if(value.scope[key]&&(key!=='case_page'||value.scope[key]!=='1'))params.set(key,value.scope[key]);return location.pathname+(params.size?'?'+params.toString():'');}
 useEffect(()=>{const pop=()=>{const next=read();const previous=current.current;const commit=()=>setState(next);const cancel=()=>history.replaceState({},'',url(previous));if(guardRef.current)guardRef.current(commit,cancel);else commit();};addEventListener('popstate',pop);return()=>removeEventListener('popstate',pop);},[]);
 function update(next:Partial<typeof state>,replace=false){const value={...state,...next};history[replace?'replaceState':'pushState']({},'',url(value));setState(value);}
 return {...state,update};
}
export function scopeQuery(scope:Scope,kind='events'){
 const p=new URLSearchParams();for(const key of ['site_id','line_id','product_id','process','start','end'] as const)if(scope[key]&&(kind!=='reference'||!['start','end'].includes(key)))p.set(key,scope[key]);
 return p;
}
export function ScopeControls({scope,sites,lines,onChange,timezone}:{scope:Scope;sites:string[];lines:any[];onChange:(s:Scope)=>void;timezone:string}){
 const change=(key:keyof Scope,value:string)=>onChange({...scope,[key]:value,case_page:'1',...(key==='site_id'?{line_id:''}:{})});
 const stamp=(value:string)=>{const date=new Date(value);return value&&Number.isFinite(date.getTime())?date.toISOString().slice(0,16):'';};
 return <section className="scope-bar" aria-label="生产范围筛选"><Space wrap size={[12,12]}>
  <label>工厂<Select aria-label="筛选工厂" value={scope.site_id||undefined} placeholder="全部授权工厂" allowClear onChange={v=>change('site_id',v??'')} options={sites.map(id=>({value:id,label:id}))}/></label>
  <label>产线<Select aria-label="筛选产线" value={scope.line_id||undefined} placeholder="全部授权产线" allowClear onChange={v=>change('line_id',v??'')} options={lines.filter(r=>!scope.site_id||r.site_id===scope.site_id).map(r=>({value:r.id,label:r.name}))}/></label>
  <label>工艺<Select aria-label="筛选工艺" value={scope.process||undefined} placeholder="两类工艺" allowClear onChange={v=>change('process',v??'')} options={[{value:'plastic_spray',label:'塑料瓶喷涂'},{value:'glass_vacuum',label:'玻璃瓶真空蒸镀'}]}/></label>
  <label>产品标识<Input aria-label="筛选产品标识" placeholder="来源中的产品 ID" value={scope.product_id} allowClear onChange={e=>change('product_id',e.target.value)}/></label>
  <label>开始时间（UTC）<Input aria-label="筛选开始时间UTC" type="datetime-local" value={stamp(scope.start)} onChange={e=>change('start',e.target.value?new Date(e.target.value+'Z').toISOString():'')}/></label>
  <label>结束时间（UTC）<Input aria-label="筛选结束时间UTC" type="datetime-local" value={stamp(scope.end)} onChange={e=>change('end',e.target.value?new Date(e.target.value+'Z').toISOString():'')}/></label>
  <Button onClick={()=>onChange({...scope,site_id:'',line_id:'',product_id:'',process:'',start:'',end:'',q:'',status:'',case_page:'1'})}>清除筛选</Button>
 </Space><div className="scope-caption">仅当前企业授权范围 · 表格时间按 {timezone} 显示 · 筛选与页面地址同步，导出使用相同范围</div></section>;
}
export function PendingOperations({onResolved}:{onResolved:(restricted?:boolean)=>void}){
 const {message}=App.useApp();const [pending,setPending]=useState(pendingOperations);
 useEffect(()=>{const update=()=>setPending(pendingOperations());addEventListener('quality-pending',update);return()=>removeEventListener('quality-pending',update);},[]);
 if(!pending.length)return null;
   return <Alert className="section-bottom" type="warning" showIcon message="有提交结果待确认" description={<Space direction="vertical">{pending.map(p=><Space key={p.key} wrap><Tag>{p.path}</Tag><span>原请求 {p.key.slice(0,8)}</span><Button onClick={async()=>{try{const r=await resolveOperation(p.key);if(r.status==='committed'||r.status==='committed_restricted'){r.status==='committed'?message.success('已确认原提交成功，请刷新查看；未创建重复操作'):message.warning(r.message);onResolved(r.status==='committed_restricted');}else message.warning('结果仍待确认，请保留原标识继续核对');}catch(e:any){message.error(e.message);}}}>查询原提交结果</Button></Space>)}</Space>}/>;
}

export function useEditingGuard(active:boolean){
 const id=useId();
 useEffect(()=>{const notify=(value:boolean)=>dispatchEvent(new CustomEvent('quality-editing',{detail:{id,active:value}}));notify(active);const leave=(event:BeforeUnloadEvent)=>{if(active){event.preventDefault();event.returnValue='';}};addEventListener('beforeunload',leave);return()=>{notify(false);removeEventListener('beforeunload',leave);};},[id,active]);
 return id;
}
