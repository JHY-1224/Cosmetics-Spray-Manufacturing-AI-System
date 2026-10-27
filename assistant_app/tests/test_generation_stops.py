import importlib.util
import json
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / 'scripts/launch_model.py'
spec = importlib.util.spec_from_file_location('model_launch_stops', path)
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


def fixture_model(tmp_path, configured=248044, chat=248046):
    (tmp_path/'config.json').write_text(json.dumps({'text_config': {'eos_token_id': configured}}))
    (tmp_path/'tokenizer_config.json').write_text(json.dumps({'eos_token': '<|im_end|>'}))
    (tmp_path/'tokenizer.json').write_text(json.dumps({'added_tokens': [{'id': chat, 'content': '<|im_end|>'}]}))


def test_training_chat_eos_is_not_lost_when_base_stops_only_at_endoftext(tmp_path):
    fixture_model(tmp_path)
    assert launch.generation_stop_ids(tmp_path) == [248044, 248046]
    (tmp_path/'generation_config.json').write_text(json.dumps({'eos_token_id': [248046, 248044]}))
    assert launch.generation_stop_ids(tmp_path) == [248044, 248046]


def test_unresolved_or_invalid_stop_id_refuses_launch(tmp_path):
    fixture_model(tmp_path, configured=True)
    with pytest.raises(ValueError, match='Invalid'):
        launch.generation_stop_ids(tmp_path)
    fixture_model(tmp_path)
    (tmp_path/'tokenizer.json').write_text('{"added_tokens":[]}')
    with pytest.raises(ValueError, match='Cannot establish'):
        launch.generation_stop_ids(tmp_path)


def test_text_and_vision_keep_distinct_tested_runtime_budgets():
    text = launch.runtime_options('text', '', {})
    assert '--enforce-eager' in text and text[text.index('--gdn-prefill-backend')+1]=='triton'
    assert '--mm-processor-kwargs' not in text and '--load-format' not in text
    vision = launch.runtime_options('vision', 'bitsandbytes', {})
    assert vision[vision.index('--load-format')+1]=='bitsandbytes'
    assert json.loads(vision[vision.index('--mm-processor-kwargs')+1])=={
        'min_pixels':262144,'max_pixels':1572864}
    assert '--gdn-prefill-backend' not in vision


@pytest.mark.parametrize('env', [
    {'ASSISTANT_VISION_MIN_PIXELS':'1572864','ASSISTANT_VISION_MAX_PIXELS':'262144'},
    {'ASSISTANT_VISION_MAX_PIXELS':'-1'},
    {'ASSISTANT_VISION_MAX_PIXELS':'16778240'},
    {'ASSISTANT_VISION_MIN_PIXELS':'1025'},
])
def test_invalid_pixel_budget_fails_before_launch(env):
    with pytest.raises(ValueError):launch.runtime_options('vision','',env)
