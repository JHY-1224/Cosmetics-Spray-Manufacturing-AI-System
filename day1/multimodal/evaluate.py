"""Frozen visual regression with the application's real prompt and JSON schema.

Default validates files only; --run performs sequential CUDA inference. No training,
downloads, deployments, parameter search or automatic approval. References never
enter model messages. Raw results and partial failures are preserved per new run.
"""
import argparse
from collections import Counter
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unicodedata

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from assistant_app.backend.prompts import VISION, policy_metadata
from assistant_app.backend.schemas import VisionEvidence
from day1.generation import stop_token_ids


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_suite(folder, training_data):
    folder=Path(folder).resolve()
    metadata=folder/'cases.jsonl'
    card=json.loads((folder/'dataset-card.json').read_text())
    if digest(metadata)!=card['cases_sha256']:
        raise ValueError('Frozen evaluation cases changed')
    train_hashes={i['sha256'] for line in Path(training_data).read_text().splitlines() if line.strip()
                  for i in json.loads(line).get('images',[])}
    rows=[]; ids=set(); groups={}; hashes={}; signatures={}
    for line in metadata.read_text().splitlines():
        if not line.strip(): continue
        row=json.loads(line)
        if row['id'] in ids or row['split'] not in ('development','locked'):
            raise ValueError('Duplicate case or invalid split')
        ids.add(row['id'])
        if groups.setdefault(row['source_group'],row['split'])!=row['split']:
            raise ValueError('Source group leaks across evaluation splits')
        relative=Path(row['image']['path']); path=(folder/relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(folder):
            raise ValueError('Evaluation image path escape')
        sha=digest(path)
        if sha!=row['image']['sha256'] or sha in train_hashes:
            raise ValueError('Image changed or overlaps training images')
        if hashes.setdefault(sha,row['split'])!=row['split']:
            raise ValueError('Same image leaks across evaluation splits')
        signature=json.dumps([row['question'],row['expected_fields']],ensure_ascii=False,sort_keys=True)
        if signatures.setdefault(signature,row['split'])!=row['split']:
            raise ValueError('Same question and reference leak across evaluation splits')
        row['_path']=path
        rows.append(row)
    if len(rows)!=card['questions'] or len(hashes)!=card['images']:
        raise ValueError('Evaluation inventory count mismatch')
    return rows,card


def normalize(value):
    if value is None: return None
    return unicodedata.normalize('NFKC',str(value)).strip().replace('μ','µ')


def equivalent(actual, expected):
    a,b=normalize(actual),normalize(expected)
    if a==b: return True
    if a is None or b is None: return False
    try:
        x,y=Decimal(a),Decimal(b)
        return x.is_finite() and y.is_finite() and x==y
    except InvalidOperation:
        return False


def score(answer, case):
    """A match needs label, status, value AND unit; no credit for unrelated numbers."""
    try:
        parsed=VisionEvidence.model_validate_json(answer)
        if parsed.image_id!=case['image_id']: raise ValueError('Image ID mismatch')
    except ValueError:
        return dict(schema_valid=False,fields_correct=0,fields_total=len(case['expected_fields']),
                    critical_errors=sum(f['critical'] for f in case['expected_fields']),
                    case_pass=False,errors=['schema_or_image_identity_invalid'])
    errors=[]; correct=0; critical=0
    for expected in case['expected_fields']:
        # Questions name the requested labels verbatim; aliases can be reviewed
        # explicitly in a future version instead of silently accepting fuzzy matches.
        found=[f for f in parsed.extracted_fields if normalize(f.label)==normalize(expected['label'])]
        good=(len(found)==1 and found[0].status==expected['status']
              and equivalent(found[0].value_text,expected['value'])
              and normalize(found[0].unit_text)==normalize(expected['unit']))
        if good: correct+=1
        else:
            errors.append('field_mismatch:'+expected['label'])
            critical+=int(expected['critical'])
    if case['injection_warning_required'] and not parsed.warnings:
        errors.append('missing_injection_warning'); critical+=1
    return dict(schema_valid=True,fields_correct=correct,fields_total=len(case['expected_fields']),
                critical_errors=critical,case_pass=not errors,errors=errors)


def repeated_tail(ids):
    """Flag long consecutive token loops, not repeated JSON field names."""
    tail=ids[-256:]
    for size in range(1,17):
        for start in range(max(0,len(tail)-size*6+1)):
            pattern=tail[start:start+size]
            count=0
            for pos in range(start,len(tail)-size+1,size):
                if tail[pos:pos+size]!=pattern: break
                count+=1
            if count>=6 and count*size>=24: return True
    return False


def messages(case):
    from PIL import Image
    with Image.open(case['_path']) as image:
        pixels=image.convert('RGB')
    question=json.dumps({'image_id':case['image_id'],'order':1,'question':case['question']},ensure_ascii=False)
    return [{'role':'system','content':VISION},
            {'role':'user','content':[{'type':'text','text':question},{'type':'image','image':pixels}]}]


def summarize(results, expected_cases, variants):
    groups={}
    for variant in variants:
        selected=[r for r in results if r['variant']==variant]
        total=sum(r['score']['fields_total'] for r in selected)
        correct=sum(r['score']['fields_correct'] for r in selected)
        complete=len(selected)==expected_cases
        # Provisional regression minimum; human correctness and real factory
        # testing are independent gates and never inferred from these numbers.
        eligible=(complete and all(r['score']['schema_valid'] and not r['hit_output_limit']
                                  and not r['repeated_output'] and r['ended_with_eos'] for r in selected)
                  and sum(r['score']['critical_errors'] for r in selected)==0
                  and total>0 and correct/total>=0.95)
        groups[variant]=dict(cases=len(selected),expected_cases=expected_cases,complete=complete,
            schema_valid=sum(r['score']['schema_valid'] for r in selected),
            fields_correct=correct,fields_total=total,field_accuracy=correct/total if total else None,
            critical_errors=sum(r['score']['critical_errors'] for r in selected),
            truncated=sum(r['hit_output_limit'] for r in selected),
            repeated=sum(r['repeated_output'] for r in selected),
            eligible_for_human_review=eligible,quality_acceptance='pending_human_review')
    return groups


def run(args, rows, card, expected_count):
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    from unsloth import FastVisionModel
    import torch
    if not torch.cuda.is_available(): raise RuntimeError('CUDA required')
    model,processor=FastVisionModel.from_pretrained(model_name=str(args.adapter.resolve()),
        load_in_4bit=True,local_files_only=True,trust_remote_code=False,max_seq_length=8192)
    FastVisionModel.for_inference(model); model.eval()
    active=getattr(model,'active_adapters',None)
    if not active or not getattr(model,'peft_config',None): raise RuntimeError('Active adapter required')
    variants=('base','finetuned') if args.variant=='both' else (args.variant,)
    if 'base' in variants and not callable(getattr(model,'disable_adapter',None)):
        raise RuntimeError('Adapter cannot be disabled for comparison')
    tokenizer=getattr(processor,'tokenizer',processor)
    eos=model.generation_config.eos_token_id
    stop=stop_token_ids(eos,tokenizer.eos_token_id)
    results=[]
    with (args.output/'responses.jsonl').open('x') as report:
        for case in rows:
            inputs=processor.apply_chat_template(messages(case),tokenize=True,add_generation_prompt=True,
                return_dict=True,return_tensors='pt').to(model.device)
            if 'pixel_values' not in inputs: raise RuntimeError('Processor omitted image pixels')
            count=inputs['input_ids'].shape[-1]
            if count+args.max_new_tokens>8192: raise RuntimeError('Context budget exceeded; no truncation')
            for variant in variants:
                torch.manual_seed(args.seed)
                torch.cuda.synchronize(); started=time.monotonic()
                context=model.disable_adapter() if variant=='base' else nullcontext()
                with context,torch.inference_mode():
                    generated=model.generate(**inputs,max_new_tokens=args.max_new_tokens,
                        do_sample=False,eos_token_id=stop)
                torch.cuda.synchronize()
                ids=generated[0,count:].tolist()
                answer=tokenizer.decode(ids,skip_special_tokens=True)
                result=dict(id=case['id'],variant=variant,category=case['category'],
                    prompt_tokens=count,completion_tokens=len(ids),
                    elapsed_seconds=round(time.monotonic()-started,3),
                    ended_with_eos=bool(ids and ids[-1] in stop),hit_output_limit=len(ids)>=args.max_new_tokens,
                    repeated_output=repeated_tail(ids),answer=answer,score=score(answer,case))
                results.append(result)
                report.write(json.dumps(result,ensure_ascii=False)+'\n'); report.flush(); os.fsync(report.fileno())
                summary=summarize(results,expected_count,variants)
                (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
                print(json.dumps({k:result[k] for k in ('id','variant','completion_tokens','hit_output_limit','repeated_output','score')},ensure_ascii=False),flush=True)
    return dict(status='completed_pending_human_review',groups=summarize(results,expected_count,variants),
        gpu=torch.cuda.get_device_name(0),active_adapters=active,artifact=str(args.adapter.resolve()),
        generation=dict(do_sample=False,max_new_tokens=args.max_new_tokens,seed=args.seed,
                        original_generation_eos=eos,explicit_eos_token_ids=stop),
        prompt_policy=policy_metadata(),cases_sha256=card['cases_sha256'],
        transport='Unsloth generate; JSON grammar not constrained. HTTP/vLLM integration must be tested separately.',
        comparison='same adapter processor and quantized base; LoRA disabled/enabled',production_accepted=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite',type=Path,default=ROOT/'data/vision-eval-v2')
    p.add_argument('--training-data',type=Path,default=ROOT/'local_training/qwen-vl-20261026/metadata.jsonl')
    p.add_argument('--adapter',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--split',choices=['development','locked'],default='development')
    p.add_argument('--unlock-locked',action='store_true',help='Final sealed test after development decisions are frozen')
    p.add_argument('--variant',choices=['both','base','finetuned'],default='both')
    p.add_argument('--limit',type=int)
    p.add_argument('--max-new-tokens',type=int,default=768)
    p.add_argument('--seed',type=int,default=3407)
    p.add_argument('--run',action='store_true')
    args=p.parse_args()
    if args.split=='locked' and not args.unlock_locked: p.error('Sealed test requires --unlock-locked')
    if args.limit is not None and args.limit<1: p.error('Positive --limit required')
    if not 64<=args.max_new_tokens<=2048: p.error('Require 64..2048 output tokens')
    if args.output.exists(): p.error('Refusing to overwrite an earlier test run')
    rows,card=load_suite(args.suite,args.training_data)
    pool=[r for r in rows if r['split']==args.split]
    selected=pool[:args.limit] if args.limit else pool
    args.output.mkdir(parents=True,exist_ok=False)
    state=dict(status='files_checked_no_inference',split=args.split,cases=len(selected),
        expected_cases=len(pool),categories=dict(Counter(r['category'] for r in selected)),
        cases_sha256=card['cases_sha256'],production_accepted=False)
    try:
        if args.run: state.update(run(args,selected,card,len(pool)))
    except (Exception,KeyboardInterrupt) as exc:
        state.update(status='failed',error_type=type(exc).__name__,error=str(exc)); raise
    finally:
        (args.output/'run.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
    print(json.dumps(state,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
