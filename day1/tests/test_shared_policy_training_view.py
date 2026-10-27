import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('shared_training_view', ROOT/'day1/2-prepare-data/prepare_shared_policy_view.py')
view = importlib.util.module_from_spec(spec)
spec.loader.exec_module(view)


def source_fixture(folder, split='train'):
    folder.mkdir()
    rows = [dict(id='fixture-training-only', split=split, messages=[
        {'role': 'system', 'content': 'historical policy'},
        {'role': 'user', 'content': 'training question'},
        {'role': 'assistant', 'content': 'approved source target, unchanged'}])]
    train = folder/'train.messages.jsonl'
    train.write_text(json.dumps(rows[0])+'\n')
    # Deliberately not parseable as training messages; only copied for provenance.
    (folder/'validation.messages.jsonl').write_text('holdout answer must not enter supervision\n')
    (folder/'test.messages.jsonl').write_text('')
    (folder/'ATTRIBUTION.txt').write_text('synthetic fixture')
    (folder/'dataset-manifest.json').write_text(json.dumps(dict(train_rows=1, validation_rows=1,
        test_rows=0, sample_ids=[rows[0]['id']], train_sha256=view.digest(train),
        validation_sha256=view.digest(folder/'validation.messages.jsonl'))))
    return rows


def test_shared_alignment_preserves_questions_targets_and_originals(tmp_path):
    source = tmp_path/'source'
    original = source_fixture(source)
    before = {f.name: f.read_bytes() for f in source.iterdir()}
    result = view.build(source, tmp_path/'aligned')
    rows = [json.loads(s) for s in (tmp_path/'aligned/train.messages.jsonl').read_text().splitlines()]
    assert rows[0]['messages'][1:] == original[0]['messages'][1:]
    assert rows[0]['messages'][0]['content'] == view.SYSTEM
    assert {f.name: f.read_bytes() for f in source.iterdir()} == before
    assert 'holdout answer' not in (tmp_path/'aligned/train.messages.jsonl').read_text()
    assert (tmp_path/'aligned/validation.messages.jsonl').read_bytes() == before['validation.messages.jsonl']
    assert result['targets_changed'] is False and result['evaluation_rows_in_training'] is False


def test_holdout_row_cannot_be_renamed_into_training(tmp_path):
    source_fixture(tmp_path/'source', split='validation')
    with pytest.raises(ValueError, match='TRAIN'):
        view.build(tmp_path/'source', tmp_path/'aligned')
    assert not (tmp_path/'aligned').exists()


def test_existing_destination_is_preserved(tmp_path):
    target = tmp_path/'existing'
    target.mkdir()
    sentinel = target/'sentinel'
    sentinel.write_bytes(b'preserve existing dataset')
    with pytest.raises(ValueError, match='overwrite'):
        view.build(tmp_path/'absent', target)
    assert sentinel.read_bytes() == b'preserve existing dataset'
