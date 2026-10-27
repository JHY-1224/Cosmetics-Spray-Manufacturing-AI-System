import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('http_visual_evaluation', ROOT / 'day1/multimodal/evaluate_http.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_subset_keeps_full_denominator_and_cannot_select_sealed_or_duplicate_cases():
    rows = [{'id': 'dev-1', 'split': 'development'}, {'id': 'dev-2', 'split': 'development'},
            {'id': 'sealed', 'split': 'locked'}]
    selected, expected = probe.selected_cases(rows, ['dev-2'])
    assert [r['id'] for r in selected] == ['dev-2'] and expected == 2
    for ids, limit in [(['sealed'], None), (['dev-1', 'dev-1'], None), (['dev-1'], 1)]:
        with pytest.raises(ValueError, match='development'):
            probe.selected_cases(rows, ids, limit)


def setup_transport(tmp_path, monkeypatch, response_factory):
    class Tokenizer:
        def __call__(self, text, **kwargs):
            return {'input_ids': list(text.encode())}
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(AutoTokenizer=SimpleNamespace(
        from_pretrained=lambda *a, **kw: Tokenizer())))
    posts = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'base'}]})
        posts.append(json.loads(request.content))
        return response_factory(request)
    original_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original_client(
        transport=httpx.MockTransport(handler), **kw))
    args = SimpleNamespace(tokenizer=tmp_path, base_url='http://127.0.0.1:5112/v1', output=tmp_path)
    evaluation = SimpleNamespace(
        messages=lambda case: [{'role': 'system', 'content': 'extraction policy'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'Read only visible data'},
                {'type': 'image', 'image': Image.new('RGB', (8, 8), 'white')}]}],
        repeated_tail=lambda ids: False,
        score=lambda answer, case: dict(schema_valid=True, fields_correct=1, fields_total=1,
                                       critical_errors=0, case_pass=True, errors=[]))
    return args, evaluation, posts


def test_timeout_preserves_intent_without_retry(tmp_path, monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout('unknown outcome', request=request)
    args, evaluation, posts = setup_transport(tmp_path, monkeypatch, timeout)
    with pytest.raises(httpx.ReadTimeout):
        probe.run(args, evaluation, [{'id': 'dev-1'}], 1, {'base': 'base'})
    assert len(posts) == 1
    intent = json.loads((tmp_path / 'dev-1-base-request.json').read_text())
    assert intent['status'] == 'prepared_no_confirmed_response'
    assert not (tmp_path / 'dev-1-base-http.json').exists()


def test_wrong_model_response_is_preserved_and_rejected(tmp_path, monkeypatch):
    args, evaluation, posts = setup_transport(tmp_path, monkeypatch, lambda request: httpx.Response(
        200, json={'model': 'wrong', 'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}]}))
    with pytest.raises(ValueError, match='identity'):
        probe.run(args, evaluation, [{'id': 'dev-1'}], 1, {'base': 'base'})
    assert len(posts) == 1
    assert json.loads((tmp_path / 'dev-1-base-http.json').read_text())['model'] == 'wrong'


def test_length_finish_cannot_pass_or_claim_eos(tmp_path, monkeypatch):
    args, evaluation, posts = setup_transport(tmp_path, monkeypatch, lambda request: httpx.Response(
        200, json={'model': 'base', 'choices': [{'message': {'content': '{}'}, 'finish_reason': 'length'}]}))
    result = probe.run(args, evaluation, [{'id': 'dev-1'}], 1, {'base': 'base'})
    assert result['base']['truncated'] == 1
    assert result['base']['eligible_for_human_review'] is False
    row = json.loads((tmp_path / 'responses.jsonl').read_text())
    assert row['ended_with_eos'] is None
    assert row['normal_finish'] is False
    assert posts[0]['messages'][-1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
