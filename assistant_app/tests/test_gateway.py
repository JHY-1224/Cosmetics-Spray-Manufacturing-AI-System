from fastapi.testclient import TestClient
import pytest
from assistant_app.backend.app import create_app
from assistant_app.backend.settings import Settings
from assistant_app.tests.test_assistant import FakeModels, new, session, enable_feishu, signed_event, feishu_event

KEY = 'synthetic-gateway-key-at-least-32-characters'


def test_remote_origin_or_listen_cannot_expose_anonymous_assistant():
    with pytest.raises(ValueError):
        Settings(public_origin='https://assistant.example.com', secure_cookie=True)
    with pytest.raises(ValueError):
        Settings(host='0.0.0.0')
    Settings(public_origin='https://assistant.example.com',secure_cookie=True,gateway_key=KEY)


def test_gateway_requires_key_and_user_even_for_static_or_status(tmp_path):
    settings = Settings(data_dir=tmp_path,public_origin='http://testserver',gateway_key=KEY)
    with TestClient(create_app(settings,FakeModels())) as c:
        for path in ['/', '/api/assistant/status','/health/live']:
            assert c.get(path).status_code == 403
        c.headers['X-Assistant-Gateway-Key'] = KEY
        assert c.get('/health/live').status_code == 403
        c.headers['X-Assistant-User'] = 'authorized-user'
        assert c.get('/health/live').status_code == 200
        c.headers['X-Assistant-Gateway-Key'] = 'wrong'
        assert c.post('/api/assistant/session').status_code == 403


def test_cookie_cannot_cross_authenticated_gateway_accounts(tmp_path):
    settings=Settings(data_dir=tmp_path,public_origin='http://testserver',gateway_key=KEY)
    with TestClient(create_app(settings,FakeModels())) as c:
        c.headers.update({'X-Assistant-Gateway-Key':KEY,'X-Assistant-User':'account-A'})
        session(c)
        conv=new(c)
        assert c.get('/api/assistant/conversations/'+conv).status_code==200
        c.headers['X-Assistant-User']='account-B'
        assert c.get('/api/assistant/conversations/'+conv).status_code==401
        session(c)
        assert c.get('/api/assistant/conversations/'+conv).status_code==404
        assert c.get('/api/assistant/conversations').json()==[]


def test_channel_callback_still_requires_its_own_signature(tmp_path):
    settings=Settings(data_dir=tmp_path,public_origin='http://testserver',gateway_key=KEY)
    app=create_app(settings,FakeModels())
    with TestClient(app) as c:
        enable_feishu(app)
        assert signed_event(c,feishu_event(),'wrong-signature-secret').status_code==403
        assert signed_event(c,feishu_event()).json()['accepted'] is True
        assert c.get('/api/assistant/status').status_code==403
