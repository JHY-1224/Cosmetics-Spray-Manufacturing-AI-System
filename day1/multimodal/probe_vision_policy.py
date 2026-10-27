"""Compare a candidate vision policy on explicitly selected development cases.

Frozen package, raw baseline and deployed application remain unchanged. Default
checks files/imports only. --run uses the existing adapter processor and 4-bit
base with LoRA disabled/enabled, keeping generation parameters fixed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_official_base import load_evaluation, select_development


def selected_development(evaluation, args):
    rows, card = evaluation.load_suite(args.evaluation_root / 'data/vision-eval-v2', args.training_data)
    development = {r['id']: r for r in rows if r['split'] == 'development'}
    ids = args.case_ids or [r['id'] for r in select_development(rows)]
    if len(ids) != len(set(ids)) or any(i not in development for i in ids):
        raise ValueError('Only unique development case IDs are allowed')
    return [development[i] for i in ids], card, len(development)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evaluation-root', type=Path, required=True)
    p.add_argument('--training-data', type=Path, required=True)
    p.add_argument('--adapter', type=Path, required=True)
    p.add_argument('--policy-file', type=Path, required=True)
    p.add_argument('--case-ids', nargs='+')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--run', action='store_true')
    args = p.parse_args()
    if args.output.exists() or args.output.is_symlink():
        p.error('Refusing to overwrite an earlier run')
    evaluation = load_evaluation(args.evaluation_root)
    rows, card, expected = selected_development(evaluation, args)
    policy = args.policy_file.read_text(encoding='utf-8')
    if not policy.strip() or len(policy) > 12000:
        p.error('Candidate policy must be 1..12000 characters')
    original_metadata = evaluation.policy_metadata()
    candidate_metadata = {**original_metadata, 'version': 'vision-policy-candidate-20261027-v1',
        'vision_sha256': hashlib.sha256(policy.encode()).hexdigest(),
        'original_vision_sha256': original_metadata['vision_sha256'], 'deployed': False}
    evaluation.VISION = policy
    evaluation.policy_metadata = lambda: dict(candidate_metadata)
    state = dict(status='files_and_imports_checked_no_inference',
        case_ids=[r['id'] for r in rows], expected_development_cases=expected,
        policy=candidate_metadata, production_accepted=False)
    if args.run:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'candidate-policy.txt').write_text(policy, encoding='utf-8')
        args.variant, args.seed, args.max_new_tokens = 'both', 3407, 768
        try:
            state.update(evaluation.run(args, rows, card, expected))
        except (Exception, KeyboardInterrupt) as exc:
            state.update(status='failed', error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            (args.output / 'run.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
