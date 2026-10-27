import type {RefObject} from 'react';
import {CheckOutlined, CloudOutlined, DesktopOutlined, DownOutlined, ExperimentOutlined, ReloadOutlined} from '@ant-design/icons';

export type ModelOption = {
  id:string; name:string; category:'cloud'|'local_base'|'local_finetuned';
  text_model:string; vision_model:string; state:string; selectable:boolean;
  text_external:boolean; vision_external:boolean; revision:string;
};
export type ModelCatalog = {default_id:string; models:ModelOption[]; unreadable_entries:number};
const groups = [
  {id:'cloud',name:'阿里云模型',icon:<CloudOutlined/>,empty:'暂未配置云端模型'},
  {id:'local_finetuned',name:'自部署微调服务',icon:<ExperimentOutlined/>,empty:'微调并部署后，从服务端统一目录中读取'},
];
export function modelState(model:ModelOption){
  if(model.selectable)return '可用';
  if(model.state==='not_deployed')return '待部署';
  if(model.state==='files_incomplete')return '文件未完整';
  if(model.state==='text_adapter_missing'||model.state==='text_merged_missing')return '尚无产物';
  return '待核验';
}

export default function ModelPicker({catalog,selected,open,onOpen,onSelect,onRefresh,busy,rootRef}:{
  catalog:ModelCatalog|null;selected:ModelOption|undefined;open:boolean;onOpen:()=>void;
  onSelect:(model:ModelOption)=>void;onRefresh:()=>void;busy:boolean;rootRef:RefObject<HTMLDivElement|null>;
}){
  return <div className="model-selector" ref={rootRef}>
    <button className="model-pill composer-model" aria-label="选择模型" aria-expanded={open}
      aria-haspopup="menu" disabled={busy} onClick={onOpen} title={selected?`${selected.text_model} · 视觉：${selected.vision_model}`:'正在读取模型列表'}>
      {selected?.category==='cloud'?<CloudOutlined/>:selected?.category==='local_finetuned'?<ExperimentOutlined/>:<DesktopOutlined/>}
      <span className="selected-model-name">{selected?.name||'读取模型中'}</span><DownOutlined/>
    </button>
    {open&&<div className="model-menu" role="menu" aria-label="可选模型">
      <div className="model-menu-heading"><strong>选择模型</strong><button aria-label="刷新模型列表" onClick={onRefresh}><ReloadOutlined/></button></div>
      {groups.map(group=><section className="model-option-group" key={group.id}>
        <div className="model-group-title">{group.icon}{group.name}</div>
        {catalog?.models.filter(model=>model.category===group.id).map(model=><button key={model.id}
          className={'model-option '+(selected?.id===model.id?'chosen':'')} role="menuitemradio"
          aria-checked={selected?.id===model.id} disabled={!model.selectable||busy} onClick={()=>onSelect(model)}>
          <span className="model-choice-check">{selected?.id===model.id&&<CheckOutlined/>}</span>
          <span className="model-choice-content"><span className="model-choice-name">{model.name}</span>
            <small>{model.text_model}</small><small>视觉 · {model.vision_model}</small></span>
          <span className={'model-state '+(model.selectable?'ready':'')}>{modelState(model)}</span>
        </button>)}
        {!catalog?.models.some(model=>model.category===group.id)&&<p className="model-group-empty">{group.empty}</p>}
      </section>)}
      <div className="model-menu-note">自部署模型须在服务器启动并通过核验后才可选择。</div>
    </div>}
  </div>;
}
