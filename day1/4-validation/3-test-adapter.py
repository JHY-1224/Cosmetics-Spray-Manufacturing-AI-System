"""Offline adapter checks and paired smoke inference in the existing training env.

Default: inspect files only. --run explicitly loads CUDA; never trains, merges,
downloads, changes the adapter, or connects to the business application.
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
from day1.generation import stop_token_ids

CASES = Path(__file__).with_name('smoke-cases.json')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def file_digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def load_cases(path):
    cases = read_json(path)
    ids = set()
    if not isinstance(cases, list) or not cases:
        raise ValueError('测试题必须是非空列表')
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('id'), str) or case['id'] in ids:
            raise ValueError('测试题ID缺失或重复')
        ids.add(case['id'])
        if not isinstance(case.get('prompt'), str) or not case['prompt'].strip():
            raise ValueError('测试题prompt为空')
        if not isinstance(case.get('review'), list) or not case['review']:
            raise ValueError('测试题缺少人工验收要点')
    return cases


def inspect_adapter(adapter, base):
    required = ('adapter_config.json', 'adapter_model.safetensors', 'tokenizer.json',
                'tokenizer_config.json', 'chat_template.jinja')
    for name in required:
        path = adapter / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'缺少或为空：{path}')
    config = read_json(adapter / 'adapter_config.json')
    recorded_base = Path(config['base_model_name_or_path']).expanduser()
    if not recorded_base.is_absolute():
        recorded_base = ROOT / recorded_base
    if recorded_base.resolve() != base.resolve():
        raise ValueError('adapter记录的基座与--base-dir不一致；保留原件，先核对云端路径')
    if config.get('peft_type') != 'LORA' or not config.get('target_modules'):
        raise ValueError('不是预期的LoRA配置或没有目标模块')
    if not (base / 'config.json').is_file() or not (base / 'download-verified.json').is_file():
        raise ValueError('基座缺少配置或下载校验标记；先完成原有verify-only检查')
    manifest = read_json(adapter.parent / 'run-manifest.json')
    train_config = manifest['config']
    if config.get('r') != train_config['lora_r'] or config.get('lora_alpha') != train_config['lora_alpha']:
        raise ValueError('adapter参数与本次训练清单不一致')
    if manifest['run_id'] != adapter.parent.name:
        raise ValueError('目录run_id与训练清单不一致')
    # Stream hashes: never deserialize training_args.bin / pickle or modify weights.
    return {'adapter': str(adapter), 'base': str(base), 'run_id': manifest['run_id'],
            'training_rows': manifest['train_rows'],
            'validation_rows': manifest['validation_rows'], 'test_rows': manifest['test_rows'],
            'files': {name: {'bytes': (adapter / name).stat().st_size,
                             'sha256': file_digest(adapter / name)} for name in required},
            'integrity_scope': 'new_hash_inventory; not a comparison with a prior trusted hash',
            'base_verification': 'marker_exists_only; run original --verify-only for full hash check'}


def infer(adapter, cases, output, max_length, max_new_tokens):
    for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY'):
        os.environ[key] = '1'
    os.environ['WANDB_DISABLED'] = 'true'
    # Import Unsloth before Transformers, matching the established trainer.
    from unsloth import FastModel
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('真实推理需要云端NVIDIA CUDA；本机仅准备和运行离线检查')
    model, processor = FastModel.from_pretrained(
        model_name=str(adapter), max_seq_length=max_length,
        load_in_4bit=True, load_in_8bit=False, full_finetuning=False,
        local_files_only=True, trust_remote_code=False)
    tokenizer = getattr(processor, 'tokenizer', processor)
    FastModel.for_inference(model)
    model.eval()
    if not callable(getattr(model, 'disable_adapter', None)):
        raise RuntimeError('模型不支持disable_adapter，无法进行可信的基座对照')
    if not getattr(model, 'peft_config', None) or not getattr(model, 'active_adapters', None):
        raise RuntimeError('未检测到活动adapter，拒绝把基座冒充微调结果')
    versions = {name: importlib.metadata.version(name)
                for name in ('unsloth', 'transformers', 'peft', 'torch')}
    stop_ids = stop_token_ids(model.generation_config.eos_token_id, tokenizer.eos_token_id)
    (output / 'environment.json').write_text(json.dumps({
        'packages': versions, 'gpu': torch.cuda.get_device_name(0),
        'comparison': 'same_4bit_base_and_adapter_tokenizer; adapter disabled vs enabled',
        'max_length': max_length, 'max_new_tokens': max_new_tokens,
        'do_sample': False, 'seed': 3407, 'enable_thinking': False,
        'active_adapters': model.active_adapters,
        'explicit_eos_token_ids': stop_ids,
    }, ensure_ascii=False, indent=2))
    eos = tokenizer.eos_token_id
    with (output / 'responses.jsonl').open('x', encoding='utf-8') as report:
        for case in cases:
            # Review criteria are deliberately excluded from the model input.
            text = tokenizer.apply_chat_template(
                [{'role': 'user', 'content': case['prompt']}], tokenize=False,
                add_generation_prompt=True, enable_thinking=False, preserve_thinking=True)
            inputs = tokenizer(text, return_tensors='pt', add_special_tokens=False)
            count = inputs['input_ids'].shape[-1]
            if count + max_new_tokens > max_length:
                raise ValueError(f'{case["id"]}超出上下文，拒绝截断')
            inputs = {key: value.to(model.device) for key, value in inputs.items()}
            for variant in ('base', 'finetuned'):
                torch.manual_seed(3407)
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
                start = time.monotonic()
                context = model.disable_adapter() if variant == 'base' else nullcontext()
                with context, torch.inference_mode():
                    generated = model.generate(
                        **inputs, max_new_tokens=max_new_tokens, do_sample=False,
                        eos_token_id=stop_ids,
                        pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else eos)
                torch.cuda.synchronize()
                ids = generated[0, count:].tolist()
                # A configured stop token is stronger evidence than output length alone.
                row = {'id': case['id'], 'variant': variant, 'prompt_tokens': count,
                       'completion_tokens': len(ids), 'elapsed_seconds': round(time.monotonic() - start, 3),
                       'peak_allocated_gib': round(torch.cuda.max_memory_allocated() / 1024**3, 3),
                       'ended_with_eos': bool(ids and ids[-1] in stop_ids),
                       'hit_output_limit': len(ids) >= max_new_tokens,
                       'answer': tokenizer.decode(ids, skip_special_tokens=True),
                       'review_status': 'pending_human_review'}
                report.write(json.dumps(row, ensure_ascii=False) + '\n')
                report.flush()
                print(json.dumps({key: row[key] for key in ('id', 'variant', 'elapsed_seconds')}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', required=True, type=Path)
    parser.add_argument('--base-dir', type=Path, default=ROOT / 'models/base/Qwen3.5-9B')
    parser.add_argument('--report-dir', required=True, type=Path, help='必须使用新目录')
    parser.add_argument('--cases', type=Path, default=CASES)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--max-length', type=int, default=4096)
    parser.add_argument('--max-new-tokens', type=int, default=768)
    parser.add_argument('--run', action='store_true', help='显式加载GPU，执行基座与adapter成对推理')
    args = parser.parse_args()
    if args.report_dir.exists():
        parser.error('报告目录已存在，拒绝覆盖历史结果')
    if args.limit is not None and args.limit < 1:
        parser.error('--limit必须为正整数')
    if not 1 <= args.max_new_tokens < args.max_length <= 4096:
        parser.error('要求1 <= max-new-tokens < max-length <= 4096，与本次训练上下文一致')
    cases = load_cases(args.cases)
    selected = cases[:args.limit] if args.limit else cases
    output = args.report_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    state = {'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'started',
             'gpu_requested': args.run, 'quality_verdict': 'not_evaluated',
             'cases_sha256': file_digest(args.cases), 'case_count': len(selected)}
    (output / 'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    try:
        state['artifact'] = inspect_adapter(args.adapter.resolve(), args.base_dir.resolve())
        (output / 'cases.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2))
        if args.run:
            infer(args.adapter.resolve(), selected, output, args.max_length, args.max_new_tokens)
        state['status'] = 'inference_completed_pending_review' if args.run else 'files_checked_no_inference'
    except (Exception, KeyboardInterrupt) as exc:
        state.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        state['finished_at'] = datetime.now(timezone.utc).isoformat()
        (output / 'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps({'status': state['status'], 'report': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
