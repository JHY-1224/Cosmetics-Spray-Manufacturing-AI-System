"""Small, explicit thinking-mode control; expose final answers only.

Uses existing captured application messages plus parameter-disjoint teaching
holdouts. No target enters generation. Raw token IDs are retained privately for
diagnosis; internal reasoning text is never decoded into the answer or console.
"""
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_app_text as app
from probe_teaching_holdout import checked_holdout


def final_tokens(ids, end_think):
    if ids.count(end_think) != 1:
        return [], None
    index = ids.index(end_think)
    return ids[index+1:], index+1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evaluation-root', type=Path, required=True)
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--teaching-data', type=Path, required=True)
    p.add_argument('--adapter', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--case-ids', nargs='+', required=True)
    p.add_argument('--variant', choices=('base', 'finetuned', 'both'), default='both')
    p.add_argument('--run', action='store_true')
    args = p.parse_args()
    if args.output.exists() or args.output.is_symlink():
        p.error('Refusing to overwrite report')
    evaluation = app.load_evaluation(args.evaluation_root)
    captured, capture_metadata = app.checked_messages(args.capture, args.evaluation_root)
    heldout, teaching_metadata = checked_holdout(args.teaching_data)
    pool = {r['id']: r for r in captured + heldout}
    if len(args.case_ids) != len(set(args.case_ids)) or any(i not in pool for i in args.case_ids):
        p.error('Only unique development/teaching-holdout IDs allowed')
    rows = [pool[i] for i in args.case_ids]
    state = dict(status='files_checked_no_inference', cases=len(rows), case_ids=args.case_ids,
        variant=args.variant, capture_sha256=capture_metadata['messages_sha256'],
        teaching_holdout_sha256=teaching_metadata['validation_sha256'],
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        production_accepted=False, generation=dict(enable_thinking=True, max_length=8192,
            max_new_tokens=2048, do_sample=False, seed=3407), adapter=str(args.adapter.resolve()))
    if not args.run:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=False)
    state['started_at'] = datetime.now(timezone.utc).isoformat()
    try:
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
            HF_HUB_DISABLE_TELEMETRY='1', WANDB_DISABLED='true')
        from unsloth import FastModel
        import torch
        model, processor = FastModel.from_pretrained(model_name=str(args.adapter.resolve()),
            max_seq_length=8192, load_in_4bit=True, local_files_only=True, trust_remote_code=False)
        tokenizer = getattr(processor, 'tokenizer', processor)
        if not getattr(model, 'active_adapters', None) or not callable(getattr(model, 'disable_adapter', None)):
            raise RuntimeError('Verified active adapter and disable control required')
        FastModel.for_inference(model)
        model.eval()
        end_think = tokenizer.convert_tokens_to_ids('</think>')
        if tokenizer.convert_ids_to_tokens(end_think) != '</think>':
            raise ValueError('Native reasoning boundary unavailable')
        stop = app.stop_token_ids(model.generation_config.eos_token_id, tokenizer.eos_token_id)
        variants = ('base', 'finetuned') if args.variant == 'both' else (args.variant,)
        results = []
        with (args.output/'responses.jsonl').open('x') as report:
            for row in rows:
                text = tokenizer.apply_chat_template(row['messages'], tokenize=False,
                    add_generation_prompt=True, enable_thinking=True, preserve_thinking=True)
                if not text.rstrip().endswith('<think>'):
                    raise ValueError('Native thinking generation prefix not established')
                inputs = tokenizer(text, return_tensors='pt', add_special_tokens=False)
                count = inputs['input_ids'].shape[-1]
                if count + 2048 > 8192:
                    raise ValueError('Context budget exceeded')
                inputs = {k: v.to(model.device) for k, v in inputs.items()}
                for variant in variants:
                    torch.manual_seed(3407)
                    start = time.monotonic()
                    context = model.disable_adapter() if variant == 'base' else nullcontext()
                    with context, torch.inference_mode():
                        generated = model.generate(**inputs, max_new_tokens=2048, do_sample=False,
                            eos_token_id=stop, pad_token_id=tokenizer.pad_token_id)
                    torch.cuda.synchronize()
                    ids = generated[0,count:].tolist()
                    final, reasoning_count = final_tokens(ids, end_think)
                    answer = tokenizer.decode(final, skip_special_tokens=True) if reasoning_count else ''
                    if '<think>' in answer or '</think>' in answer:
                        raise ValueError('Reasoning boundary leaked into final answer')
                    result = dict(id=row['id'], variant=variant, prompt_tokens=count,
                        completion_tokens=len(ids), reasoning_tokens=reasoning_count,
                        final_answer_tokens=len(final), answer=answer,
                        ended_with_eos=bool(ids and ids[-1] in stop), hit_output_limit=len(ids)>=2048,
                        repeated_output=evaluation.repeated_tail(ids),
                        elapsed_seconds=round(time.monotonic()-start,3),
                        generated_token_ids=ids, review_status='pending_content_review')
                    results.append(result)
                    report.write(json.dumps(result, ensure_ascii=False)+'\n');report.flush();os.fsync(report.fileno())
                    print(json.dumps({k:v for k,v in result.items() if k not in ('answer','generated_token_ids')}),flush=True)
        state.update(status='completed_pending_content_review', generations=len(results),
            eos_ended=sum(r['ended_with_eos'] for r in results), truncated=sum(r['hit_output_limit'] for r in results),
            final_answer_available=sum(bool(r['answer']) for r in results))
    except BaseException as exc:
        state.update(status='failed',error=repr(exc))
        raise
    finally:
        state['finished_at'] = datetime.now(timezone.utc).isoformat()
        (args.output/'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
