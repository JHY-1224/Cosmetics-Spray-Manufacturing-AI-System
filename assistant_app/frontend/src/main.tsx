import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AppstoreOutlined, ArrowUpOutlined, CheckOutlined, CloseOutlined, CopyOutlined,
  DeleteOutlined, ExperimentOutlined, EyeOutlined, FileImageOutlined, InfoCircleOutlined,
  LoadingOutlined, MessageOutlined, PlusOutlined, QuestionCircleOutlined,
  SearchOutlined, SettingOutlined, StopOutlined, ThunderboltFilled, ToolOutlined,
  LikeOutlined, DislikeOutlined, ReloadOutlined, SafetyCertificateOutlined} from '@ant-design/icons';
import './style.css';
import ModelPicker, {type ModelCatalog, type ModelOption} from './ModelPicker';

type Conversation = {id:string;title:string;created:number};
type Evidence = {image_id:string;order:number;image_kind:string;visible_observations:string[];
  extracted_fields:{label:string;raw_text:string|null;value_text:string|null;unit_text:string|null;location:string;status:string}[];
  uncertain_items:string[];missing_context:string[];warnings:string[]};
type Job = {id:string;conversation_id:string;text:string;attachment_ids:string[];phase:string;
  answer:string;answer_html:string;error:string|null;vision:Evidence[];metadata:Record<string,unknown>};
type Status = {text:{base:string;state:string;available:boolean;endpoint_available:boolean;provider:string};
  vision:{base:string;available:boolean;state:string;provider:string};mock:boolean;
  limits:{max_images:number;image_bytes:number;ttl_seconds:number}};
type Pick = {key:string;file:File;url:string};
function reviewNotices(job:Job):string[]{
  const rows=job.metadata.review_notices;
  return Array.isArray(rows)?rows.filter((x):x is {message:string}=>!!x&&typeof x==='object'&&typeof x.message==='string').map(x=>x.message):[];
}
const terminal = ['done','failed','cancelled','interrupted'];
const phases:Record<string,string> = {queued:'正在排队',reading_images:'正在逐张读取图片',analyzing:'正在整理证据',generating:'正在生成回答',done:'已完成',failed:'处理失败',cancelled:'已取消',interrupted:'服务重启，任务已中断'};
let csrf = '';
async function api<T>(url:string, init:RequestInit = {}):Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type','application/json');
  if (init.method && init.method !== 'GET') headers.set('X-CSRF-Token',csrf);
  const result = await fetch('/api/assistant'+url,{...init,headers,credentials:'same-origin'});
  const data = await result.json();
  if (!result.ok) throw new Error(data.detail?.message || '请求失败，请稍后再试');
  return data;
}
const examples = [
  {icon:<ToolOutlined/>,label:'喷涂工艺',title:'表面出现橘皮，先查什么？',text:'塑料包材喷涂后出现橘皮，应该先收集哪些信息、按什么顺序排查？',color:'blue'},
  {icon:<FileImageOutlined/>,label:'图片识读',title:'拍张照片，一起看细节',text:'请帮我读取这张图片中的可见信息，区分观察结果和需要人工确认的内容。',color:'purple'},
  {icon:<ExperimentOutlined/>,label:'材料与相容性',title:'瓶器和内容物，如何验证？',text:'新内容物配方与喷涂瓶器做相容性验证，需要哪些基准信息和试验记录？',color:'amber'},
];

