"""Offline Qwen3-VL-8B QLoRA entry. --dry-run never imports GPU frameworks.

Uses the official Unsloth vision collator, response masking and native processor.
No automatic installs, network downloads, uploads, merges or random data splitting.
"""
import argparse
from datetime import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import load_records, conversation

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model-dir', type=Path, default=Path('/root/autodl-tmp/models/Qwen3-VL-8B-Instruct'))
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output-root', type=Path, default=ROOT / 'models/finetuned/Qwen3-VL-8B-cosmetics')
    p.add_argument('--allow-unreviewed', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--max-steps', type=int, default=-1, help='Set 1 for the first CUDA smoke run')
    p.add_argument('--max-length', type=int, default=4096)
    args = p.parse_args()
    if args.max_steps == 0 or args.max_steps < -1 or not 512 <= args.max_length <= 8192:
        p.error('Invalid steps/context budget')
    rows = load_records(args.data, args.allow_unreviewed)
    train = [r for r in rows if r['split'] == 'train']
    if not train:
        raise ValueError('No train rows')
    model_dir = args.model_dir.expanduser().resolve()
    config = json.loads((model_dir / 'config.json').read_text())
    text = config.get('text_config', config)
    if config.get('model_type') != 'qwen3_vl' or text.get('hidden_size') != 4096:
        raise ValueError('Expected Qwen3-VL-8B-Instruct architecture')
    state = {'model_dir': str(model_dir), 'train_rows': len(train),
             'validation_rows': sum(r['split'] == 'validation' for r in rows),
             'test_rows': sum(r['split'] == 'test' for r in rows),
             'data_sha256': hashlib.sha256(args.data.read_bytes()).hexdigest(),
             'expert_reviewed': all(r.get('expert_reviewed') for r in train),
             'source_groups': sorted({r['source_group'] for r in train}),
             'dry_run': args.dry_run, 'gpu_tested': False}
    state.update(max_steps=args.max_steps, purpose='smoke' if args.max_steps > 0 else 'development_sft')
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return
    proof = json.loads((model_dir / 'download-verified.json').read_text())
    if proof.get('status') != 'verified' or proof.get('model_id') != 'Qwen/Qwen3-VL-8B-Instruct':
        raise ValueError('Run official verify-only on the existing cloud model first')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', WANDB_DISABLED='true')
    from unsloth import FastVisionModel, is_bfloat16_supported
    from unsloth.trainer import UnslothVisionDataCollator
    import torch
    from trl import SFTConfig, SFTTrainer
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA required; use --dry-run on the Mac')
    model, processor = FastVisionModel.from_pretrained(
        model_name=str(model_dir), max_seq_length=args.max_length,
        load_in_4bit=True, use_gradient_checkpointing='unsloth',
        local_files_only=True, trust_remote_code=False)
    model = FastVisionModel.get_peft_model(
        model, finetune_vision_layers=False, finetune_language_layers=True,
        finetune_attention_modules=True, finetune_mlp_modules=True,
        r=16, lora_alpha=16, lora_dropout=0, bias='none', random_state=3407,
        use_rslora=False, loftq_config=None)
    FastVisionModel.for_training(model)
    # Bound pixels separately from text length; never silently truncate image tokens.
    processor.image_processor.size = {'shortest_edge': 256 * 32 * 32, 'longest_edge': 1536 * 32 * 32}
    collator = UnslothVisionDataCollator(
        model, processor, max_seq_length=1_000_000, resize='max',
        train_on_responses_only=True, instruction_part='<|im_start|>user\n',
        response_part='<|im_start|>assistant\n', completion_only_loss=True)
    class VisionRows:
        def __len__(self): return len(train)
        def __getitem__(self, index): return conversation(train[index])
    data = VisionRows()
    # Validate every expanded image/text sequence and supervision before allocating a run.
    for i in range(len(data)):
        batch = collator([data[i]])
        if batch['input_ids'].shape[-1] > args.max_length:
            raise ValueError(f"{train[i]['id']}: expanded sequence exceeds budget; crop/review explicitly")
        if not (batch['labels'] != -100).any():
            raise ValueError(f"{train[i]['id']}: empty answer supervision")
    run = args.output_root.resolve() / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    run.mkdir(parents=True, exist_ok=False)
    state.update(run_id=run.name, config={'lora_r': 16, 'lora_alpha': 16},
                 packages={n: importlib.metadata.version(n) for n in ('unsloth', 'transformers', 'trl', 'torch')},
                 supervision='assistant_only; image/user/padding masked; no truncation',
                 actual_trainable_modules=[n for n,v in model.named_parameters() if v.requires_grad])
    (run / 'run-manifest.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    trainer = SFTTrainer(model=model, processing_class=processor, train_dataset=data,
        data_collator=collator, args=SFTConfig(
            output_dir=str(run / 'checkpoints'), per_device_train_batch_size=1,
            gradient_accumulation_steps=4, num_train_epochs=1, max_steps=args.max_steps,
            learning_rate=2e-4, warmup_steps=0, logging_steps=1, optim='adamw_8bit',
            bf16=is_bfloat16_supported(), fp16=not is_bfloat16_supported(),
            remove_unused_columns=False, dataset_kwargs={'skip_prepare_dataset': True},
            max_length=None, packing=False, report_to='none', save_strategy='epoch',
            save_total_limit=1, seed=3407))
    metrics = trainer.train().metrics
    trainer.save_model(str(run / 'adapter'))
    processor.save_pretrained(str(run / 'adapter'))
    (run / 'train-metrics.json').write_text(json.dumps(metrics, indent=2))
    print('Saved vision adapter:', run / 'adapter', flush=True)


if __name__ == '__main__':
    main()
