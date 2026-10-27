"""Evaluate the 9B adapter using captured actual application messages.

Explicit --run is required for GPU inference. Frozen development cases and
application system policy are checked before loading. No training or deployment.
"""
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'day1/multimodal'))
from verify_official_base import load_evaluation
from assistant_app.backend.prompts import SYSTEM, policy_metadata
from assistant_app.backend import runtime_prompts
from day1.generation import stop_token_ids

CASES_SHA = '960bcba3298b40e969ed75f754f29963211a8ecf4aecff61e561af09e9849661'


def captured_policy(metadata):
    """Validate legacy captures without silently rewriting their system policy."""
    saved = metadata.get('policy', {})
    legacy = policy_metadata()
    current = runtime_prompts.policy_metadata()
    if saved.get('version') == legacy['version']:
        expected, text = legacy, SYSTEM
    elif saved.get('version') == current['version']:
        expected, text = current, runtime_prompts.SYSTEM
    else:
        raise ValueError('Unknown captured application policy version')
    if any(saved.get(key) != expected[key] for key in ('text_sha256', 'vision_sha256')):
        raise ValueError('Captured application policy hash changed')
    return text


def checked_messages(folder, kit):
    metadata = json.loads((folder / 'capture.json').read_text())
    system = captured_policy(metadata)
    path = folder / 'messages.jsonl'
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata['messages_sha256']:
        raise ValueError('Captured messages changed')
    cases_path = kit / 'data/text-eval-v1/development.json'
    if hashlib.sha256(cases_path.read_bytes()).hexdigest() != CASES_SHA:
        raise ValueError('Frozen text development cases changed')
    cases = json.loads(cases_path.read_text())
    expected = {c['id']: c for c in cases if c['split'] == 'development'}
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if len(rows) != 18 or len({r['id'] for r in rows}) != 18 or {r['id'] for r in rows} != set(expected):
        raise ValueError('Require all 18 unique development cases')
    for row in rows:
        messages = row['messages']
        if row['split'] != 'development' or len(messages) != 2:
            raise ValueError('Require single-turn development captures')
        if messages[0] != {'role': 'system', 'content': system} or messages[1]['role'] != 'user':
            raise ValueError('Application system policy or user role changed')
        current = json.loads(messages[1]['content'])
        if current['user_original_question'] != expected[row['id']]['prompt'] or current['vision_evidence']:
            raise ValueError('Application question differs from frozen development case')
    return rows, metadata


