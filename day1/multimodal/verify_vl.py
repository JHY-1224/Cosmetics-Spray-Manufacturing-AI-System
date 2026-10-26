"""Actual CUDA vision-adapter smoke in the existing Unsloth environment."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import load_records, conversation


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--adapter',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists(): raise SystemExit('Refusing to overwrite evidence')
    rows=load_records(args.data,allow_unreviewed=True)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    from unsloth import FastVisionModel
    import torch
    if not torch.cuda.is_available(): raise SystemExit('CUDA required')
    model,processor=FastVisionModel.from_pretrained(model_name=str(args.adapter.resolve()),
        load_in_4bit=True,local_files_only=True,trust_remote_code=False)
    FastVisionModel.for_inference(model)
    active=getattr(model,'active_adapters',None)
    if not active or not getattr(model,'peft_config',None): raise RuntimeError('Adapter is not active')
    results=[]
    for row in rows[:3]:
        messages=conversation(row)['messages'][:1]
        inputs=processor.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,
            return_dict=True,return_tensors='pt').to(model.device)
        with torch.inference_mode():
            output=model.generate(**inputs,max_new_tokens=256,do_sample=False)
        answer=processor.batch_decode(output[:,inputs['input_ids'].shape[-1]:],skip_special_tokens=True)[0]
        results.append({'id':row['id'],'answer':answer,'quality_review':'pending'})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as out:
        json.dump({'artifact':str(args.adapter.resolve()),'gpu':torch.cuda.get_device_name(0),
                   'active_adapters':active,'results':results,'note':'runtime activation; not accuracy evaluation'},out,ensure_ascii=False,indent=2)


if __name__=='__main__': main()
