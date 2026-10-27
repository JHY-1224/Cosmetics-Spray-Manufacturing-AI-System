"""Offline preflight: use the exact native model template without loading weights."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import ROOT, PREPARED, native_tokenizer, verified_rows, tokenize_example, audit_text_labels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-length', type=int, default=4096)
    parser.add_argument('--model-dir', type=Path, default=ROOT / 'models/base/Qwen3.5-9B')
    parser.add_argument('--report-file', type=Path, help='New output file; never overwrite a prior report')
    parser.add_argument('--prepared-dir', type=Path, default=PREPARED)
    args = parser.parse_args()
    model = args.model_dir.resolve()
    report_file = args.report_file or PREPARED / f'tokenizer-preflight-{model.name}.json'
    if report_file.exists():
        parser.error('Report already exists; select a new --report-file')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    tokenizer = native_tokenizer(model)
    rows, manifest = verified_rows(args.prepared_dir)
    results = []
    for row in rows:
        item = tokenize_example(tokenizer, row['messages'], args.max_length)
        results.append({'tokens': len(item['input_ids']), **audit_text_labels(tokenizer, row['messages'], item)})
    report = {'status': 'passed', 'rows': len(rows), 'max_length': args.max_length,
              'train_sha256': manifest['train_sha256'],
              'model_dir': str(model),
              'template_sha256': hashlib.sha256(tokenizer.get_chat_template().encode()).hexdigest(),
              'max_tokens': max(x['tokens'] for x in results), 'samples': results,
              'gpu_training': 'not_executed'}
    with report_file.open('x') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
