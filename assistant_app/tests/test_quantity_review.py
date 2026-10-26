from fastapi.testclient import TestClient
import pytest

from assistant_app.backend.app import create_app
from assistant_app.backend.quantity_review import quantity_notices
from assistant_app.backend.settings import Settings
from assistant_app.tests.test_assistant import FakeModels, session, new, submit, wait


@pytest.mark.parametrize('text,ratio', [
    ('原来20克，这次80克，需要继续加吗？','4'),
    ('原来加水20g，现在加水80g','4'),
    ('基准0.02kg，本次80克','4'),
    ('原来用量100毫升，现在25mL','0.25'),
])
def test_large_explicit_change_requires_review(text,ratio):
    notices = quantity_notices(text)
    assert notices[0]['code'] == 'quantity_change_review'
    assert notices[0]['ratio'] == ratio
    assert '执行批准' in notices[0]['message']
    assert notices[0]['source'] == 'user_statement'


def test_unit_conversion_prevents_false_large_change():
    assert quantity_notices('原来1kg，这次1000g') == []
    assert quantity_notices('原来20克，现在21克') == []


@pytest.mark.parametrize('text', ['原来20g，现在80mL', '原来加水20g，现在加色浆80g'])
def test_different_basis_never_computed_as_comparable(text):
    notices = quantity_notices(text)
    assert notices[0]['code'] == 'quantity_context_mismatch'
    assert 'ratio' not in notices[0]


def test_no_baseline_and_unrelated_questions():
    assert quantity_notices('添加80克固化剂可以吗')[0]['code'] == 'quantity_basis_required'
    assert quantity_notices('为什么提示词注入有风险？') == []
    assert quantity_notices('读取温度-20摄氏度的图表') == []


@pytest.mark.parametrize('text', ['原来0g，现在80g', '原来20g，现在-1g'])
def test_invalid_quantity_basis(text):
    assert quantity_notices(text)[0]['code'] == 'quantity_invalid_baseline'


def test_extreme_but_bounded_input_does_not_crash():
    assert quantity_notices('原来0.000000000001mg，现在9999999999999999kg')[0]['code'] == 'quantity_change_review'


def test_notice_is_durable_even_if_model_unavailable(tmp_path):
    settings = Settings(data_dir=tmp_path, public_origin='http://testserver',
                        text_mode='base_preview', allow_base_preview=True)
    models = FakeModels()
    models.text_failure = True
    with TestClient(create_app(settings, models)) as client:
        session(client)
        conv = new(client)
        ident = submit(client, conv, '原来20克，这次80克').json()['request_id']
        result = wait(client, ident)
        assert result['phase'] == 'failed'
        assert result['metadata']['review_notices'][0]['ratio'] == '4'
        restored = client.get('/api/assistant/conversations/'+conv).json()['messages'][0]
        assert restored['metadata']['review_notices'] == result['metadata']['review_notices']