function Assistant(){
  const [conversations,setConversations]=useState<Conversation[]>([]);
  const [active,setActive]=useState<string|null>(null);
  const activeRef=useRef<string|null>(null);
  const [jobs,setJobs]=useState<Job[]>([]);
  const [text,setText]=useState('');
  const [picks,setPicks]=useState<Pick[]>([]);
  const picksRef=useRef<Pick[]>([]);
  const [status,setStatus]=useState<Status|null>(null);
  const [error,setError]=useState('');
  const [submitting,setSubmitting]=useState(false);
  const [initialized,setInitialized]=useState(false);
  const [panel,setPanel]=useState<'help'|null>(null);
  const [catalog,setCatalog]=useState<ModelCatalog|null>(null);
  const [selectedModelId,setSelectedModelId]=useState('');
  const [modelMenu,setModelMenu]=useState(false);
  const modelPicker=useRef<HTMLDivElement>(null);
  const selectedModel=catalog?.models.find(model=>model.id===selectedModelId);
  const [preview,setPreview]=useState<string|null>(null);
  const [filter,setFilter]=useState('');
  const [searching,setSearching]=useState(false);
  const [drag,setDrag]=useState(false);
  const [copied,setCopied]=useState('');
  const [feedback,setFeedback]=useState<Record<string,string>>({});
  const input=useRef<HTMLInputElement>(null);
  const textarea=useRef<HTMLTextAreaElement>(null);
  const bottom=useRef<HTMLDivElement>(null);
  const generation=useRef(0);
  const loadRevision=useRef(0);
  const sendLock=useRef(false);
  const staged=useRef<{conversation:string;key:string;text:string;ids:string[];keys:string[];modelId:string}|null>(null);
  const pending=jobs.find(j=>!terminal.includes(j.phase));

  function switchTo(id:string|null){
    generation.current++;
    activeRef.current=id;setActive(id);setJobs([]);setText('');setError('');
    clearPicks();staged.current=null;
    if(id) localStorage.setItem('coating-current-conversation',id); else localStorage.removeItem('coating-current-conversation');
  }
  async function list(){setConversations(await api<Conversation[]>('/conversations'));}
  async function load(id:string){
    const current=generation.current, revision=++loadRevision.current;
    const result=await api<{messages:Job[]}>('/conversations/'+id);
    if(activeRef.current===id&&generation.current===current&&revision===loadRevision.current) setJobs(result.messages);
  }
  async function refreshStatus(){setStatus(await api<Status>('/status'));}
  async function refreshCatalog(refresh=false){
    const result=await api<ModelCatalog>('/models'+(refresh?'?refresh=true':''));
    setCatalog(result);
    setSelectedModelId(current=>{
      const preferred=current||localStorage.getItem('coating-selected-model')||result.default_id;
      return result.models.some(model=>model.id===preferred)?preferred:result.default_id;
    });
  }
  function selectModel(model:ModelOption){
    if(pending||submitting||!model.selectable)return;
    setSelectedModelId(model.id);localStorage.setItem('coating-selected-model',model.id);
    staged.current=null;setModelMenu(false);
  }
  useEffect(()=>{
    if(!modelMenu)return;
    const outside=(event:PointerEvent)=>{if(!modelPicker.current?.contains(event.target as Node))setModelMenu(false);};
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')setModelMenu(false);};
    document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
    return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);};
  },[modelMenu]);

  useEffect(()=>{
    let alive=true;
    (async()=>{
      try{
        const session=await api<{csrf_token:string}>('/session',{method:'POST'});csrf=session.csrf_token;
        const rows=await api<Conversation[]>('/conversations');
        if(!alive)return;
        setConversations(rows);setInitialized(true);
        const previous=localStorage.getItem('coating-current-conversation');
        if(previous&&rows.some(c=>c.id===previous)){activeRef.current=previous;setActive(previous);await load(previous);}
        await refreshStatus();
        await refreshCatalog();
      }catch(e){if(alive)setError((e as Error).message);}
    })();
    return()=>{alive=false;picksRef.current.forEach(p=>URL.revokeObjectURL(p.url));};
  },[]);
  useEffect(()=>{
    if(!active)return;
    let alive=true;
    const id=active;
    load(id).catch(e=>{if(alive&&activeRef.current===id)setError(e.message);});
    return()=>{alive=false;};
  },[active]);
  useEffect(()=>{
    if(!pending||!active)return;
    const id=active, request=pending.id;
    const source=new EventSource('/api/assistant/requests/'+request+'/events');
    let busy=false;
    const update=async()=>{
      if(busy)return;busy=true;
      try{await load(id);}catch(e){if(activeRef.current===id)setError((e as Error).message);}finally{busy=false;}
    };
    ['phase','vision_result','done','error'].forEach(kind=>source.addEventListener(kind,update));
    // Reconnect and browser refresh only query the persisted task, never regenerate it.
    const timer=window.setInterval(update,2500);
    return()=>{source.close();clearInterval(timer);};
  },[active,pending?.id]);
  useEffect(()=>{bottom.current?.scrollIntoView({behavior:'smooth'});},[jobs,pending?.phase]);
  useEffect(()=>{if(!preview&&!panel)return;const close=(e:KeyboardEvent)=>{if(e.key==='Escape'){setPreview(null);setPanel(null);}};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[preview,panel]);

  function clearPicks(){picksRef.current.forEach(p=>URL.revokeObjectURL(p.url));picksRef.current=[];setPicks([]);if(input.current)input.current.value='';}
  function addFiles(files:File[]){
    staged.current=null;setError('');
    if(files.some(f=>!['image/jpeg','image/png','image/webp'].includes(f.type))){setError('请选择 JPEG、PNG 或 WebP 静态图片。');return;}
    if(files.some(f=>f.size>(status?.limits.image_bytes||6*1024*1024))){setError('单张图片不能超过6 MiB，请裁剪局部后重试。');return;}
    if(picksRef.current.length+files.length>(status?.limits.max_images||3)){setError('每条消息最多支持3张图片。');return;}
    const next=[...picksRef.current,...files.map(file=>({key:crypto.randomUUID(),file,url:URL.createObjectURL(file)}))];
    picksRef.current=next;setPicks(next);
  }
  function removePick(key:string){staged.current=null;const item=picksRef.current.find(p=>p.key===key);if(item)URL.revokeObjectURL(item.url);const next=picksRef.current.filter(p=>p.key!==key);picksRef.current=next;setPicks(next);}
  async function send(){
    if(sendLock.current||pending||(!text.trim()&&!picksRef.current.length)||!initialized||!selectedModelId)return;
    sendLock.current=true;setSubmitting(true);setError('');
    const originalText=text.trim(), files=[...picksRef.current], turn=generation.current, modelId=selectedModelId;
    let id=activeRef.current;
    try{
      if(!id){const c=await api<{id:string}>('/conversations',{method:'POST'});id=c.id;
        if(generation.current!==turn)return;
        activeRef.current=id;setActive(id);localStorage.setItem('coating-current-conversation',id);}
      let snapshot=staged.current;
      if(!snapshot||snapshot.conversation!==id||snapshot.text!==originalText||snapshot.modelId!==modelId||snapshot.keys.join()!==files.map(f=>f.key).join()){
        const ids:string[]=[];
        if(files.length){const form=new FormData();form.append('conversation_id',id);files.forEach(f=>form.append('files',f.file));
          const upload=await api<{attachments:{id:string}[]}>('/uploads',{method:'POST',body:form});ids.push(...upload.attachments.map(x=>x.id));}
        snapshot={conversation:id,key:crypto.randomUUID(),text:originalText,ids,keys:files.map(f=>f.key),modelId};staged.current=snapshot;
      }
      if(generation.current!==turn)return;
      await api('/conversations/'+id+'/messages',{method:'POST',body:JSON.stringify({text:snapshot.text,attachment_ids:snapshot.ids,request_key:snapshot.key,model_id:snapshot.modelId})});
      if(activeRef.current===id&&generation.current===turn){setText('');clearPicks();staged.current=null;await load(id);}
      await list();
    }catch(e){if(generation.current===turn)setError((e as Error).message);}
    finally{sendLock.current=false;setSubmitting(false);}
  }
  async function retry(job:Job){
    if(submitting||pending)return;
    setSubmitting(true);setError('');
    try{await api('/conversations/'+job.conversation_id+'/messages',{method:'POST',body:JSON.stringify({text:job.text,attachment_ids:job.attachment_ids,request_key:crypto.randomUUID(),model_id:selectedModelId})});if(activeRef.current===job.conversation_id)await load(job.conversation_id);}
    catch(e){setError((e as Error).message);}finally{setSubmitting(false);}
  }
  async function clear(){if(!active)return;const id=active;try{await api('/conversations/'+id,{method:'DELETE'});switchTo(null);await list();}catch(e){setError((e as Error).message);}}
  async function cancel(){if(!pending)return;try{await api('/requests/'+pending.id+'/cancel',{method:'POST'});if(active)await load(active);}catch(e){setError((e as Error).message);}}
  async function vote(job:Job,value:string){try{await api('/feedback',{method:'POST',body:JSON.stringify({request_id:job.id,value})});setFeedback({...feedback,[job.id]:value});}catch(e){setError((e as Error).message);}}
  async function copy(job:Job){try{await navigator.clipboard.writeText(job.answer);setCopied(job.id);}catch{setError('浏览器未允许复制，请选中文字复制。');}}
  const modelNotice=selectedModel?(selectedModel.text_external||selectedModel.vision_external?
    (selectedModel.text_external&&selectedModel.vision_external?'当前使用阿里云，文字与图片将发送至云端':
      selectedModel.text_external?'文字使用云端模型，图片使用本地视觉服务':'文字使用本地模型，图片发送至云端识读'):
    '当前使用本地模型服务，不自动进入本项目训练集'):'图片与对话不会自动进入本项目训练集';

  return <div className="app-shell" onDragOver={e=>{e.preventDefault();if(e.dataTransfer.types.includes('Files'))setDrag(true);}} onDrop={e=>{e.preventDefault();setDrag(false);addFiles([...e.dataTransfer.files]);}}>
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="涂镀智溯首页"><span className="brand-mark"><AppstoreOutlined/></span><span>涂镀智溯<small>PROCESS INTELLIGENCE</small></span></a>
      <button className="new-chat" onClick={()=>switchTo(null)} disabled={submitting}><PlusOutlined/><span>开启新对话</span><kbd>＋</kbd></button>
      <button className="nav-item" onClick={()=>{setSearching(!searching);setFilter('');}}><SearchOutlined/><span>搜索对话</span></button>
      {searching&&<input className="history-search" aria-label="搜索历史对话" value={filter} placeholder="搜索当前访客的对话" onChange={e=>setFilter(e.target.value)}/>}
      <div className="history-label">近期对话 <span>{conversations.length||''}</span></div>
      <nav className="history" aria-label="近期对话">
        {conversations.filter(c=>c.title.includes(filter)).map(c=><button key={c.id} disabled={submitting} className={'history-item '+(active===c.id?'selected':'')} onClick={()=>switchTo(c.id)}><MessageOutlined/><span>{c.title}</span></button>)}
        {!conversations.length&&<div className="history-empty">每一次探讨，都从这里开始。<br/>你的近期对话将保存在此处。</div>}
      </nav>
      <div className="sidebar-bottom">
        <div className="scope-note"><SafetyCertificateOutlined/><div>专注工艺，审慎判断<small>建议需结合现场证据与人工核验</small></div></div>
        <button className="nav-item" onClick={()=>setModelMenu(true)} disabled={submitting||!!pending}><SettingOutlined/><span>模型与连接</span><span className={'status-dot '+(selectedModel?.selectable?'online':'')}/></button>
        <button className="nav-item" onClick={()=>setPanel('help')}><QuestionCircleOutlined/><span>使用指南</span></button>
        <div className="visitor"><span className="visitor-avatar"><ExperimentOutlined/></span><div>访客工作空间<small>免登录 · 独立会话</small></div><span className="visitor-badge">访客</span></div>
      </div>
    </aside>
    <main className="main">
      <header className="topbar"><div className="topbar-start"><span className="product-title">多模态工艺助手</span><span className="version">PREVIEW</span></div>
      </header>
      {status?.mock&&<div className="mock-banner">开发模拟测试 · 当前回答不来自真实模型，不代表实际推理效果</div>}
      <div className={'chat-scroll '+(!jobs.length?'empty-scroll':'')}>
        {!jobs.length?<section className="welcome">
          <span className="welcome-symbol"><AppstoreOutlined/></span>
          <div className="eyebrow">为每一道工艺，多想一步</div>
          <h1>让工艺问题，更有<span>解。</span></h1>
          <p className="welcome-description">从包材喷涂到真空镀铝，与你一起看清细节、理清思路。</p>
          <div className="example-grid">{examples.map((e,index)=><button className="example-card" key={e.label} onClick={()=>{setText(e.text);textarea.current?.focus();if(index===1)input.current?.click();}}><span className={'example-icon '+e.color}>{e.icon}</span><span className="example-label">{e.label}</span><strong>{e.title}</strong><span className="example-arrow">↗</span></button>)}</div>
          <div className="capability-line"><EyeOutlined/> 读懂图片 <span/> <MessageOutlined/> 连续追问 <span/> <ToolOutlined/> 辅助核查</div>
        </section>:<div className="messages">
          <div className="conversation-heading"><span>工艺探讨</span><button className="text-button" onClick={clear}><DeleteOutlined/>清空当前对话</button></div>
          {jobs.map(job=><article className="turn" key={job.id}>
            <div className="user-message"><div className="message-images">{job.attachment_ids.map((id,i)=><button key={id} onClick={()=>setPreview('/api/assistant/attachments/'+id)} aria-label={'放大第'+(i+1)+'张图片'}><img src={'/api/assistant/attachments/'+id} alt={'第'+(i+1)+'张图片，过期后需重新上传'}/><span>图片 {i+1}</span></button>)}</div>{job.text&&<p>{job.text}</p>}</div>
            <div className="assistant-message"><span className="assistant-avatar"><AppstoreOutlined/></span><div className="answer-body"><div className="answer-name">涂镀智溯 <small>工艺助手</small></div>
              {reviewNotices(job).length>0&&<div className="review-warning" role="alert"><strong><SafetyCertificateOutlined/> 请先核对用量依据</strong>{reviewNotices(job).map((notice,i)=><p key={i}>{notice}</p>)}</div>}
              {job.vision.length>0&&<details className="vision-result"><summary><EyeOutlined/>图中可见信息 · {job.vision.length} 张 <small>模型识别，未经人工确认</small></summary>{job.vision.map(v=><div className="vision-item" key={v.image_id}><strong>图片 {v.order} · {v.image_kind}</strong><ul>{v.visible_observations.map((o,i)=><li key={i}>{o}</li>)}</ul>{v.extracted_fields.length>0&&<div className="field-table"><table><thead><tr><th>字段</th><th>原始文字</th><th>识别状态</th></tr></thead><tbody>{v.extracted_fields.map((f,i)=><tr key={i}><td>{f.label}</td><td>{f.raw_text||'未知'}</td><td>{f.status==='readable'?'可辨识':'待确认'}</td></tr>)}</tbody></table></div>}{[...v.uncertain_items,...v.missing_context,...v.warnings].map((item,i)=><p className="uncertain" key={i}>{item}</p>)}</div>)}</details>}
              {!terminal.includes(job.phase)&&<div className="phase"><LoadingOutlined/>{phases[job.phase]||job.phase}</div>}
              {job.answer&&<><div className="answer-label">基于当前信息的分析与核查建议</div><div className="markdown" dangerouslySetInnerHTML={{__html:job.answer_html}}/><div className="answer-actions"><button aria-label="复制回答" onClick={()=>copy(job)}>{copied===job.id?<CheckOutlined/>:<CopyOutlined/>}</button><button className={feedback[job.id]==='helpful'?'voted':''} aria-label="有帮助" onClick={()=>vote(job,'helpful')}><LikeOutlined/></button><button className={feedback[job.id]==='problem'?'voted':''} aria-label="有问题" onClick={()=>vote(job,'problem')}><DislikeOutlined/></button><span>建议仅供人工核查参考</span></div></>}
              {job.error&&<div className="job-error"><InfoCircleOutlined/>{job.vision.length?'图像读取已完成，建议生成失败。':''}{job.error}</div>}
              {['failed','interrupted'].includes(job.phase)&&<button className="text-button retry" onClick={()=>retry(job)} disabled={submitting||!!pending}><ReloadOutlined/>重试这条消息</button>}
              {job.phase==='cancelled'&&<div className="muted">已取消本次处理</div>}
            </div></div>
          </article>)}<div ref={bottom}/>
        </div>}
      </div>
      <div className="composer-area">
        {error&&<div className="error-banner" role="alert"><InfoCircleOutlined/><span>{error}</span><button aria-label="关闭提示" onClick={()=>setError('')}><CloseOutlined/></button></div>}
        {selectedModel?.category==='local_base'&&<div className="preview-note">基础模型预览，尚未加载本项目9B微调版本</div>}
        <div className={'composer '+(drag?'dragging':'')}>
          {picks.length>0&&<div className="attachments">{picks.map((p,i)=><div className="attachment" key={p.key}><button className="attachment-preview" onClick={()=>setPreview(p.url)} aria-label={'预览待发送图片'+(i+1)}><img src={p.url} alt={'待发送图片'+(i+1)}/></button><span>图片 {i+1}</span><button className="remove-image" aria-label={'移除图片'+(i+1)} onClick={()=>removePick(p.key)}><CloseOutlined/></button></div>)}</div>}
          <textarea ref={textarea} aria-label="输入工艺问题" placeholder="描述你的工艺问题，或上传图片一起分析…" value={text} maxLength={6000} disabled={submitting} onChange={e=>{setText(e.target.value);staged.current=null;}} onPaste={e=>{const files=[...e.clipboardData.items].filter(i=>i.kind==='file').map(i=>i.getAsFile()).filter((f):f is File=>!!f);if(files.length){e.preventDefault();addFiles(files);}}} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send();}}}/>
          <div className="composer-tools"><div><button className="image-button" onClick={()=>input.current?.click()} disabled={submitting} aria-label="上传图片"><PlusOutlined/><span>添加图片</span></button><span className="upload-hint">JPEG / PNG / WebP · 最多3张</span></div><div className="send-group"><ModelPicker catalog={catalog} selected={selectedModel} open={modelMenu} onOpen={()=>setModelMenu(!modelMenu)} onSelect={selectModel} onRefresh={()=>refreshCatalog(true).catch(e=>setError(e.message))} busy={submitting||!!pending} rootRef={modelPicker}/><span className="enter-hint">Enter 发送</span>{pending?<button className="send-button stop" aria-label="取消生成" onClick={cancel}><StopOutlined/></button>:<button className="send-button" aria-label="发送消息" onClick={send} disabled={submitting||!initialized||!selectedModelId||(!text.trim()&&!picks.length)}>{submitting?<LoadingOutlined/>:<ArrowUpOutlined/>}</button>}</div></div>
          <input type="file" ref={input} accept="image/jpeg,image/png,image/webp" multiple hidden onChange={e=>{addFiles([...e.target.files||[]]);e.target.value='';}}/>
        </div>
        <div className="composer-footer"><span><SafetyCertificateOutlined/> {modelNotice}</span><span>AI 可能出错，关键结论请人工核验。</span></div>
      </div>
      <footer className="page-footer"><span>涂镀智溯 <b>·</b> 从细节，走向可靠</span><span>包材喷涂 / 真空镀铝 / 品质核查</span></footer>
    </main>
    {drag&&<div className="drop-overlay" onDragLeave={()=>setDrag(false)}><FileImageOutlined/><h2>松开图片，开始一起分析</h2><p>每条消息最多3张静态图片</p></div>}
    {panel&&<div className="modal-backdrop" onClick={()=>setPanel(null)}><section className="modal" role="dialog" aria-modal="true" aria-label="使用指南" onClick={e=>e.stopPropagation()}><button className="modal-close icon-button" aria-label="关闭窗口" onClick={()=>setPanel(null)}><CloseOutlined/></button><span className="modal-icon"><QuestionCircleOutlined/></span><h2>从一个具体问题开始</h2><p>你可以描述喷涂、固化、真空镀铝问题，也可以上传瓶器、表面缺陷或工艺记录图片。</p><ol><li>每条消息支持1—3张静态图片，可拖拽或粘贴。</li><li>文字与图片一起发送，图片会按顺序逐张读取。</li><li>继续追问时，请说明“第几张图片”和具体位置。</li><li>Enter 发送，Shift + Enter 换行；处理期间可以取消。</li></ol><p>当前访客的短期历史保存在独立空间，默认24小时后到期。清空对话会同时删除后台关联消息与图片。</p><p className="connection-note">本助手提供解释与人工核查建议。设备参数、配方变更和质量放行，仍需依据批准标准和现场证据。</p></section></div>}
    {preview&&<div className="lightbox" role="dialog" aria-modal="true" aria-label="图片预览" onClick={()=>setPreview(null)}><button aria-label="关闭图片预览" className="lightbox-close" onClick={()=>setPreview(null)}><CloseOutlined/></button><img src={preview} alt="放大的图片，可通过关闭按钮返回" onClick={e=>e.stopPropagation()}/></div>}
  </div>;
}
createRoot(document.getElementById('root')!).render(<Assistant/>);
