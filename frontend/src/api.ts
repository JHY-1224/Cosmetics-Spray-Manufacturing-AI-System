type PendingOperation = {key:string;path:string;signature:string;identity:string;createdAt:string;state:'in_flight'|'unknown'};
type RequestOptions = {signal?:AbortSignal;idempotencyKey?:string};
let identity='anonymous';
const storeKey='quality.pending-operations.v1';
let operations:PendingOperation[]=[];
try{const saved=JSON.parse(sessionStorage.getItem(storeKey)??'[]');operations=Array.isArray(saved)?saved.filter(p=>p&&['key','path','signature','identity','createdAt'].every(k=>typeof p[k]==='string')).slice(-100).map(p=>({...p,state:'unknown'})):[];}catch{/* storage can be disabled */}
function save(){try{sessionStorage.setItem(storeKey,JSON.stringify(operations));}catch{/* keep in-memory recovery */}window.dispatchEvent(new Event('quality-pending'));}
export function setApiIdentity(value:string){identity=value;window.dispatchEvent(new Event('quality-pending'));}
export function pendingOperations(){return operations.filter(p=>p.identity===identity&&p.state==='unknown');}
async function hash(value:string|ArrayBuffer){const bytes=typeof value==='string'?new TextEncoder().encode(value):value;return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');}
async function fingerprint(data:unknown){
 if(!(data instanceof FormData))return hash(JSON.stringify(data));
 const parts=[];for(const [key,value] of data.entries())parts.push([key,typeof value==='string'?value:[value.name,value.size,await hash(await value.arrayBuffer())]]);
 return hash(JSON.stringify(parts));
}
export async function resolveOperation(key:string){
 const op=operations.find(p=>p.key===key&&p.identity===identity);if(!op)throw new Error('没有当前账号可查询的待确认操作');
 const res=await fetch('/api/commands/'+encodeURIComponent(key),{credentials:'same-origin'});const body=await res.json();
 if(!res.ok){if(res.status===401)dispatchEvent(new Event('quality-auth-expired'));throw new Error(body.detail?.message??'无法确认原提交结果');}
 if(body.status==='committed'||body.status==='committed_restricted'){operations=operations.filter(p=>p!==op);save();}
 if(body.status==='committed_restricted')dispatchEvent(new Event('quality-command-restricted'));
 return body;
}
export async function api<T=any>(path:string,data?:unknown,method?:string,options:RequestOptions={}):Promise<T>{
 const requestIdentity=identity;
 const verb=method??(data===undefined?'GET':'POST');
 const mutating=data!==undefined&&!path.startsWith('/auth/')&&!path.startsWith('/users')&&!/\/(preview|sample)$/.test(path);
 const headers:Record<string,string>={};let operation:PendingOperation|undefined;
 if(!['GET','HEAD','OPTIONS'].includes(verb)){
  const csrf=document.cookie.split('; ').find(v=>v.startsWith('quality_csrf='))?.split('=').slice(1).join('=');
  if(csrf)headers['X-CSRF-Token']=decodeURIComponent(csrf);
 }
 if(data!==undefined){
  if(mutating){
   const signature=await hash(verb+':'+path+':'+await fingerprint(data));
   if(identity!==requestIdentity)throw new Error('账号已切换，请在当前账号核对后重新办理');
   if(operations.some(p=>p.identity===identity&&p.path===path&&p.signature!==signature))throw new Error('此动作还有结果未确认的原提交，请先查询原请求结果。');
   operation=operations.find(p=>p.identity===identity&&p.signature===signature);
   if(!operation){operation={key:options.idempotencyKey??crypto.randomUUID(),path,signature,identity,createdAt:new Date().toISOString(),state:'in_flight'};operations.push(operation);save();}
   headers['Idempotency-Key']=operation.key;
  }else headers['Idempotency-Key']=options.idempotencyKey??crypto.randomUUID();
  if(!(data instanceof FormData))headers['Content-Type']='application/json';
 }
 try{
  const res=await fetch('/api'+path,{credentials:'same-origin',method:verb,headers,signal:options.signal,
   body:data===undefined?undefined:data instanceof FormData?data:JSON.stringify(data)});
  let body:any;try{body=await res.json();}catch{throw new Error(`服务返回异常（${res.status}）`);}
  if(!res.ok){
   if(body.detail?.code==='command_result_restricted')dispatchEvent(new Event('quality-command-restricted'));
   if(res.status===401&&!path.startsWith('/auth/'))dispatchEvent(new Event('quality-auth-expired'));
   if(res.status<500&&operation){operations=operations.filter(p=>p!==operation);save();operation=undefined;}
   throw new Error(Array.isArray(body.detail)?body.detail.map((e:any)=>e.loc?.join('.')+'：'+e.msg).join('；'):body.detail?.fields?.length?body.detail.fields.slice(0,3).map((e:any)=>e.loc?.join('.')+'：'+e.message).join('；'):body.detail?.message??`请求失败（${res.status}）`);
  }
  if(operation){operations=operations.filter(p=>p!==operation);save();}if(mutating)dispatchEvent(new Event('quality-records-changed'));return body;
 }catch(error){
  if(operation){
   let restricted=false;
   try{const recovered=await resolveOperation(operation.key);if(recovered.status==='committed')return recovered.response as T;restricted=recovered.status==='committed_restricted';}catch{/* keep unknown, never fabricate success */}
   if(restricted)throw new Error('原提交已完成，当前授权不能读取结果。请按原对象核对，不要重复提交。');
   operation.state='unknown';save();
   throw new Error('提交结果待确认。已保留原请求标识；请查询原结果，再次提交相同内容会复用原标识。');
  }
  throw error;
 }
}
