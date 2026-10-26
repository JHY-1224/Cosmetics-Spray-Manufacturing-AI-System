import {createContext,useContext,useEffect,useMemo,useRef,useState} from 'react';
import type {TableProps} from 'antd';
import {Alert,Button,Checkbox,Input,Popover,Space,Table} from 'antd';
import {api} from './api';

type Context={identity:string;query:string;refresh:number};
export const RecordsContext=createContext<Context>({identity:'anonymous',query:'',refresh:0});
type Props<T extends object>=TableProps<T>&{tableId:string;resource?:string;filters?:Record<string,string>;refreshToken?:number;remoteQuery?:string;searchable?:boolean};
export function DataTable<T extends object=any>({tableId,resource,filters={},refreshToken=0,remoteQuery,searchable=true,...props}:Props<T>){
 const context=useContext(RecordsContext),[page,setPage]=useState(1),[size,setSize]=useState(25),[search,setSearch]=useState(''),[sort,setSort]=useState({field:'created_at',order:'desc'}),[reload,setReload]=useState(0);
 const [loaded,setLoaded]=useState<any>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
 const columns=useMemo(()=>(props.columns??[]).map((c:any,index)=>({...c,key:String(c.key??(Array.isArray(c.dataIndex)?c.dataIndex.join('.'):c.dataIndex)??('column-'+index))})),[props.columns]);
 const storageKey='quality.columns.v1:'+context.identity+':'+tableId;
 const readColumns=()=>{try{const value=JSON.parse(localStorage.getItem(storageKey)??'null');return Array.isArray(value)&&value.every(k=>typeof k==='string')?value:null;}catch{return null;}};
 const [preference,setPreference]=useState<{key:string;keys:string[]|null}>(()=>({key:storageKey,keys:readColumns()}));
 const chosen=preference.key===storageKey?preference.keys:readColumns();
 const visible=columns.filter(c=>!chosen||chosen.includes(c.key));
 const filterKey=JSON.stringify(filters),query=remoteQuery??context.query;
 const signature=JSON.stringify([context.identity,query,resource,filterKey,search,sort,page,size,context.refresh,refreshToken,reload]);
 const current=useRef(signature);current.current=signature;
 useEffect(()=>{setPage(1);},[context.identity,query,filterKey,resource,search]);
 useEffect(()=>{if(!resource)return;const controller=new AbortController(),requested=signature;setLoading(true);setError('');
  const params=new URLSearchParams(query);Object.entries(filters).forEach(([k,v])=>{if(v)params.set(k,v);});params.set('page',String(page));params.set('page_size',String(size));if(search)params.set('q',search);params.set('sort_by',sort.field);params.set('sort_order',sort.order);
  api('/records/'+resource+'?'+params,undefined,undefined,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted&&current.current===requested){setLoaded({signature:requested,value});if(value.total>0&&page>Math.ceil(value.total/size))setPage(Math.ceil(value.total/size));}}).catch(e=>{if(!controller.signal.aborted&&current.current===requested)setError(e.message);}).finally(()=>{if(!controller.signal.aborted&&current.current===requested)setLoading(false);});return()=>controller.abort();
 },[signature]);
 useEffect(()=>{const changed=()=>setReload(n=>n+1);addEventListener('quality-records-changed',changed);return()=>removeEventListener('quality-records-changed',changed);},[]);
 const result=loaded?.signature===signature?loaded.value:null;
 function select(keys:string[]|null){if(keys&&!columns.some(c=>keys.includes(c.key)))return;setPreference({key:storageKey,keys});try{if(keys)localStorage.setItem(storageKey,JSON.stringify(keys));else localStorage.removeItem(storageKey);}catch{/* preferences remain usable in memory */}}
 function exportPage(){const rows=(resource?result?.items:props.dataSource)??[], selected=visible.length?visible:columns;const safe=(v:any)=>{let text=v==null?'':typeof v==='object'?JSON.stringify(v):String(v);if(/^\s*[=+\-@\t\r]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';};const lines=[selected.map(c=>safe(typeof c.title==='string'?c.title:c.key)).join(',')];for(const row of rows){lines.push(selected.map(c=>{const path=Array.isArray(c.dataIndex)?c.dataIndex:[c.dataIndex];return safe(c.dataIndex?path.reduce((v:any,k:any)=>v?.[k],row):'');}).join(','));}const url=URL.createObjectURL(new Blob(['\uFEFF'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=tableId+'-current-page.csv';link.click();URL.revokeObjectURL(url);}
 const settings=<Space direction="vertical"><Checkbox.Group style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(110px,1fr))',gap:8,maxHeight:350,overflow:'auto',maxWidth:'75vw'}} value={(visible.length?visible:columns).map(c=>c.key)} options={columns.map(c=>({value:c.key,label:typeof c.title==='string'?c.title:c.key}))} onChange={v=>select(v as string[])}/><Button size="small" onClick={()=>select(null)}>恢复默认列</Button></Space>;
 const displayed=(visible.length?visible:columns).map((c:any)=>({...c,...(resource&&typeof c.dataIndex==='string'&&result?.sort_fields.includes(c.dataIndex)?{sorter:true,sortOrder:sort.field===c.dataIndex?(sort.order==='asc'?'ascend':'descend'):null}:{})}));
 return <div className="records-table" data-table-id={tableId}><Space wrap className="table-controls">{resource&&searchable&&<Input.Search aria-label={tableId+' 搜索'} placeholder="搜索此列表" allowClear onSearch={setSearch} style={{width:240}}/>}<Popover content={settings} trigger="click" title="选择显示列（至少一列）"><Button aria-label={tableId+' 列设置'}>列设置</Button></Popover><Button onClick={exportPage} disabled={!!resource&&!result}>导出当前页</Button>{resource&&<Button onClick={()=>setReload(n=>n+1)}>刷新列表</Button>}</Space>
 {error&&<Alert type="error" showIcon message="列表暂不可用" description={error} action={<Button onClick={()=>setReload(n=>n+1)}>重试</Button>}/>}
 <Table<T> {...props} scroll={props.scroll??{x:'max-content'}} columns={displayed} loading={resource?loading:props.loading} dataSource={resource?(result?.items??[]):props.dataSource} pagination={resource?{current:page,pageSize:size,total:result?.total??0,showSizeChanger:true,pageSizeOptions:[10,25,50,100,200],showTotal:n=>'共 '+n+' 条',responsive:true}:props.pagination??{pageSize:10,showSizeChanger:true,showTotal:n=>'共 '+n+' 条'}} onChange={resource?(p,_f,s:any)=>{setPage(p.current??1);setSize(p.pageSize??25);if(!Array.isArray(s)&&s.field)setSort({field:String(s.field),order:s.order==='ascend'?'asc':'desc'});}:props.onChange}/>
 </div>;
}

/** Searchable authorized selector; never relies on the first legacy catalog page. */
export function RecordSelect({resource,filters={},value,onChange,mode,placeholder,labelField='title',onSelectRecord,disabled,id,ariaLabel,extraOptions=[]}:{id?:string;ariaLabel?:string;extraOptions?:any[];resource:string;filters?:Record<string,string>;value?:any;onChange?:(v:any)=>void;mode?:'multiple';placeholder?:string;labelField?:string;onSelectRecord?:(r:any)=>void;disabled?:boolean}){
 const context=useContext(RecordsContext),[reload,setReload]=useState(0),[search,setSearch]=useState(''),[rows,setRows]=useState<any[]>([]),[error,setError]=useState('');const signature=JSON.stringify([context.identity,context.query,resource,filters,search,context.refresh,reload]);
 useEffect(()=>{const c=new AbortController();setRows([]);setError('');const p=new URLSearchParams(context.query);p.delete('start');p.delete('end');p.set('page_size','50');p.set('q',search);Object.entries(filters).forEach(([k,v])=>p.set(k,v));api('/records/'+resource+'?'+p,undefined,undefined,{signal:c.signal}).then(r=>{if(!c.signal.aborted)setRows(r.items);}).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();},[signature]);
 useEffect(()=>{const changed=()=>setReload(n=>n+1);addEventListener('quality-records-changed',changed);return()=>removeEventListener('quality-records-changed',changed);},[]);
 const options=[...new Map([...rows,...extraOptions.filter(r=>!search||String(r[labelField]??'').toLowerCase().includes(search.toLowerCase()))].map(r=>[r.id,r])).values()];
 return <><SelectBridge getPopupContainer={trigger=>trigger.parentElement??document.body} aria-label={ariaLabel} id={id} disabled={disabled} mode={mode} value={value} onChange={(v:any)=>{onChange?.(v);onSelectRecord?.(options.find(r=>r.id===v));}} options={options.map(r=>({value:r.id,label:(r[labelField]??r.id)+(r.revision?' · v'+r.revision:'')}))} filterOption={false} showSearch allowClear onSearch={setSearch} placeholder={placeholder??'输入名称搜索授权记录'}/>{error&&<small role="alert">{error}</small>}</>;
}
import {Select as SelectBridge} from 'antd';