def infer(args, rows, evaluation):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', WANDB_DISABLED='true')
    from unsloth import FastModel
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA required')
    precision = getattr(args, 'precision', '4bit')
    if precision not in ('4bit', 'bf16'):
        raise ValueError('Unsupported diagnostic precision')
    load_kwargs = {'dtype': torch.bfloat16} if precision == 'bf16' else {}
    model, processor = FastModel.from_pretrained(model_name=str(args.adapter.resolve()),
        max_seq_length=4096, load_in_4bit=precision == '4bit', load_in_8bit=False,
        local_files_only=True, trust_remote_code=False, **load_kwargs)
    tokenizer = getattr(processor, 'tokenizer', processor)
    FastModel.for_inference(model)
    model.eval()
    loaded_4bit = bool(getattr(model, 'is_loaded_in_4bit', False))
    loaded_8bit = bool(getattr(model, 'is_loaded_in_8bit', False))
    quantized_parameters = sum(getattr(p, 'quant_state', None) is not None for p in model.parameters())
    base_dtypes = sorted({str(p.dtype) for n, p in model.named_parameters() if 'lora_' not in n})
    if loaded_4bit != (precision == '4bit') or loaded_8bit:
        raise RuntimeError('Actual loaded quantization differs from requested diagnostic precision')
    if precision == 'bf16' and (quantized_parameters or 'torch.bfloat16' not in base_dtypes
                                or any(t in base_dtypes for t in ('torch.uint8', 'torch.int8'))):
        raise RuntimeError('Unquantized BF16 parameter structure was not established')
    if not getattr(model, 'peft_config', None) or not getattr(model, 'active_adapters', None):
        raise RuntimeError('Active adapter required; refusing a base fallback')
    variants = ('base', 'finetuned') if args.variant == 'both' else (args.variant,)
    if 'base' in variants and not callable(getattr(model, 'disable_adapter', None)):
        raise RuntimeError('Cannot disable adapter for the application control')
    eos = model.generation_config.eos_token_id
    stop = stop_token_ids(eos, tokenizer.eos_token_id, args.stop_mode)
    results = []
    with (args.output / 'responses.jsonl').open('x', encoding='utf-8') as f:
        for row in rows:
            text = tokenizer.apply_chat_template(row['messages'], tokenize=False,
                add_generation_prompt=True, enable_thinking=False, preserve_thinking=True)
            inputs = tokenizer(text, return_tensors='pt', add_special_tokens=False)
            count = inputs['input_ids'].shape[-1]
            if count + 1024 > 4096:
                raise ValueError('Context budget exceeded; refusing truncation: ' + row['id'])
            inputs = {k: v.to(model.device) for k, v in inputs.items()}
            for variant in variants:
                torch.manual_seed(3407)
                torch.cuda.synchronize()
                start = time.monotonic()
                context = model.disable_adapter() if variant == 'base' else nullcontext()
                with context, torch.inference_mode():
                    generated = model.generate(**inputs, max_new_tokens=1024, do_sample=False,
                        pad_token_id=tokenizer.pad_token_id, eos_token_id=stop)
                torch.cuda.synchronize()
                ids = generated[0, count:].tolist()
                result = dict(id=row['id'], variant=variant, prompt_tokens=count,
                    completion_tokens=len(ids), elapsed_seconds=round(time.monotonic() - start, 3),
                    ended_with_eos=bool(ids and ids[-1] in stop), hit_output_limit=len(ids) >= 1024,
                    repeated_output=evaluation.repeated_tail(ids),
                    generated_token_ids=ids,
                    special_token_positions=[dict(position=i, token_id=v,
                        token=tokenizer.convert_ids_to_tokens(v)) for i, v in enumerate(ids)
                        if v in tokenizer.all_special_ids],
                    answer=tokenizer.decode(ids, skip_special_tokens=True), review_status='pending_review')
                results.append(result)
                f.write(json.dumps(result, ensure_ascii=False) + '\n')
                f.flush()
                os.fsync(f.fileno())
                print(json.dumps({k: v for k, v in result.items()
                                  if k not in ('answer', 'generated_token_ids')}, ensure_ascii=False), flush=True)
    return dict(status='inference_completed_pending_review', cases=len(rows), generations=len(results),
        eos_ended=sum(r['ended_with_eos'] for r in results),
        truncated=sum(r['hit_output_limit'] for r in results),
        repeated=sum(r['repeated_output'] for r in results),
        generation=dict(do_sample=False, max_new_tokens=1024, seed=3407,
                        enable_thinking=False, stop_mode=args.stop_mode,
                        original_generation_eos=eos, tokenizer_eos=tokenizer.eos_token_id,
                        explicit_eos_token_ids=stop),
        precision=dict(requested=precision, loaded_in_4bit=loaded_4bit,
                       loaded_in_8bit=loaded_8bit, quantized_parameters=quantized_parameters,
                       base_parameter_dtypes=base_dtypes),
        transport='Unsloth ' + precision + ' inference with actual app messages; HTTP/vLLM still separate')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evaluation-root', type=Path, required=True)
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--adapter', type=Path, required=True)
    p.add_argument('--base-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--variant', choices=('finetuned', 'base', 'both'), default='finetuned')
    p.add_argument('--case-ids', nargs='+')
    p.add_argument('--stop-mode', choices=('chat', 'legacy'), default='chat')
    p.add_argument('--precision', choices=('4bit', 'bf16'), default='4bit')
    p.add_argument('--run', action='store_true')
    args = p.parse_args()
    if args.output.exists() or args.output.is_symlink():
        p.error('Refusing to overwrite an earlier report')
    evaluation = load_evaluation(args.evaluation_root)
    rows, capture = checked_messages(args.capture, args.evaluation_root)
    if args.case_ids:
        pool = {r['id']: r for r in rows}
        if len(args.case_ids) != len(set(args.case_ids)) or any(i not in pool for i in args.case_ids):
            p.error('Only unique captured development case IDs are allowed')
        rows = [pool[i] for i in args.case_ids]
    spec = importlib.util.spec_from_file_location('frozen_text_adapter_check',
        args.evaluation_root / 'day1/4-validation/3-test-adapter.py')
    adapter_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter_check)
    artifact = adapter_check.inspect_adapter(args.adapter.resolve(), args.base_dir.resolve())
    state = dict(status='files_and_messages_checked_no_inference', cases=len(rows),
        case_ids=[r['id'] for r in rows], variant=args.variant,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        capture_sha256=capture['messages_sha256'], cases_sha256=CASES_SHA,
        artifact=artifact, prompt_policy=policy_metadata(), production_accepted=False)
    if args.run:
        args.output.mkdir(parents=True, exist_ok=False)
        state['started_at'] = datetime.now(timezone.utc).isoformat()
        try:
            state.update(infer(args, rows, evaluation))
        except (Exception, KeyboardInterrupt) as exc:
            state.update(status='failed', error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            state['finished_at'] = datetime.now(timezone.utc).isoformat()
            (args.output / 'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
