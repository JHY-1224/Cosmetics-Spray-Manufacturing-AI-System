"""Paired, explicit text-policy control on captured/teaching development inputs."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_app_text as app
from probe_teaching_holdout import checked_holdout


def policy_rows(rows, policy, paired):
    result = []
    for row in rows:
        for name in (('original', 'candidate') if paired else ('candidate',)):
            value = copy.deepcopy(row)
            value['id'] += '--' + name
            if name == 'candidate':
                value['messages'][0] = {'role': 'system', 'content': policy}
            result.append(value)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evaluation-root', type=Path, required=True)
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--teaching-data', type=Path, required=True)
    p.add_argument('--policy-file', type=Path, required=True)
    p.add_argument('--adapter', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    selection = p.add_mutually_exclusive_group(required=True)
    selection.add_argument('--case-ids', nargs='+')
    selection.add_argument('--all-development', action='store_true')
    p.add_argument('--paired', action='store_true')
    p.add_argument('--precision', choices=('4bit', 'bf16'), default='4bit')
    p.add_argument('--variant', choices=('base', 'finetuned', 'both'), default='finetuned')
    p.add_argument('--run', action='store_true')
    args = p.parse_args()
    if args.output.exists() or args.output.is_symlink():
        p.error('Refusing to overwrite report')
    evaluation = app.load_evaluation(args.evaluation_root)
    captured, capture_metadata = app.checked_messages(args.capture, args.evaluation_root)
    heldout, teaching_metadata = checked_holdout(args.teaching_data)
    pool = {r['id']: r for r in captured + heldout}
    ids = list(pool) if args.all_development else args.case_ids
    if len(ids) != len(set(ids)) or any(i not in pool for i in ids):
        p.error('Only unique development/teaching-holdout IDs allowed')
    policy = args.policy_file.read_text()
    if not 100 <= len(policy) <= 12000:
        p.error('Candidate policy size invalid')
    rows = policy_rows([pool[i] for i in ids], policy, args.paired)
    state = dict(status='files_checked_no_inference', source_cases=len(ids), source_case_ids=ids,
        planned_generations=len(rows), paired=args.paired,
        capture_sha256=capture_metadata['messages_sha256'],
        teaching_holdout_sha256=teaching_metadata['validation_sha256'],
        original_policy_sha256=hashlib.sha256(app.SYSTEM.encode()).hexdigest(),
        candidate_policy_sha256=hashlib.sha256(policy.encode()).hexdigest(),
        adapter=str(args.adapter.resolve()), production_accepted=False, policy_deployed=False)
    state.update(requested_precision=args.precision,
        policy_probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        inference_probe_sha256=hashlib.sha256(Path(app.__file__).read_bytes()).hexdigest())
    args.stop_mode = 'chat'
    state['variant'] = args.variant
    if args.run:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output/'candidate-policy.txt').write_text(policy)
        state['started_at'] = datetime.now(timezone.utc).isoformat()
        try:
            state.update(app.infer(args, rows, evaluation))
        except BaseException as exc:
            state.update(status='failed', error=repr(exc))
            raise
        finally:
            state['finished_at'] = datetime.now(timezone.utc).isoformat()
            (args.output/'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
