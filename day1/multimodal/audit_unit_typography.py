"""Rescore complete raw HTTP evidence through the narrow application formatter.

Both original and pipeline scores use the UNCHANGED frozen scorer. Preserve raw
answers and all failures; no GPU calls, numeric conversions or automatic release.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_official_base import load_evaluation


def audit(args):
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('Refusing to overwrite audit')
    source = json.loads(args.input_responses.with_name('run.json').read_text())
    if not source['status'].startswith('completed_'):
        raise ValueError('Require completed raw inference report')
    policy_sha = hashlib.sha256(args.policy_file.read_bytes()).hexdigest()
    if source['policy_sha256'] != policy_sha:
        raise ValueError('Raw inference policy differs from specified candidate')
    evaluation = load_evaluation(args.evaluation_root)
    cases, _ = evaluation.load_suite(args.evaluation_root/'data/vision-eval-v2', args.training_data)
    development = {r['id']: r for r in cases if r['split'] == 'development'}
    rows = [json.loads(l) for l in args.input_responses.read_text().splitlines() if l.strip()]
    required = {(v, i) for v in source['variants'] for i in development}
    if len(rows) != len(required) or {(r['variant'],r['id']) for r in rows} != required:
        raise ValueError('Require complete unique development matrix; no partial success report')
    formatter_file = ROOT/'assistant_app/backend/visual_units.py'
    spec = importlib.util.spec_from_file_location('candidate_unit_formatter', formatter_file)
    formatter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(formatter)
    results = []
    for row in rows:
        case = development[row['id']]
        raw_score = evaluation.score(row['answer'], case)
        if raw_score != row['score']:
            raise ValueError('Frozen raw score differs; inspect scorer/inference identity')
        if row['model'] != source['variants'][row['variant']]:
            raise ValueError('Raw model identity differs from recorded route')
        try:
            original = evaluation.VisionEvidence.model_validate_json(row['answer']).model_dump()
        except ValueError:
            # A formatter may never repair malformed/uncertain evidence into acceptance.
            pipeline_answer, changes, pipeline_score = None, [], raw_score
        else:
            normalized, changes = formatter.canonical_units(original)
            pipeline_answer = json.dumps(normalized, ensure_ascii=False)
            pipeline_score = evaluation.score(pipeline_answer, case)
        results.append(dict(id=row['id'], variant=row['variant'], raw_score=raw_score,
            pipeline_score=pipeline_score, unit_typography_normalizations=changes,
            pipeline_answer=pipeline_answer, raw_answer_sha256=hashlib.sha256(row['answer'].encode()).hexdigest(),
            normal_finish=row['normal_finish'], repeated_output=row['repeated_output']))
    groups = {}
    for variant in source['variants']:
        selected = [r for r in results if r['variant'] == variant]
        scores = {}
        for name in ('raw_score', 'pipeline_score'):
            total = sum(r[name]['fields_total'] for r in selected)
            correct = sum(r[name]['fields_correct'] for r in selected)
            critical = sum(r[name]['critical_errors'] for r in selected)
            scores[name] = dict(cases=len(selected), fields_correct=correct, fields_total=total,
                critical_errors=critical, all_cases_pass=all(r[name]['case_pass'] for r in selected),
                errors=dict(Counter(e for r in selected for e in r[name]['errors'])))
        groups[variant] = dict(**scores, normalizations=sum(len(r['unit_typography_normalizations']) for r in selected),
            all_normal_stops=all(r['normal_finish'] for r in selected),
            no_loop_heuristic_hits=not any(r['repeated_output'] for r in selected))
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'scored-evidence.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in results))
    state = dict(status='raw_and_pipeline_scored_no_inference', groups=groups,
        raw_responses_sha256=hashlib.sha256(args.input_responses.read_bytes()).hexdigest(),
        policy_sha256=policy_sha, formatter_sha256=hashlib.sha256(formatter_file.read_bytes()).hexdigest(),
        auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        production_accepted=False, human_review='pending', raw_report_modified=False)
    (args.output/'summary.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
    return state


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for option in ('evaluation-root', 'training-data', 'input-responses', 'policy-file', 'output'):
        p.add_argument('--'+option, type=Path, required=True)
    print(json.dumps(audit(p.parse_args()),ensure_ascii=False,indent=2))
