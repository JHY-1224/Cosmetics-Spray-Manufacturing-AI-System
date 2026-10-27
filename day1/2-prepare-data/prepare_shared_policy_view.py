"""Create a new shared-policy training view without changing any answer target.

Only verified TRAIN rows enter supervision. Holdout files are copied unchanged
for provenance; their questions and answers are never parsed into training rows.
This prepares data, never starts an optimizer or approves model quality.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'day1'))
from pipeline import verified_rows
from assistant_app.backend.runtime_prompts import SYSTEM, policy_metadata


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source, output):
    source, output = Path(source).resolve(), Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('Refusing to overwrite a previous dataset')
    rows, previous = verified_rows(source)
    ids = [r.get('id') for r in rows]
    if any(not isinstance(ident, str) or not ident for ident in ids) or len(set(ids)) != len(ids):
        raise ValueError('Unique training IDs required')
    if any(r.get('split') != 'train' or r['messages'][0]['role'] != 'system' for r in rows):
        raise ValueError('Only explicit TRAIN rows with a system message are accepted')
    files = ['validation.messages.jsonl', 'test.messages.jsonl', 'ATTRIBUTION.txt']
    for name in files:
        if not (source/name).is_file() or (source/name).is_symlink():
            raise ValueError('Required source provenance file missing or symlinked: ' + name)
    if previous.get('validation_sha256') and digest(source/files[0]) != previous['validation_sha256']:
        raise ValueError('Source validation file changed')
    aligned = copy.deepcopy(rows)
    for old, new in zip(rows, aligned, strict=True):
        new['messages'][0] = {'role': 'system', 'content': SYSTEM}
        if new['messages'][1:] != old['messages'][1:]:
            raise ValueError('Only the system policy may change; questions and targets must stay unchanged')
    output.mkdir(parents=True, exist_ok=False)
    train = output/'train.messages.jsonl'
    with train.open('x', encoding='utf-8') as handle:
        for row in aligned:
            handle.write(json.dumps(row, ensure_ascii=False)+'\n')
    for name in files:
        shutil.copy2(source/name, output/name)
    metadata = {**previous,
        'train_sha256': digest(train), 'source_train_sha256': previous['train_sha256'],
        'source_manifest_sha256': digest(source/'dataset-manifest.json'),
        'application_policy': policy_metadata(), 'training_policy_sha256': policy_metadata()['text_sha256'],
        'derivation': 'Same verified TRAIN questions and targets; only system policy aligned to the shared runtime bundle.',
        'targets_changed': False, 'original_targets_changed': False,
        'evaluation_rows_in_training': False, 'validation_copied_unchanged': True,
        'validation_independent_of_policy_selection': False,
        'development_findings_informed_policy_design': True,
        'training_material_source': 'existing approved training rows only; no error ledger or holdout answers',
        'generator_sha256': digest(Path(__file__)),
        'status': 'prepared_not_trained_not_quality_accepted'}
    (output/'dataset-manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    checked, _ = verified_rows(output)
    assert len(checked) == len(rows)
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.source, args.output)
    print(json.dumps({k: result[k] for k in ('train_rows', 'train_sha256', 'source_train_sha256',
        'training_policy_sha256', 'targets_changed', 'evaluation_rows_in_training', 'status')}, indent=2))
