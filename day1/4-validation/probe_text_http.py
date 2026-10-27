"""Explicit local vLLM text control on captured development messages.

No training, deployment or automatic retries. Keep request intentions, raw HTTP
responses and final answers. Service finish_reason is not proof of EOS token IDs.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_app_text as app
from probe_teaching_holdout import checked_holdout


def generation_parameters(profile, seed):
    if profile == 'greedy':
        return dict(temperature=0, top_p=1.0, top_k=-1, presence_penalty=0,
                    repetition_penalty=1.0, seed=seed)
    if profile == 'official_non_thinking':
        # Qwen's model card, Best Practices: non-thinking reasoning tasks.
        return dict(temperature=1.0, top_p=1.0, top_k=40, presence_penalty=2.0,
                    repetition_penalty=1.0, seed=seed)
    raise ValueError('Unknown explicit sampler profile')


def run(args, rows, variants):
    import httpx
    results = []
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=180) as client:
        inventory = client.get(args.base_url.rstrip('/')+'/models')
        inventory.raise_for_status()
        available = {m['id'] for m in inventory.json()['data']}
        if not set(variants.values()) <= available:
            raise ValueError('Requested served identities unavailable')
        (args.output/'models.json').write_bytes(inventory.content)
        with (args.output/'responses.jsonl').open('x') as report:
            for row in rows:
                for seed in args.seeds:
                    for variant, model in variants.items():
                        payload = dict(model=model, messages=row['messages'], max_tokens=1024,
                            stream=False, chat_template_kwargs={'enable_thinking': False,
                            'preserve_thinking': True}, **generation_parameters(args.sampler_profile, seed))
                        fingerprint = hashlib.sha256(json.dumps(payload,ensure_ascii=False,
                                                               sort_keys=True).encode()).hexdigest()
                        name = row['id']+'-'+variant+'-'+str(seed)
                        intent = dict(id=row['id'], variant=variant, model=model, seed=seed,
                            request_sha256=fingerprint, status='prepared_no_confirmed_response')
                        with (args.output/(name+'-request.json')).open('x') as f:
                            json.dump(intent,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
                        start = time.monotonic()
                        raw = bytearray()
                        with client.stream('POST',args.base_url.rstrip('/')+'/chat/completions',json=payload) as response:
                            for chunk in response.iter_bytes():
                                raw.extend(chunk)
                                if len(raw)>256*1024:
                                    with (args.output/(name+'-oversized.bin')).open('xb') as f:f.write(raw)
                                    raise ValueError('Oversized response rejected')
                        with (args.output/(name+'-http.json')).open('xb') as f:f.write(raw)
                        response.raise_for_status()
                        body = json.loads(raw)
                        choice, message = body['choices'][0], body['choices'][0]['message']
                        answer = message.get('content')
                        if body.get('model')!=model or not isinstance(answer,str) or not answer.strip():
                            raise ValueError('Wrong model identity or empty final answer')
                        if message.get('tool_calls') or message.get('function_call'):
                            raise ValueError('Unexpected tools in text answer')
                        if '<think>' in answer or '</think>' in answer:
                            raise ValueError('Reasoning markers in final answer; response rejected')
                        result = dict(id=row['id'],variant=variant,model=model,seed=seed,
                            request_sha256=fingerprint,http_status=response.status_code,
                            answer=answer,finish_reason=choice.get('finish_reason'),
                            stop_reason=choice.get('stop_reason'), ended_with_eos=None,
                            normal_finish=choice.get('finish_reason')=='stop',
                            hit_output_limit=choice.get('finish_reason')=='length',
                            usage=body.get('usage'),elapsed_seconds=round(time.monotonic()-start,3),
                            review_status='pending_content_review')
                        results.append(result)
                        report.write(json.dumps(result,ensure_ascii=False)+'\n');report.flush();os.fsync(report.fileno())
                        print(json.dumps({k:result[k] for k in
                            ('id','variant','seed','finish_reason','elapsed_seconds')}),flush=True)
    return dict(generations=len(results),normal_stops=sum(r['normal_finish'] for r in results),
                truncated=sum(r['hit_output_limit'] for r in results))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for option in ('evaluation-root','capture','teaching-data','policy-file','output'):
        p.add_argument('--'+option,type=Path,required=True)
    p.add_argument('--base-url',required=True)
    p.add_argument('--base-model',required=True)
    p.add_argument('--adapter-model',required=True)
    p.add_argument('--variant',choices=('base','finetuned','both'),default='both')
    p.add_argument('--sampler-profile',choices=('greedy','official_non_thinking'),default='greedy')
    p.add_argument('--seeds',type=int,nargs='+',default=[3407])
    selected = p.add_mutually_exclusive_group(required=True)
    selected.add_argument('--case-ids',nargs='+')
    selected.add_argument('--all-development',action='store_true')
    p.add_argument('--run',action='store_true')
    args = p.parse_args()
    url = urlsplit(args.base_url)
    if (url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1')
            or url.path.rstrip('/')!='/v1' or url.username or url.password or url.query or url.fragment):
        p.error('Loopback HTTP /v1 endpoint required')
    if (args.output.exists() or args.output.is_symlink() or args.base_model==args.adapter_model
            or len(args.seeds)!=len(set(args.seeds)) or any(s<0 or s>2**31-1 for s in args.seeds)):
        p.error('New output, distinct model aliases and unique bounded seeds required')
    app.load_evaluation(args.evaluation_root)
    captured,capture = app.checked_messages(args.capture,args.evaluation_root)
    heldout,teaching = checked_holdout(args.teaching_data)
    pool = {r['id']:r for r in captured+heldout}
    ids = list(pool) if args.all_development else args.case_ids
    if (len(ids)!=len(set(ids)) or any(i not in pool or not re.fullmatch(r'[A-Za-z0-9_-]+',i) for i in ids)):
        p.error('Only unique captured development/teaching IDs allowed')
    policy = args.policy_file.read_text()
    if not 100<=len(policy)<=12000:p.error('Invalid policy size')
    rows = copy.deepcopy([pool[i] for i in ids])
    for row in rows:row['messages'][0]={'role':'system','content':policy}
    variants={'base':args.base_model,'finetuned':args.adapter_model}
    if args.variant!='both':variants={args.variant:variants[args.variant]}
    state=dict(status='files_checked_no_http_inference',cases=len(rows),case_ids=ids,
        variants=variants,sampler_profile=args.sampler_profile,seeds=args.seeds,
        generation=[generation_parameters(args.sampler_profile,s) for s in args.seeds],max_tokens=1024,
        capture_sha256=capture['messages_sha256'],teaching_sha256=teaching['validation_sha256'],
        policy_sha256=hashlib.sha256(policy.encode()).hexdigest(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        base_url=args.base_url,production_accepted=False,policy_deployed=False)
    if args.run:
        args.output.mkdir(parents=True,exist_ok=False)
        state['started_at']=datetime.now(timezone.utc).isoformat()
        try:
            state.update(run(args,rows,variants),status='completed_pending_content_review')
        except BaseException as exc:
            state.update(status='failed_no_automatic_retry',error=repr(exc));raise
        finally:
            state['finished_at']=datetime.now(timezone.utc).isoformat()
            (args.output/'run.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
    print(json.dumps(state,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
