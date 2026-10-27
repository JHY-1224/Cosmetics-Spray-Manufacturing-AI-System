import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

module_path = Path(__file__).resolve().parents[1] / 'multimodal/dataset.py'
spec = importlib.util.spec_from_file_location('vision_dataset', module_path)
dataset = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dataset)


@pytest.fixture
def sample(tmp_path):
    image = tmp_path / 'sample.png'
    Image.new('RGB',(32,32),'white').save(image)
    row={'id':'a','source_group':'source-a','split':'train','expert_reviewed':True,
         'question':'读取图片','answer':'合成白色测试图','source_url':'https://example.org/fixture',
         'images':[{'path':image.name,'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]}
    return tmp_path/'metadata.jsonl', row


def write(path, rows):
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))


def test_image_pixels_loaded_not_just_filename(sample):
    path,row=sample;write(path,[row])
    result=dataset.conversation(dataset.load_records(path)[0])
    assert result['messages'][0]['content'][1]['image'].size==(32,32)
    assert result['messages'][1]['content'][0]['text']==row['answer']


def test_training_dataset_uses_hf_type_and_lazy_pixels(sample, monkeypatch):
    hf = pytest.importorskip('datasets', reason='HF dataset contract requires the training environment')
    path, row = sample
    write(path, [row])
    rows = dataset.load_records(path)
    original = dataset.conversation
    calls = []
    def tracked(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(dataset, 'conversation', tracked)
    data = dataset.training_dataset(rows)
    assert isinstance(data, hf.Dataset)
    assert not calls  # Do not eagerly decode the full collection into memory.
    item = data[0]
    assert len(calls) == 1
    assert set(item) == {'messages'}
    image = item['messages'][0]['content'][1]['image']
    assert image.mode == 'RGB' and image.getpixel((0, 0)) == (255, 255, 255)
    assert item['messages'][1]['content'][0]['text'] == row['answer']


def test_training_dataset_batch_select_and_multiple_images(sample):
    pytest.importorskip('datasets', reason='HF dataset contract requires the training environment')
    path, row = sample
    write(path, [row])
    first = dataset.load_records(path)[0]
    other = {**first, 'question': '另一问题', 'answer': '另一答案', '_paths': first['_paths'] * 2}
    data = dataset.training_dataset([first, other])
    batch = data[[1, 0]]
    assert len(batch['messages']) == 2
    assert batch['messages'][0][0]['content'][0]['text'] == '另一问题'
    assert len(batch['messages'][0][0]['content']) == 3
    assert all(isinstance(c['image'], Image.Image) for c in batch['messages'][0][0]['content'][1:])
    assert data.select([1])[0]['messages'][1]['content'][0]['text'] == '另一答案'
    assert next(iter(data))['messages'][0]['content'][0]['text'] == row['question']


def test_unreviewed_requires_development_opt_in(sample):
    path,row=sample;row['expert_reviewed']=False;write(path,[row])
    with pytest.raises(ValueError,match='expert review'): dataset.load_records(path)
    assert len(dataset.load_records(path,allow_unreviewed=True))==1


def test_optional_system_is_preserved_without_changing_old_rows(sample):
    path, row = sample
    row['system'] = '只输出可见证据 JSON；缺失数值为 null。'
    write(path, [row])
    messages = dataset.conversation(dataset.load_records(path)[0])['messages']
    assert [m['role'] for m in messages] == ['system', 'user', 'assistant']
    assert messages[0]['content'] == row['system']
    assert messages[1]['content'][0]['text'] == row['question']


def test_system_survives_lazy_hf_transform_and_mixed_batch(sample):
    pytest.importorskip('datasets', reason='HF dataset contract requires the training environment')
    path, row = sample
    write(path, [row])
    original = dataset.load_records(path)[0]
    policy = '保持缺项为空，不推断现场数值。'
    data = dataset.training_dataset([original, {**original, 'system': policy}])
    batch = data[[1, 0]]['messages']
    assert batch[0][0] == {'role': 'system', 'content': policy}
    assert [m['role'] for m in batch[1]] == ['user', 'assistant']
    assert data.select([1])[0]['messages'][0]['content'] == policy


@pytest.mark.parametrize('system', ['', '  ', None, {'role': 'system'}])
def test_invalid_system_is_rejected(sample, system):
    path, row = sample
    row['system'] = system
    write(path, [row])
    with pytest.raises(ValueError, match='system must be'):
        dataset.load_records(path)


def test_replaced_image_rejected(sample):
    path,row=sample;write(path,[row]);(path.parent/'sample.png').write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash mismatch'): dataset.load_records(path)


@pytest.mark.parametrize('same_group',[True,False])
def test_group_and_duplicate_image_cannot_leak(sample,same_group):
    path,row=sample;other=copy.deepcopy(row);other.update(id='b',split='test')
    if not same_group: other['source_group']='other'
    write(path,[row,other])
    with pytest.raises(ValueError,match='leaks'): dataset.load_records(path)


def test_image_escape_rejected(sample):
    path,row=sample;row['images'][0]['path']='../elsewhere.png';write(path,[row])
    with pytest.raises(ValueError,match='path escape'): dataset.load_records(path)


def test_public_dataset_is_portable():
    path=Path(__file__).resolve().parents[2]/'data/vision-public/metadata.jsonl'
    rows=dataset.load_records(path,allow_unreviewed=True)
    assert len(rows)==20
    assert len({r['source_group'] for r in rows})==1
    assert all(r['rights_status']=='CC_BY_4.0_declared' for r in rows)
