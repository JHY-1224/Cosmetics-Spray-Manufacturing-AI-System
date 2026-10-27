"""Replay actual training prompts and their preassigned parameter holdout.

The final assistant target is saved only for review; it never enters a request.
Training replay measures fit, not independent model quality.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline import verified_rows, read_messages
import probe_text_http as http_probe


def prepare(data, plan, policy):
    train, manifest = verified_rows(data)
    val_path = data/'validation.messages.jsonl'
    if hashlib.sha256(val_path.read_bytes()).hexdigest()!=manifest['validation_sha256']:
        raise ValueError('Validation file changed')
    validation = read_messages(val_path)
    if len(validation)!=manifest['validation_rows']:
        raise ValueError('Validation row count changed')
    if {r['id'] for r in train}&{r['id'] for r in validation}:
        raise ValueError('Training and validation overlap')
    pools={'train':{r['id']:r for r in train},'validation':{r['id']:r for r in validation}}
    rows, references = [], []
    for split,ids in plan.items():
        if split not in pools or len(ids)!=len(set(ids)):raise ValueError('Invalid split/selection')
        for ident in ids:
            source=pools[split][ident]
            if source['split']!=split or source['messages'][-1]['role']!='assistant':raise ValueError('Invalid source roles')
            messages=[dict(m) for m in source['messages'][:-1]]
            if split=='train' and messages[0]!={'role':'system','content':policy}:
                raise ValueError('Training prompt policy differs; replay must be exact')
            messages[0]={'role':'system','content':policy}
            rows.append(dict(id=ident,messages=messages))
            references.append(dict(id=ident,split=split,reference=source['messages'][-1]['content'],expected=source.get('expected')))
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('Duplicate replay IDs')
    return rows,references,manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('data','plan','policy-file','output'):p.add_argument('--'+key,type=Path,required=True)
    for key in ('base-url','base-model','adapter-model'):p.add_argument('--'+key,required=True)
    p.add_argument('--run',action='store_true')
    args=p.parse_args()
    url=urlsplit(args.base_url)
    if (url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1') or url.path.rstrip('/')!='/v1'
            or url.username or url.password or url.query or url.fragment):p.error('Require loopback /v1')
    if args.output.exists() or args.output.is_symlink() or args.base_model==args.adapter_model:p.error('Require new report and distinct aliases')
    policy=args.policy_file.read_text()
    rows,references,manifest=prepare(args.data,json.loads(args.plan.read_text()),policy)
    args.seeds,args.sampler_profile=[3407],'greedy'
    state=dict(status='files_checked_no_inference',cases=len(rows),
        split_counts={s:sum(r['split']==s for r in references) for s in ('train','validation')},
        train_sha256=manifest['train_sha256'],validation_sha256=manifest['validation_sha256'],
        policy_sha256=hashlib.sha256(policy.encode()).hexdigest(),plan_sha256=hashlib.sha256(args.plan.read_bytes()).hexdigest(),
        training_replay_is_independent_quality=False,reference_answers_sent=False,production_accepted=False)
    if args.run:
        args.output.mkdir(parents=True,exist_ok=False)
        (args.output/'references.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in references))
        state['started_at']=datetime.now(timezone.utc).isoformat()
        try:state.update(http_probe.run(args,rows,{'base':args.base_model,'finetuned':args.adapter_model}),status='completed_pending_reference_review')
        except BaseException as exc:state.update(status='failed_no_automatic_retry',error=repr(exc));raise
        finally:
            state['finished_at']=datetime.now(timezone.utc).isoformat()
            (args.output/'run.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
    print(json.dumps(state,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
