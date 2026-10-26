"""Copy only the dataset package's approved default split, never wildcard candidates."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import SOURCE, PREPARED, ROOT, read_messages, sha256


def main():
    train = SOURCE / 'data/train.messages.jsonl'
    rows = read_messages(train)
    if not rows:
        raise ValueError('Default training split is empty')
    records = [json.loads(line) for line in (SOURCE / 'data/records.jsonl').read_text().splitlines() if line.strip()]
    eligible = [x for x in records if x.get('default_eligible') and x['split'] == 'train'
                and x.get('use_status') == 'approved_for_intended_training']
    if [x['messages'] for x in eligible] != [x['messages'] for x in rows]:
        raise ValueError('Default export does not match eligible source records')
    for split in ('train', 'validation', 'test'):
        source = SOURCE / f'data/{split}.messages.jsonl'
        target = PREPARED / source.name
        if target.exists() and sha256(target) != sha256(source):
            raise FileExistsError(f'Refusing to replace changed data: {target}')
        shutil.copy2(source, target)
    for name in ('ATTRIBUTION.txt', 'DATA_CARD.md'):
        target = PREPARED / name
        if target.exists() and sha256(target) != sha256(SOURCE / name):
            raise FileExistsError(f'Refusing to replace changed provenance: {target}')
        shutil.copy2(SOURCE / name, target)
    manifest = {'source_package': str(SOURCE.relative_to(ROOT)),
                'source_train': str(train.relative_to(ROOT)), 'train_rows': len(rows),
                'train_sha256': sha256(train), 'sample_ids': [x['sample_id'] for x in eligible],
                'validation_rows': len(read_messages(PREPARED / 'validation.messages.jsonl')),
                'test_rows': len(read_messages(PREPARED / 'test.messages.jsonl')),
                'supervision': 'all assistant answers, user/system/padding masked',
                'expert_review': 'not_reviewed_by_domain_expert',
                'excluded': 'candidates, curation, by_domain, legacy, prompt_completion duplicate views'}
    (PREPARED / 'dataset-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
