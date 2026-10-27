"""Reconstruct the existing vision training collator; never update model weights.

This audits current tensors, not tensors retained from a historical training job.
The native template tail must match the actual expanded input exactly before any
claim about assistant/EOS supervision is made. No truncation or masking repair.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import load_records, training_dataset

HEADER = '<|im_start|>assistant\n'


def check_labels(input_ids, labels, attention, tail_ids, header_ids, answer_ids,
                 end_ids, eos_id):
    """Prove token alignment and report supervision without changing any label."""
    if not len(input_ids) == len(labels) == len(attention):
        raise ValueError('Tensor lengths differ')
    active = [i for i, value in enumerate(attention) if value]
    if not active or active != list(range(active[0], active[-1] + 1)):
        raise ValueError('Attention mask is empty or non-contiguous')
    ids = [input_ids[i] for i in active]
    if (tail_ids != header_ids + answer_ids + end_ids or
            len(ids) < len(tail_ids) or ids[-len(tail_ids):] != tail_ids):
        raise ValueError('Native assistant tail does not exactly align with input')
    if not end_ids or end_ids[0] != eos_id:
        raise ValueError('Native completion does not immediately end with EOS')
    start = active[-1] + 1 - len(tail_ids)
    answer_start = start + len(header_ids)
    eos_position = answer_start + len(answer_ids)
    bad_prefix = [i for i in range(answer_start) if labels[i] != -100]
    missing_answer = [i for i in range(answer_start, eos_position)
                      if labels[i] != input_ids[i]]
    bad_padding = [i for i, value in enumerate(attention)
                   if not value and labels[i] != -100]
    mismatches = [i for i, value in enumerate(labels)
                  if value != -100 and value != input_ids[i]]
    eos_supervised = labels[eos_position] == eos_id
    return {
        'aligned': True, 'input_tokens': len(ids),
        'answer_tokens': len(answer_ids),
        'supervised_tokens': sum(v != -100 for v in labels),
        'assistant_start': answer_start, 'eos_position': eos_position,
        'eos_input_id': input_ids[eos_position],
        'eos_label': labels[eos_position], 'eos_supervised': eos_supervised,
        'supervised_prefix_positions': bad_prefix,
        'missing_answer_positions': missing_answer,
        'supervised_padding_positions': bad_padding,
        'label_value_mismatches': mismatches,
        'passed': bool(answer_ids) and eos_supervised and not any(
            (bad_prefix, missing_answer, bad_padding, mismatches)),
    }


def native_tail(processor, messages, answer):
    marker = '__SUPERVISION_AUDIT_4d13e7a9__'
    changed = copy.deepcopy(messages)
    changed[-1]['content'] = [{'type': 'text', 'text': marker}]
    rendered = processor.apply_chat_template(messages, tokenize=False,
                                              add_generation_prompt=False)
    probe = processor.apply_chat_template(changed, tokenize=False,
                                           add_generation_prompt=False)
    if probe.count(marker) != 1:
        raise ValueError('Cannot identify final assistant in native template')
    prefix, end = probe.split(marker)
    if not prefix.endswith(HEADER) or prefix + answer + end != rendered:
        raise ValueError('Native template does not preserve exact assistant text')
    if not end.startswith('<|im_end|>') or end[len('<|im_end|>'):].strip():
        raise ValueError('Unexpected native assistant terminator')
    tok = processor.tokenizer
    encode = lambda text: tok(text, add_special_tokens=False)['input_ids']
    return (encode(HEADER + answer + end), encode(HEADER), encode(answer),
            encode(end), tok.convert_tokens_to_ids('<|im_end|>'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project-root', type=Path,
                   default=Path(__file__).resolve().parents[2])
    p.add_argument('--model-dir', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--allow-unreviewed', action='store_true')
    args = p.parse_args()
    rows = [r for r in load_records(args.data, args.allow_unreviewed)
            if r['split'] == 'train']
    if not rows:
        raise ValueError('No training rows')
    config = json.loads((args.model_dir / 'config.json').read_text())
    if (config.get('model_type') != 'qwen3_vl' or
            config.get('text_config', {}).get('hidden_size') != 4096):
        raise ValueError('This reconstruction is specific to Qwen3-VL-8B')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', WANDB_DISABLED='true')
    from unsloth import FastVisionModel
    from unsloth.trainer import UnslothVisionDataCollator
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA required for the existing training pipeline')
    model, processor = FastVisionModel.from_pretrained(
        model_name=str(args.model_dir.resolve()), max_seq_length=4096,
        load_in_4bit=True, use_gradient_checkpointing='unsloth',
        local_files_only=True, trust_remote_code=False)
    model = FastVisionModel.get_peft_model(
        model, finetune_vision_layers=False, finetune_language_layers=True,
        finetune_attention_modules=True, finetune_mlp_modules=True,
        r=16, lora_alpha=16, lora_dropout=0, bias='none', random_state=3407,
        use_rslora=False, loftq_config=None)
    FastVisionModel.for_training(model)
    processor.image_processor.size = {
        'shortest_edge': 256 * 32 * 32, 'longest_edge': 1536 * 32 * 32}
    collator = UnslothVisionDataCollator(
        model, processor, max_seq_length=1_000_000, resize='max',
        train_on_responses_only=True, instruction_part='<|im_start|>user\n',
        response_part=HEADER, completion_only_loss=True)
    data = training_dataset(rows)
    source = Path(inspect.getfile(UnslothVisionDataCollator))
    report = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'kind': 'current_training_pipeline_reconstruction_no_weight_updates',
        'data_sha256': hashlib.sha256(args.data.read_bytes()).hexdigest(),
        'train_script_sha256': hashlib.sha256(
            (args.project_root / 'day1/multimodal/train_vl.py').read_bytes()).hexdigest(),
        'audit_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'collator_source': str(source),
        'collator_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'packages': {n: importlib.metadata.version(n) for n in
                     ('unsloth', 'unsloth_zoo', 'torch', 'transformers', 'trl', 'datasets', 'peft')},
        'pad_token_id': processor.tokenizer.pad_token_id,
        'eos_token_id': processor.tokenizer.eos_token_id,
        'collator_masked_token_ids': collator.padding_token_ids.tolist(),
        'image_size': processor.image_processor.size,
        'rows': [], 'padding_batch': [],
    }

    def audit(batch, index, item, row):
        ids = batch['input_ids'][index].tolist()
        labels = batch['labels'][index].tolist()
        attention = batch['attention_mask'][index].tolist()
        result = {'id': row['id']}
        try:
            result.update(check_labels(ids, labels, attention,
                                      *native_tail(processor, item['messages'], row['answer'])))
            if result['input_tokens'] > 4096:
                result.update(passed=False, over_training_budget=True)
        except ValueError as exc:
            result.update(aligned=False, passed=False, error=str(exc))
        result.update(answer=row['answer'], supervised_text=processor.tokenizer.decode(
            [v for v in labels if v != -100], skip_special_tokens=False),
            input_ids=ids, labels=labels, attention_mask=attention)
        return result

    for i, row in enumerate(rows):
        item = data[i]
        batch = collator([item])
        result = audit(batch, 0, item, row)
        report['rows'].append(result)
        print(json.dumps({k: v for k, v in result.items() if k not in
                         ('input_ids', 'labels', 'attention_mask')}, ensure_ascii=False), flush=True)
    selected = sorted(range(len(rows)), key=lambda i: report['rows'][i].get('input_tokens', 0))
    selected = list(dict.fromkeys((selected[0], selected[-1])))
    items = [data[i] for i in selected]
    batch = collator(items)
    for index, (i, item) in enumerate(zip(selected, items, strict=True)):
        report['padding_batch'].append(audit(batch, index, item, rows[i]))
    report['summary'] = {
        'rows': len(rows), 'passed': sum(r['passed'] for r in report['rows']),
        'unverifiable': sum(not r['aligned'] for r in report['rows']),
        'eos_supervised': sum(r.get('eos_supervised', False) for r in report['rows']),
        'padding_batch_passed': all(r['passed'] for r in report['padding_batch']),
        'weight_updates': 0, 'historical_tensors_available': False,
    }
    report['summary']['all_passed'] = (
        report['summary']['passed'] == len(rows) and report['summary']['padding_batch_passed'])
    target = args.output_dir / 'supervision-audit.json'
    with target.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('AUDIT_SUMMARY', json.dumps(report['summary']), flush=True)
    print('REPORT', target, flush=True)
    if not report['summary']['all_passed']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
