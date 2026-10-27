"""Run new template-sharing teaching holdouts without exposing their targets."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_app_text as app_probe


def checked_holdout(folder):
    manifest = json.loads((folder/'dataset-manifest.json').read_text())
    path = folder/'validation.messages.jsonl'
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['validation_sha256']:
        raise ValueError('Teaching holdout changed')
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if len(rows) != manifest['validation_rows'] or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Invalid holdout inventory')
    if {r['id'] for r in rows} & set(manifest['sample_ids']):
        raise ValueError('Holdout overlaps training inventory')
    requests = []
    for row in rows:
        messages = row['messages']
        if row['split'] != 'validation' or len(messages) != 3:
            raise ValueError('Require single-turn teaching holdouts')
        if messages[0] != {'role': 'system', 'content': app_probe.SYSTEM}:
            raise ValueError('Holdout application system mismatch')
        if messages[1]['role'] != 'user' or messages[2]['role'] != 'assistant':
            raise ValueError('Invalid holdout roles')
        # Reference answers/expected values stay in the separate dataset.
        requests.append(dict(id=row['id'], messages=messages[:2]))
    return requests, manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evaluation-root', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--adapter', type=Path, required=True)
    p.add_argument('--base-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--run', action='store_true')
    args = p.parse_args()
    if args.output.exists() or args.output.is_symlink():
        p.error('Refusing to overwrite report')
    rows, manifest = checked_holdout(args.data)
    evaluation = app_probe.load_evaluation(args.evaluation_root)
    args.variant, args.stop_mode = 'finetuned', 'chat'
    state = dict(status='holdout_files_checked_no_inference', cases=len(rows),
        validation_sha256=manifest['validation_sha256'],
        scope=manifest['validation_scope'], production_accepted=False)
    if args.run:
        args.output.mkdir(parents=True, exist_ok=False)
        state['started_at'] = datetime.now(timezone.utc).isoformat()
        try:
            state.update(app_probe.infer(args, rows, evaluation))
        except (Exception, KeyboardInterrupt) as exc:
            state.update(status='failed', error=repr(exc))
            raise
        finally:
            state['finished_at'] = datetime.now(timezone.utc).isoformat()
            (args.output/'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
