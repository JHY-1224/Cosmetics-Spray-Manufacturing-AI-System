"""Bounded reference-loss and prompt/input controls; never updates weights.

Default checks public development data only. --run uses an existing CUDA runtime,
records paired base/adapter results, and refuses to overwrite earlier evidence.
"""
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'day1'))
from pipeline import verified_rows, read_messages, tokenize_example, audit_text_labels
from day1.generation import stop_token_ids

SHORT_POLICY = ('你是包材工艺资料核对助手。只回答用户当前问题，只使用输入提供的事实。'
                '保留名称、数值、单位、符号、表头和行列对应；未提供的事实保持未知。'
                '计算列出简短公式和最终结果，检查分项、总量和单位一致。'
                '不要增加与问题无关的解释或待核查清单，不替原文改错。'
                '不得声称执行、批准、确认唯一根因、结案或正式放行。')
CONTROL_IDS = ('S-cdfba606e0e1004247-canonical', 'S-bfed82d3b7db875642-canonical',
               'balanced-percentage-validation-064', 'balanced-sample_sufficiency-train-000')


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def json_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def inputs_only(row, policy=None):
    if row['messages'][-1]['role'] != 'assistant':
        raise ValueError('Reference must be the final assistant turn')
    messages = [dict(m) for m in row['messages'][:-1]]
    if messages[-1]['role'] != 'user' or messages[0]['role'] != 'system':
        raise ValueError('Require system and user input')
    if policy is not None:
        messages[0] = {'role': 'system', 'content': policy}
    return messages


def table_excerpt(question):
    """Reformat original Table 6 cells, without references or invented values."""
    lines = [line.strip() for line in question.splitlines() if line.strip()]
    column_start = lines.index('Variable') + 1
    boundary = lines.index('I-calorimetric trace, 20 °C/min')
    columns = lines[column_start:boundary]
    if len(columns) != 11 or len(set(columns)) != 11:
        raise ValueError('Unexpected Table 6 columns')
    group = lines.index('II-calorimetric trace after rapid cooling, 20 °C/min')
    row_start = lines.index('Δcp (J/g°C)', group)
    cells = lines[row_start + 1:row_start + 1 + len(columns)]
    if len(cells) != len(columns) or lines[row_start + 1 + len(columns)] != 'Tm (°C)':
        raise ValueError('Unexpected Table 6 row boundary')
    prefix = lines[:column_start - 1]
    return '\n'.join(prefix + [
        '以下仅重排原文第二次扫描Δcp这一行；其他原表行未展示。',
        lines[group], '| 原列名 | Δcp (J/g°C)原单元格 |', '|---|---|',
        *[f'| {name} | {value} |' for name, value in zip(columns, cells, strict=True)]])


def prepare(data, plan):
    train, manifest = verified_rows(data)
    validation_path = data / 'validation.messages.jsonl'
    if sha(validation_path.read_bytes()) != manifest['validation_sha256']:
        raise ValueError('Validation data changed')
    validation = read_messages(validation_path)
    if len(validation) != manifest['validation_rows']:
        raise ValueError('Validation row count changed')
    pools = {'train': {r['id']: r for r in train}, 'validation': {r['id']: r for r in validation}}
    selected = []
    for split, ids in plan.items():
        if split not in pools or len(ids) != len(set(ids)):
            raise ValueError('Invalid replay plan')
        selected.extend(pools[split][ident] for ident in ids)
    if len({r['id'] for r in selected}) != len(selected):
        raise ValueError('Duplicate diagnostic IDs')
    all_rows = {r['id']: r for r in train + validation}
    controls = []
    for ident in CONTROL_IDS:
        for policy in ('original', 'short'):
            controls.append(dict(id=ident, control=policy,
                messages=inputs_only(all_rows[ident], SHORT_POLICY if policy == 'short' else None)))
    ident = 'S-cdfba606e0e1004247-assistant-json'
    controls.append(dict(id=ident, control='envelope_short', messages=inputs_only(all_rows[ident], SHORT_POLICY)))
    ident = 'S-bfed82d3b7db875642-canonical'
    messages = inputs_only(all_rows[ident], SHORT_POLICY)
    messages[-1]['content'] = table_excerpt(messages[-1]['content'])
    controls.append(dict(id=ident, control='table_excerpt_short', messages=messages))
    return selected, controls, manifest


