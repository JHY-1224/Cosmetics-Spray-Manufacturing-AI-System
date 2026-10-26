"""Unsloth LoRA SFT; adapted structure/parameters from the FFmpeg reference.

Use --dry-run for offline path/data checks. Actual training requires CUDA.
Outputs are per-run and never overwrite an earlier run or the base weights.
"""
import argparse
from datetime import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import ROOT, native_tokenizer, verified_rows, tokenize_example, AssistantCollator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('train_config.json'))
    parser.add_argument('--model-dir', type=Path, help='Explicit offline base path for this environment')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output-root', type=Path, help='Root for versioned adapter runs')
    parser.add_argument('--merge', action='store_true', help='Also export BF16 merged weights after adapter save')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.model_dir:
        config['model_dir'] = str(args.model_dir.expanduser().resolve())
    rows, manifest = verified_rows()
    model_dir = ROOT / config['model_dir']
    state = {'train_rows': len(rows), 'validation_rows': manifest['validation_rows'],
             'test_rows': manifest['test_rows'], 'model_dir': str(model_dir), 'config': config,
             'weights_verified': (model_dir / 'download-verified.json').is_file()}
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return
    if not state['weights_verified']:
        raise SystemExit('Complete official model download and SHA256 verification before training')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['WANDB_DISABLED'] = 'true'
    from unsloth import FastModel, is_bfloat16_supported
    import torch
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer
    if not torch.cuda.is_available():
        raise SystemExit('NVIDIA CUDA GPU required; this Mac is for preparation/download only')
    model, processor = FastModel.from_pretrained(
        model_name=str(model_dir), max_seq_length=config['max_length'],
        load_in_4bit=config['load_in_4bit'], load_in_8bit=False,
        full_finetuning=False, local_files_only=True, trust_remote_code=False)
    tokenizer = native_tokenizer(model_dir, getattr(processor, 'tokenizer', processor))
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenized = [tokenize_example(tokenizer, row['messages'], config['max_length']) for row in rows]
    model = FastModel.get_peft_model(
        model, finetune_vision_layers=False, finetune_language_layers=True,
        finetune_attention_modules=True, finetune_mlp_modules=True,
        r=config['lora_r'], lora_alpha=config['lora_alpha'], lora_dropout=config['lora_dropout'],
        bias='none', use_gradient_checkpointing='unsloth', random_state=config['seed'],
        use_rslora=False, loftq_config=None)
    run_id = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output = ROOT / 'day1/3-train/outputs' / run_id
    artifact_root = args.output_root or ROOT / 'models/finetuned' / (model_dir.name + '-cosmetics')
    adapter = artifact_root.expanduser().resolve() / run_id / 'adapter'
    output.mkdir(parents=True, exist_ok=False)
    adapter.mkdir(parents=True, exist_ok=False)
    metadata = {**state, 'dataset': manifest, 'run_id': run_id,
                'actual_trainable_modules': [name for name, parameter in model.named_parameters()
                                             if parameter.requires_grad],
                'packages': {name: importlib.metadata.version(name)
                             for name in ('unsloth', 'transformers', 'trl', 'torch', 'datasets')},
                'enable_thinking': False, 'supervision': 'all assistant answers + EOS; no truncation'}
    (output / 'run-manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    training_keys = ('per_device_train_batch_size', 'gradient_accumulation_steps', 'warmup_steps',
                     'num_train_epochs', 'learning_rate', 'logging_steps', 'optim',
                     'weight_decay', 'lr_scheduler_type', 'seed')
    trainer = SFTTrainer(
        model=model, processing_class=tokenizer, train_dataset=Dataset.from_list(tokenized),
        data_collator=AssistantCollator(tokenizer.pad_token_id),
        args=SFTConfig(
            **{key: config[key] for key in training_keys}, output_dir=str(output),
            max_length=config['max_length'], packing=False, report_to='none',
            bf16=is_bfloat16_supported(), fp16=not is_bfloat16_supported(),
            dataset_kwargs={'skip_prepare_dataset': True}, remove_unused_columns=False,
            save_strategy='epoch', save_total_limit=config.get('save_total_limit', 3), eval_strategy='no'))
    metrics = trainer.train().metrics
    trainer.save_model(str(adapter))
    tokenizer.save_pretrained(str(adapter))
    (adapter.parent / 'run-manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    (output / 'train-metrics.json').write_text(json.dumps(metrics, indent=2))
    if args.merge:
        merged = adapter.parent / 'merged'
        model.save_pretrained_merged(str(merged), tokenizer, save_method='merged_16bit')
    print('Saved adapter:', adapter)
    print('Merged model:', adapter.parent / 'merged' if args.merge else 'not requested')


if __name__ == '__main__':
    main()
