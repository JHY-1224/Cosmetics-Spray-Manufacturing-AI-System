"""Actual CUDA vision-adapter smoke in the existing Unsloth environment."""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dataset import load_records, conversation
from day1.generation import stop_token_ids


def run_inference(model, processor, rows, max_new_tokens=256, compare_base=False):
    """Same images/template/decoding, with LoRA disabled versus enabled.

    This comparison uses the adapter's processor and the same quantized base;
    it is not an independent clean-base or held-out accuracy evaluation.
    """
    import torch
    if compare_base and not callable(getattr(model, 'disable_adapter', None)):
        raise RuntimeError('Cannot disable adapter for a trustworthy base comparison')
    tokenizer = getattr(processor, 'tokenizer', processor)
    eos = model.generation_config.eos_token_id
    stop_ids = stop_token_ids(eos, tokenizer.eos_token_id)
    results = []
    for row in rows:
        # Reference answers and provenance are excluded from the model input.
        messages = conversation(row)['messages'][:1]
        inputs = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors='pt').to(model.device)
        if 'pixel_values' not in inputs:
            raise RuntimeError('Processor did not produce image pixels')
        count = inputs['input_ids'].shape[-1]
        for variant in (('base', 'finetuned') if compare_base else ('finetuned',)):
            torch.manual_seed(3407)
            context = model.disable_adapter() if variant == 'base' else nullcontext()
            with context, torch.inference_mode():
                output = model.generate(**inputs, max_new_tokens=max_new_tokens,
                    do_sample=False, eos_token_id=stop_ids)
            ids = output[0, count:].tolist()
            result = {'id': row['id'], 'variant': variant, 'split': row['split'],
                      'prompt_tokens': count, 'completion_tokens': len(ids),
                      'ended_with_eos': bool(ids and ids[-1] in stop_ids),
                      'hit_output_limit': len(ids) >= max_new_tokens,
                      'answer': tokenizer.decode(ids, skip_special_tokens=True),
                      'quality_review': 'pending'}
            results.append(result)
            print(json.dumps({k: result[k] for k in (
                'id', 'variant', 'completion_tokens', 'ended_with_eos', 'hit_output_limit')}), flush=True)
    return results


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--adapter',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--compare-base',action='store_true',help='Compare the same 4bit base with LoRA disabled/enabled')
    p.add_argument('--limit',type=int,default=3)
    p.add_argument('--max-new-tokens',type=int,default=256)
    args=p.parse_args()
    if args.limit < 1 or not 1 <= args.max_new_tokens <= 2048:
        p.error('Require positive --limit and 1 <= --max-new-tokens <= 2048')
    if args.output.exists(): raise SystemExit('Refusing to overwrite evidence')
    rows=load_records(args.data,allow_unreviewed=True)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    from unsloth import FastVisionModel
    import torch
    if not torch.cuda.is_available(): raise SystemExit('CUDA required')
    model,processor=FastVisionModel.from_pretrained(model_name=str(args.adapter.resolve()),
        load_in_4bit=True,local_files_only=True,trust_remote_code=False)
    FastVisionModel.for_inference(model)
    model.eval()
    active=getattr(model,'active_adapters',None)
    if not active or not getattr(model,'peft_config',None): raise RuntimeError('Adapter is not active')
    results=run_inference(model,processor,rows[:args.limit],args.max_new_tokens,args.compare_base)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as out:
        json.dump({'artifact':str(args.adapter.resolve()),'gpu':torch.cuda.get_device_name(0),
                   'active_adapters':active,'compare_base':args.compare_base,
                   'generation':{'do_sample':False,'max_new_tokens':args.max_new_tokens,'seed':3407,
                                 'original_generation_eos':model.generation_config.eos_token_id,
                                 'explicit_eos_token_ids':stop_token_ids(model.generation_config.eos_token_id,
                                     getattr(processor,'tokenizer',processor).eos_token_id)},
                   'results':results,'note':'runtime activation; same processor/4bit base; not accuracy evaluation'},out,ensure_ascii=False,indent=2)
    print(json.dumps({'status':'inference_completed_pending_review','report':str(args.output)},ensure_ascii=False))


if __name__=='__main__': main()
