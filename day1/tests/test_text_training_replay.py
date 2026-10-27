import importlib.util
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'day1/4-validation'))
spec=importlib.util.spec_from_file_location('text_training_replay',ROOT/'day1/4-validation/replay_text_training.py')
replay=importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def dataset(tmp_path):
    def row(ident,split):return dict(id=ident,split=split,messages=[
        dict(role='system',content='policy'),dict(role='user',content='source question'),
        dict(role='assistant',content='target-for-review-only')])
    for name,rows in [('train',[row('train-1','train')]),('validation',[row('val-1','validation')])]:
        (tmp_path/(name+'.messages.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in rows))
    m=dict(train_rows=1,validation_rows=1,train_sha256=hashlib.sha256((tmp_path/'train.messages.jsonl').read_bytes()).hexdigest(),
           validation_sha256=hashlib.sha256((tmp_path/'validation.messages.jsonl').read_bytes()).hexdigest())
    (tmp_path/'dataset-manifest.json').write_text(json.dumps(m))
    return {'train':['train-1'],'validation':['val-1']}


def test_final_training_targets_are_excluded_from_model_input(tmp_path):
    plan=dataset(tmp_path)
    requests,references,_=replay.prepare(tmp_path,plan,'policy')
    assert 'target-for-review-only' not in json.dumps(requests)
    assert all(r['reference']=='target-for-review-only' for r in references)
    assert all(r['messages'][-1]['role']=='user' for r in requests)


def test_training_replay_cannot_silently_change_its_system_policy(tmp_path):
    plan=dataset(tmp_path)
    with pytest.raises(ValueError,match='policy differs'):replay.prepare(tmp_path,plan,'another policy')


def test_validation_manifest_is_checked_before_sampling(tmp_path):
    plan=dataset(tmp_path)
    (tmp_path/'validation.messages.jsonl').write_text('changed')
    with pytest.raises(ValueError,match='Validation file changed'):replay.prepare(tmp_path,plan,'policy')
