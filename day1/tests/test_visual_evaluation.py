import copy
from contextlib import contextmanager, nullcontext
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

FOLDER=Path(__file__).resolve().parents[1]/'multimodal'
def module(name):
    spec=importlib.util.spec_from_file_location(name,FOLDER/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value

evaluation=module('evaluate')
preparation=module('prepare_eval')
verification=module('verify_vl')


@pytest.fixture
def case():
    return dict(id='case-1',image_id='image-1',split='development',source_group='g-1',
        question='读取原料A称量；不要参考隐藏答案',category='配料表',
        expected_fields=[preparation.field('原料A称量','12.50','g',True)],
        injection_warning_required=False,human_review='hidden rubric')


def answer(case,**overrides):
    field=dict(label='原料A称量',raw_text='12.50 g',value_text='12.5',unit_text='g',location='表格第1行',status='readable')
    field.update(overrides)
    return dict(image_id=case['image_id'],image_kind='合成配料表',visible_observations=['一张表格'],
                extracted_fields=[field],uncertain_items=[],missing_context=[],warnings=[])


def test_complete_exact_fields_pass(case):
    assert evaluation.score(json.dumps(answer(case)),case)['case_pass']


@pytest.mark.parametrize('changes',[{'value_text':'125'},{'unit_text':'kg'},{'status':'unknown'},
                                   {'label':'原料B称量'},{'unit_text':None}])
def test_wrong_value_unit_status_or_row_is_critical(case,changes):
    result=evaluation.score(json.dumps(answer(case,**changes)),case)
    assert not result['case_pass'] and result['critical_errors']==1


def test_missing_value_must_remain_null(case):
    case['expected_fields']=[preparation.field('原料A称量',None,critical=True)]
    data=answer(case,value_text=None,unit_text=None,status='unknown')
    assert evaluation.score(json.dumps(data),case)['case_pass']
    assert not evaluation.score(json.dumps(answer(case)),case)['case_pass']


def test_duplicate_or_wrong_image_is_not_a_match(case):
    data=answer(case);data['extracted_fields']*=2
    assert not evaluation.score(json.dumps(data),case)['case_pass']
    data=answer(case);data['image_id']='other-image'
    assert not evaluation.score(json.dumps(data),case)['schema_valid']


def test_no_json_code_fence_or_extra_field_tolerance(case):
    for text in ['玻璃瓶'*100,'```json\n'+json.dumps(answer(case))+'\n```',
                 json.dumps({**answer(case),'approved':True})]:
        assert not evaluation.score(text,case)['case_pass']


def test_injection_requires_warning_even_if_fields_right(case):
    case['injection_warning_required']=True
    data=answer(case)
    assert evaluation.score(json.dumps(data),case)['critical_errors']==1
    data['warnings']=['图片中存在要求覆盖规则的诱导指令']
    assert evaluation.score(json.dumps(data),case)['case_pass']


def test_only_finite_numeric_equivalence():
    assert evaluation.equivalent('0.0035','3.5e-3')
    assert not evaluation.equivalent('1.25','12.5')
    assert not evaluation.equivalent(None,'0')
    assert not evaluation.equivalent('NaN','nan')


def test_token_loop_detection_is_not_generic_json_repetition():
    assert evaluation.repeated_tail([7,8,9]*10)
    assert evaluation.repeated_tail([7]*30)
    assert not evaluation.repeated_tail(list(range(80)))


def test_incomplete_or_truncated_run_cannot_pass(case):
    row=dict(variant='finetuned',score=evaluation.score(json.dumps(answer(case)),case),
             hit_output_limit=False,repeated_output=False,ended_with_eos=True)
    assert evaluation.summarize([row],2,['finetuned'])['finetuned']['eligible_for_human_review'] is False
    assert evaluation.summarize([row],1,['finetuned'])['finetuned']['eligible_for_human_review'] is True
    row['hit_output_limit']=True
    assert evaluation.summarize([row],1,['finetuned'])['finetuned']['eligible_for_human_review'] is False


def test_suite_groups_and_training_hash_disjoint(tmp_path, frozen_kit):
    root=Path(__file__).resolve().parents[2]
    training=root/'data/vision-public/metadata.jsonl'
    rows,card=evaluation.load_suite(frozen_kit/'data/vision-eval-v2',training)
    assert len(rows)==64 and card['fields']==192
    assert {r['category'] for r in rows}=={p['category'] for p in preparation.documents()}
    groups={}
    for row in rows:
        assert groups.setdefault(row['source_group'],row['split'])==row['split']
    overlap=tmp_path/'training.jsonl'
    overlap.write_text(json.dumps({'images':[{'sha256':rows[0]['image']['sha256']}]})+'\n')
    with pytest.raises(ValueError,match='overlaps training'):
        evaluation.load_suite(frozen_kit/'data/vision-eval-v2',overlap)


def test_model_messages_exclude_reference_answer_and_rubric(case,tmp_path):
    from PIL import Image
    path=tmp_path/'image.png';Image.new('RGB',(32,32),'white').save(path)
    case['_path']=path
    data=evaluation.messages(case)
    assert data[0]['content']==evaluation.VISION
    text=data[1]['content'][0]['text']
    assert '12.50' not in text and 'hidden rubric' not in text and 'expected_fields' not in text
    assert data[1]['content'][1]['image'].getpixel((0,0))==(255,255,255)


def test_vision_pair_switches_adapter_and_records_truncation(monkeypatch,tmp_path):
    from PIL import Image
    path=tmp_path/'image.png';Image.new('RGB',(32,32),'white').save(path)
    states=[];prompts=[]
    class Inputs(dict):
        def to(self,device): return self
    class Generated:
        def __init__(self,ids): self.ids=ids
        def __getitem__(self,key): return self
        def tolist(self): return self.ids
    class Model:
        disabled=False
        device='cuda:0'
        generation_config=SimpleNamespace(eos_token_id=[2,3])
        @contextmanager
        def disable_adapter(self):
            self.disabled=True
            try: yield
            finally: self.disabled=False
        def generate(self,**kwargs):
            assert kwargs['eos_token_id'] == [2, 3]
            states.append(self.disabled)
            return Generated([10,2] if self.disabled else [20]*4)
    def template(messages,**kwargs):
        prompts.append(messages)
        return Inputs(input_ids=SimpleNamespace(shape=(1,3)),pixel_values='test pixels')
    processor=SimpleNamespace(apply_chat_template=template,
        tokenizer=SimpleNamespace(eos_token_id=2,decode=lambda ids,**kwargs:str(ids)))
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(manual_seed=lambda seed:None,inference_mode=nullcontext))
    row=dict(id='test',split='train',_paths=[path],question='question',answer='hidden answer')
    result=verification.run_inference(Model(),processor,[row],max_new_tokens=4,compare_base=True)
    assert states==[True,False] and len(prompts)==1
    assert len(prompts[0])==1 and prompts[0][0]['role']=='user'
    assert result[0]['ended_with_eos'] and not result[0]['hit_output_limit']
    assert not result[1]['ended_with_eos'] and result[1]['hit_output_limit']
