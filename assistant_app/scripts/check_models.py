"""Explicit offline verification and optional real HTTP smoke. Writes only a new report."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from assistant_app.backend.settings import Settings
from assistant_app.backend.model_inventory import inventory, adapter_inventory, digest
from assistant_app.backend.model_clients import ModelClients
from assistant_app.backend.model_clients import vision_as_text


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--hash', action='store_true', help='Read all shards; never download')
    p.add_argument('--probe', action='store_true', help='Make actual text generation request to configured endpoint')
    p.add_argument('--runtime-evidence', type=Path)
    p.add_argument('--kind', choices=['text', 'vision'], default='text')
    p.add_argument('--env-file', type=Path)
    args = p.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite report')
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    s = Settings()
    if args.kind == 'vision':
        s = vision_as_text(s)
    architecture = 'qwen3_5' if args.kind == 'text' else 'qwen3_vl'
    result = {'mode': s.text_mode, 'text_base': str(s.text_model_path.resolve()), 'inventories': [],
              'gpu_generation_performed': False, 'runtime_adapter_verified': False}
    try:
        result['inventories'].append(inventory(s.text_model_path, architecture, args.hash))
        if s.text_mode == 'finetuned_lora':
            if not s.text_adapter_path:
                raise ValueError('text_adapter_missing')
            result['artifact'] = str(s.text_adapter_path.resolve())
            result['inventories'].append(adapter_inventory(s.text_adapter_path, s.text_model_path, args.hash))
            if args.runtime_evidence:
                result['runtime_evidence'] = str(args.runtime_evidence.resolve())
                result['runtime_evidence_sha256'] = digest(args.runtime_evidence)
        elif s.text_mode == 'finetuned_merged':
            if not s.text_merged_model_path:
                raise ValueError('text_merged_missing')
            result['artifact'] = str(s.text_merged_model_path.resolve())
            result['inventories'].append(inventory(s.text_merged_model_path, architecture, args.hash))
            lineage = json.loads((s.text_merged_model_path.parent / 'run-manifest.json').read_text())
            if Path(lineage['model_dir']).resolve() != s.text_model_path.resolve():
                raise ValueError('merged_lineage_mismatch')
        try:
            result['vision'] = inventory(s.vision_model_path, 'qwen3_vl', args.hash)
        except (OSError, ValueError):
            result['vision'] = {'state': 'incomplete_or_missing'}
        if args.probe:
            async def probe():
                models = ModelClients(s)
                try:
                    available = await models.available('text')
                    answer, meta = await models.call('text', [{'role': 'user', 'content': '请只回答：模型接线测试。'}])
                    return {'listed': available, 'response': answer, 'metadata': meta,
                            'note': '真实HTTP生成；服务端GPU与Adapter加载仍须独立核对'}
                finally:
                    await models.close()
            result['probe'] = asyncio.run(probe())
        result['state'] = 'offline_files_verified' if args.hash else 'offline_file_presence_only'
    except (OSError, ValueError, KeyError) as exc:
        result['state'], result['error'] = 'failed', str(exc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
    print(json.dumps({'state': result['state'], 'report': str(args.output)}, ensure_ascii=False))
    if result['state'] == 'failed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