def run(args, references, controls, manifest):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', WANDB_DISABLED='true')
    from unsloth import FastModel
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('Existing cloud CUDA environment required')
    active = __import__('subprocess').check_output(
        ['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader'], text=True).strip()
    other_pids = [pid for pid in active.splitlines() if pid.strip() != str(os.getpid())]
    if other_pids:
        raise RuntimeError('GPU is already used; no processes were stopped: ' + ','.join(other_pids))
    adapter_config = json.loads((args.adapter / 'adapter_config.json').read_text())
    if Path(adapter_config['base_model_name_or_path']).resolve() != args.base_dir.resolve():
        raise ValueError('Adapter base path differs')
    if sha((args.adapter / 'adapter_model.safetensors').read_bytes()) != args.adapter_sha256:
        raise ValueError('Adapter weight identity differs')
    model, processor = FastModel.from_pretrained(model_name=str(args.adapter), max_seq_length=4096,
        dtype=torch.bfloat16, load_in_4bit=False, load_in_8bit=False,
        local_files_only=True, trust_remote_code=False)
    tokenizer = getattr(processor, 'tokenizer', processor)
    FastModel.for_inference(model)
    model.eval()
    if not getattr(model, 'active_adapters', None) or not callable(getattr(model, 'disable_adapter', None)):
        raise RuntimeError('Active switchable LoRA required')
    if getattr(model, 'is_loaded_in_4bit', False) or getattr(model, 'is_loaded_in_8bit', False):
        raise RuntimeError('BF16 control was unexpectedly quantized')
    stop = stop_token_ids(model.generation_config.eos_token_id, tokenizer.eos_token_id)
    environment = dict(packages={n: importlib.metadata.version(n) for n in
        ('unsloth', 'transformers', 'torch', 'peft')}, gpu=torch.cuda.get_device_name(0),
        active_adapters=model.active_adapters, explicit_eos_token_ids=stop,
        precision='BF16 base with original saved LoRA', train_sha256=manifest['train_sha256'])
    (args.output / 'environment.json').write_text(json.dumps(environment, ensure_ascii=False, indent=2))
    nll = []
    with (args.output / 'reference-loss.jsonl').open('x') as out:
        for row in references:
            item = tokenize_example(tokenizer, row['messages'], 4096)
            audit = audit_text_labels(tokenizer, row['messages'], item)
            tensors = {k: torch.tensor([v], device=model.device) for k, v in item.items()}
            for variant in ('base', 'finetuned'):
                context = model.disable_adapter() if variant == 'base' else nullcontext()
                with context, torch.inference_mode():
                    loss = float(model(**tensors).loss.detach().float().item())
                if not __import__('math').isfinite(loss):
                    raise ValueError('Nonfinite reference loss')
                record = dict(id=row['id'], split=row['split'], variant=variant,
                    nll=loss, target_tokens=audit['supervised_tokens'], supervision=audit,
                    input_labels_sha256=sha(json.dumps(item, sort_keys=True).encode()))
                nll.append(record)
                out.write(json.dumps(record) + '\n'); out.flush(); os.fsync(out.fileno())
                print(json.dumps(dict(phase='reference_loss', completed=len(nll), id=row['id'], variant=variant)), flush=True)
            del tensors
    with (args.output / 'responses.jsonl').open('x') as out:
        for row in controls:
            prompt = tokenizer.apply_chat_template(row['messages'], tokenize=False,
                add_generation_prompt=True, enable_thinking=False, preserve_thinking=True)
            inputs = tokenizer(prompt, return_tensors='pt', add_special_tokens=False)
            count = inputs['input_ids'].shape[-1]
            if count + args.max_new_tokens > 4096:
                raise ValueError('Context budget exceeded; no truncation')
            inputs = {k: v.to(model.device) for k, v in inputs.items()}
            for variant in ('base', 'finetuned'):
                context = model.disable_adapter() if variant == 'base' else nullcontext()
                torch.manual_seed(3407); started = time.monotonic()
                with context, torch.inference_mode():
                    generated = model.generate(**inputs, max_new_tokens=args.max_new_tokens,
                        do_sample=False, repetition_penalty=1.0,
                        eos_token_id=stop, pad_token_id=tokenizer.pad_token_id)
                ids = generated[0, count:].tolist()
                answer = tokenizer.decode(ids, skip_special_tokens=True)
                if '<think>' in answer or '</think>' in answer:
                    raise ValueError('Unexpected reasoning markers; final answer not accepted')
                record = dict(id=row['id'], control=row['control'], variant=variant, answer=answer,
                    completion_tokens=len(ids), ended_with_eos=bool(ids and ids[-1] in stop),
                    hit_output_limit=len(ids) >= args.max_new_tokens,
                    input_sha256=sha(json.dumps(row['messages'], ensure_ascii=False, sort_keys=True).encode()),
                    elapsed_seconds=round(time.monotonic() - started, 3), review_status='pending')
                out.write(json.dumps(record, ensure_ascii=False) + '\n'); out.flush(); os.fsync(out.fileno())
                print(json.dumps({k: v for k, v in record.items() if k not in ('answer',)}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('data', 'plan', 'adapter', 'base-dir', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--adapter-sha256', required=True)
    p.add_argument('--max-new-tokens', type=int, default=512)
    p.add_argument('--run', action='store_true')
    args = p.parse_args()
    if args.output.exists() or args.output.is_symlink() or not 64 <= args.max_new_tokens <= 1024:
        p.error('Require new evidence directory and bounded output')
    references, controls, manifest = prepare(args.data, json.loads(args.plan.read_text()))
    state = dict(status='checked_no_inference', reference_cases=len(references),
        planned_loss_forwards=2 * len(references), planned_generations=2 * len(controls),
        script_sha256=sha(Path(__file__).read_bytes()), train_sha256=manifest['train_sha256'],
        validation_sha256=manifest['validation_sha256'], short_policy_sha256=sha(SHORT_POLICY.encode()),
        production_accepted=False, optimizer_steps=0)
    if args.run:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'controls.json').write_text(json.dumps(controls, ensure_ascii=False, indent=2))
        state['started_at'] = datetime.now(timezone.utc).isoformat()
        try:
            run(args, references, controls, manifest)
            state['status'] = 'completed_pending_review'
        except BaseException as exc:
            state.update(status='failed_no_automatic_retry', error=repr(exc)); raise
        finally:
            state['finished_at'] = datetime.now(timezone.utc).isoformat()
            (args.output / 'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
