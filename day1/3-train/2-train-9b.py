"""9B-only entry into the existing approved-data training pipeline; no automatic merge."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', type=Path, default=Path('/root/autodl-tmp/models/Qwen3.5-9B'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output-root', type=Path)
    args = parser.parse_args()
    model = args.model_dir.expanduser().resolve()
    config = json.loads((model / 'config.json').read_text())
    text = config.get('text_config', config)
    if config.get('model_type') != 'qwen3_5' or text.get('hidden_size') != 4096:
        raise SystemExit('此入口仅接受Qwen3.5-9B；拒绝旧27B或VL基座')
    command = [sys.executable, str(Path(__file__).with_name('1-train.py')), '--config',
               str(Path(__file__).with_name('train_config.qwen35-9b.json')), '--model-dir', str(model)]
    if args.dry_run:
        command.append('--dry-run')
    if args.output_root:
        command.extend(['--output-root', str(args.output_root)])
    raise SystemExit(subprocess.run(command, cwd=ROOT).returncode)
