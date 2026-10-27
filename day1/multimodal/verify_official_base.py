"""Offline official-base/official-processor probe in the existing Unsloth runtime.

No adapter, training or decoding parameter search. Uses three training images only
for failure diagnosis, plus one development case per category for a limited smoke.
Neither subset is a full model-quality verdict. Sealed cases are never selected.
"""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dataset import load_records, conversation
from day1.generation import stop_token_ids

MANIFEST_SHA256 = '6839c7bdea2a22172d20b6bd1ddf9277dc99460b82d54c389fc4ee149da863b7'


def load_evaluation(root):
    """Load the frozen, verified kit explicitly; never resolve a pip evaluate package."""
    root = Path(root).resolve()
    manifest_path = root / 'MANIFEST.json'
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != MANIFEST_SHA256:
        raise ValueError('Unexpected evaluation kit manifest')
    manifest = json.loads(manifest_path.read_text())
    inventory = {}
    for item in manifest['files']:
        relative = Path(item['path'])
        path = (root / relative).resolve()
        if (relative.is_absolute() or not path.is_relative_to(root) or
                (root / relative).is_symlink() or not path.is_file() or
                path.stat().st_size != item['bytes'] or
                hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']):
            raise ValueError('Evaluation package integrity failed: ' + item['path'])
        inventory[item['path']] = item['sha256']
    script = root / 'day1/multimodal/evaluate.py'
    if 'day1/multimodal/evaluate.py' not in inventory:
        raise ValueError('Evaluation module is not in the manifest')
    spec = importlib.util.spec_from_file_location('cosmetics_frozen_vision_eval', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Reject an already-imported application contract from different source bytes.
    for actual, relative in (
            (inspect.getfile(module.VisionEvidence), 'assistant_app/backend/schemas.py'),
            (inspect.getfile(module.policy_metadata), 'assistant_app/backend/prompts.py')):
        if hashlib.sha256(Path(actual).read_bytes()).hexdigest() != inventory[relative]:
            raise ValueError('Imported application contract differs from frozen package')
    return module


def select_development(rows):
    selected, seen = [], set()
    for row in rows:
        if row['split'] == 'development' and row['category'] not in seen:
            selected.append(row)
            seen.add(row['category'])
    if not selected:
        raise ValueError('No development cases')
    return selected


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-dir', type=Path, required=True)
    p.add_argument('--training-data', type=Path, required=True)
    p.add_argument('--evaluation-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--run', action='store_true', help='Explicitly load CUDA and generate; default checks files/imports only')
    args = p.parse_args()
    if args.output.exists():
        p.error('Refusing to overwrite earlier evidence')
    evaluation = load_evaluation(args.evaluation_root)
    train = load_records(args.training_data, allow_unreviewed=True)[:3]
    rows, card = evaluation.load_suite(args.evaluation_root / 'data/vision-eval-v2', args.training_data)
    development = select_development(rows)
    config = json.loads((args.model_dir / 'config.json').read_text())
    if (config.get('model_type') != 'qwen3_vl' or
            config.get('text_config', {}).get('hidden_size') != 4096):
        raise ValueError('This diagnostic requires the original Qwen3-VL-8B base')
    if not args.run:
        print(json.dumps({
            'status': 'files_and_imports_checked_no_inference',
            'evaluation_module': evaluation.__file__,
            'evaluation_manifest_sha256': MANIFEST_SHA256,
            'training_replay_images': len(train),
            'development_cases': len(development),
            'development_categories': [row['category'] for row in development],
            'planned_generations': 2 * len(train) + len(development),
            'production_accepted': False,
        }, ensure_ascii=False, indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', WANDB_DISABLED='true')
    from unsloth import FastVisionModel
    from transformers import AutoProcessor
    import torch
    model, _ = FastVisionModel.from_pretrained(
        model_name=str(args.model_dir.resolve()), max_seq_length=8192,
        load_in_4bit=True, local_files_only=True, trust_remote_code=False)
    # Explicitly reload the official processor, not one saved with an adapter.
    processor = AutoProcessor.from_pretrained(
        str(args.model_dir.resolve()), local_files_only=True, trust_remote_code=False)
    FastVisionModel.for_inference(model)
    model.eval()
    if getattr(model, 'peft_config', None):
        raise RuntimeError('This probe requires a base without an adapter')
    tokenizer = processor.tokenizer
    eos = model.generation_config.eos_token_id
    stop = stop_token_ids(eos, tokenizer.eos_token_id)
    state = {
        'status': 'started', 'base': str(args.model_dir.resolve()),
        'processor_source': str(args.model_dir.resolve()),
        'processor_class': type(processor).__name__,
        'image_processor_class': type(processor.image_processor).__name__,
        'image_size': dict(processor.image_processor.size),
        'generation': {'do_sample': False, 'seed': 3407,
                       'original_generation_eos': eos, 'explicit_eos_token_ids': stop,
                       'training_replay_max_new_tokens': 256, 'development_max_new_tokens': 768},
        'gpu': torch.cuda.get_device_name(0),
        'packages': {n: importlib.metadata.version(n) for n in
                     ('unsloth', 'torch', 'transformers', 'peft')},
        'training_data_sha256': hashlib.sha256(args.training_data.read_bytes()).hexdigest(),
        'cases_sha256': card['cases_sha256'], 'production_accepted': False,
        'evaluation_module': evaluation.__file__,
        'evaluation_manifest_sha256': MANIFEST_SHA256,
        'scope': '14 diagnostic generations; shared Unsloth/4bit runtime, not an independent runtime',
    }
    (args.output / 'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    jobs = []
    for row in train:
        original = conversation(row)['messages'][:1]
        jobs.append(('training_replay', row, original, 256))
        simple = conversation(row)['messages'][:1]
        simple[0]['content'][0]['text'] = (
            '请用不超过三句话描述图片中直接可见的物体、颜色和排列。'
            '看不清的内容说明看不清，不推测工序、材质或用途。')
        jobs.append(('visible_only_prompt', row, simple, 256))
    jobs.extend(('development_json', row, evaluation.messages(row), 768) for row in development)
    results = []
    try:
        with (args.output / 'responses.jsonl').open('x') as out:
            for kind, row, prompt, budget in jobs:
                inputs = processor.apply_chat_template(
                    prompt, tokenize=True, add_generation_prompt=True,
                    return_dict=True, return_tensors='pt').to(model.device)
                count = inputs['input_ids'].shape[-1]
                if 'pixel_values' not in inputs or count + budget > 8192:
                    raise ValueError('Missing pixels or context budget exceeded')
                torch.manual_seed(3407)
                torch.cuda.synchronize()
                started = time.monotonic()
                with torch.inference_mode():
                    generated = model.generate(**inputs, max_new_tokens=budget,
                        do_sample=False, eos_token_id=stop)
                torch.cuda.synchronize()
                ids = generated[0, count:].tolist()
                answer = tokenizer.decode(ids, skip_special_tokens=True)
                result = {
                    'id': row['id'], 'kind': kind, 'prompt_tokens': count,
                    'pixel_values_shape': list(inputs['pixel_values'].shape),
                    'image_grid_thw': inputs['image_grid_thw'].tolist(),
                    'completion_tokens': len(ids), 'ended_with_eos': bool(ids and ids[-1] in stop),
                    'hit_output_limit': len(ids) >= budget, 'repeated_output': evaluation.repeated_tail(ids),
                    'elapsed_seconds': round(time.monotonic() - started, 3),
                    'answer': answer, 'quality_review': 'pending',
                }
                if kind == 'development_json':
                    result['score'] = evaluation.score(answer, row)
                results.append(result)
                out.write(json.dumps(result, ensure_ascii=False) + '\n')
                out.flush()
                os.fsync(out.fileno())
                print(json.dumps(result, ensure_ascii=False), flush=True)
        state.update(status='completed_pending_review', generations=len(results),
                     eos_ended=sum(r['ended_with_eos'] for r in results),
                     repeated=sum(r['repeated_output'] for r in results))
    except Exception as exc:
        state.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        (args.output / 'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print('OFFICIAL_BASE_SUMMARY', json.dumps(state, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
