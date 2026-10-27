import copy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'day1'))
from pipeline import audit_text_labels, tokenize_example


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


prepare = module('prepare_app_training', ROOT / 'day1/2-prepare-data/prepare_app_training.py')
capture = module('capture_app_messages', ROOT / 'day1/4-validation/capture_app_messages.py')
probe = module('probe_app_text', ROOT / 'day1/4-validation/probe_app_text.py')
supplement = module('prepare_teaching_supplement', ROOT / 'day1/2-prepare-data/prepare_teaching_supplement.py')
holdout_probe = module('probe_teaching_holdout', ROOT / 'day1/4-validation/probe_teaching_holdout.py')
reasoning_probe = module('probe_text_reasoning', ROOT / 'day1/4-validation/probe_text_reasoning.py')
policy_probe = module('probe_text_policy', ROOT / 'day1/4-validation/probe_text_policy.py')


def test_reasoning_is_never_treated_as_final_answer_without_unique_native_boundary():
    assert reasoning_probe.final_tokens([3, 4, 9, 5, 6], 9) == ([5, 6], 3)
    assert reasoning_probe.final_tokens([3, 4, 5], 9) == ([], None)
    assert reasoning_probe.final_tokens([9, 3, 9, 4], 9) == ([], None)


def test_policy_control_changes_only_system_and_preserves_original_inputs():
    rows = [{'id': 'fixture', 'messages': [{'role': 'system', 'content': 'original'},
                                         {'role': 'user', 'content': 'same question'}]}]
    before = copy.deepcopy(rows)
    result = policy_probe.policy_rows(rows, 'candidate', True)
    assert rows == before
    assert [r['id'] for r in result] == ['fixture--original', 'fixture--candidate']
    assert [r['messages'][0]['content'] for r in result] == ['original', 'candidate']
    assert all(r['messages'][1] == rows[0]['messages'][1] for r in result)


def test_chat_generation_stops_at_supervised_eos_even_when_base_config_differs():
    assert probe.stop_token_ids(248044, 248046) == [248044, 248046]
    assert probe.stop_token_ids([248046, 248044], 248046) == [248046, 248044]
    assert probe.stop_token_ids(None, 248046) == [248046]
    assert probe.stop_token_ids(248044, 248046, 'legacy') == [248044]


@pytest.mark.parametrize('configured,chat_eos', [
    (None, None), (True, 248046), (-1, 248046), ([1, True], 248046), ('248044', 248046)])
def test_invalid_generation_eos_is_rejected(configured, chat_eos):
    with pytest.raises(ValueError, match='EOS'):
        probe.stop_token_ids(configured, chat_eos)


class NativeTokenizer:
    is_fast = True
    eos_token_id = ord('~')

    def apply_chat_template(self, messages, **kwargs):
        text = ''.join({'system': 'S', 'user': 'U', 'assistant': 'A'}[m['role']] + m['content'] + '~\n'
                       for m in messages)
        if kwargs.get('add_generation_prompt'):
            text += 'A'
        return [ord(c) for c in text] if kwargs.get('tokenize', True) else text

    def __call__(self, text, **kwargs):
        return {'input_ids': [ord(c) for c in text], 'offset_mapping': [(i, i + 1) for i in range(len(text))]}

    def decode(self, ids, **kwargs):
        return ''.join(chr(i) for i in ids)


@pytest.fixture
def tensors():
    tokenizer = NativeTokenizer()
    messages = [{'role': 'system', 'content': 'policy'}, {'role': 'user', 'content': 'question'},
                {'role': 'assistant', 'content': 'answer'}]
    return tokenizer, messages, tokenize_example(tokenizer, messages, 1000)


def test_audit_accepts_only_native_answer_eos_and_trailing_separator(tensors):
    tokenizer, messages, item = tensors
    result = audit_text_labels(tokenizer, messages, item)
    assert result['passed'] and result['eos_supervised'] == 1
    assert result['supervised_native_separators'] == 1


def test_missing_eos_is_rejected(tensors):
    tokenizer, messages, item = tensors
    item['labels'][-2] = -100
    with pytest.raises(ValueError, match='Missing'):
        audit_text_labels(tokenizer, messages, item)


@pytest.mark.parametrize('position', [0, 15])
def test_system_user_or_user_separator_supervision_is_rejected(tensors, position):
    tokenizer, messages, item = tensors
    item['labels'][position] = item['input_ids'][position]
    with pytest.raises(ValueError, match='Unexpected'):
        audit_text_labels(tokenizer, messages, item)


def test_targets_unchanged_in_multiturn_training_view():
    original = [{'role': 'system', 'content': 'old policy'}, {'role': 'user', 'content': 'first question'},
                {'role': 'assistant', 'content': 'source answer'}, {'role': 'user', 'content': 'second question'},
                {'role': 'assistant', 'content': '{"source_value":7}'}]
    before = copy.deepcopy(original)
    result = prepare.app_view(original)
    assert original == before
    assert [m['content'] for m in result if m['role'] == 'assistant'] == ['source answer', '{"source_value":7}']
    assert 'original_images_expired' in result[1]['content']
    assert 'user_original_question' in result[3]['content']


def test_training_envelope_matches_actual_application_request():
    import json
    question = '请读取本次明确提供的资料，保持未知值。'
    rows = capture.capture([{'id': 'fixture-question', 'prompt': question}])
    actual = json.loads(rows[0]['messages'][1]['content'])
    assert prepare.current_input(question) == actual


def test_teaching_holdout_targets_are_excluded_and_parameter_groups_are_disjoint(tmp_path):
    import hashlib
    import json
    training = supplement.teaching_case('volume_density', 0)
    heldout = supplement.teaching_case('volume_density', 0, heldout=True)
    assert training['parameter_group'] != heldout['parameter_group']
    path = tmp_path/'validation.messages.jsonl'
    path.write_text(json.dumps(heldout, ensure_ascii=False)+'\n')
    manifest = {'validation_rows': 1, 'validation_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'sample_ids': [training['id']]}
    (tmp_path/'dataset-manifest.json').write_text(json.dumps(manifest))
    requests, _ = holdout_probe.checked_holdout(tmp_path)
    assert [m['role'] for m in requests[0]['messages']] == ['system', 'user']
    assert 'expected' not in requests[0]
    assert heldout['messages'][-1]['content'] not in str(requests)
    path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError, match='changed'):
        holdout_probe.checked_holdout(tmp_path)
