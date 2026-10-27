import asyncio
import json
import time

from fastapi.testclient import TestClient
import pytest

from assistant_app.backend.app import create_app
from assistant_app.backend.settings import Settings


class Client:
    is_mock = True

    def __init__(self, settings=None, online=True, delay=0):
        self.settings = settings
        self.online, self.delay = online, delay
        self.calls = []

    async def available(self, kind):
        return self.online

    async def close(self):
        pass

    async def call(self, kind, messages, json_mode=False):
        await asyncio.sleep(self.delay)
        model = self.settings.text_served_model if self.settings else 'cloud-model'
        self.calls.append((kind, model))
        return 'model=' + model, {'model': model}


def base_model(directory):
    directory.mkdir(parents=True)
    for name, data in {'config.json': {'model_type': 'qwen3_5'},
                       'tokenizer_config.json': {'chat_template': 'native-test-template'},
                       'tokenizer.json': {}}.items():
        (directory / name).write_text(json.dumps(data))
    (directory / 'model.safetensors').write_bytes(b'fixture-file-presence-only')
    return directory


@pytest.fixture
def environment(tmp_path):
    root = tmp_path / 'models'
    root.mkdir()
    base = base_model(root / 'base/Qwen3.5-9B')
    profiles = tmp_path / 'profiles'
    profiles.mkdir()
    settings = Settings(data_dir=tmp_path / 'data', public_origin='http://testserver',
                        text_mode='approved_cloud', model_directory=root, model_profiles_dir=profiles,
                        text_model_path=base, local_text_served_model='local-base', allow_base_preview=True)
    default = Client()
    clients = []
    def factory(config):
        client = Client(config)
        clients.append(client)
        return client
    app = create_app(settings, default, factory)
    with TestClient(app) as browser:
        browser.headers['X-CSRF-Token'] = browser.post('/api/assistant/session').json()['csrf_token']
        yield browser, app, default, clients, profiles, base


def options(browser):
    response = browser.get('/api/assistant/models?refresh=true')
    assert response.status_code == 200, response.text
    return response.json()


def start(browser, model, key='model-choice-request'):
    conversation = browser.post('/api/assistant/conversations').json()['id']
    response = browser.post('/api/assistant/conversations/' + conversation + '/messages',
        json={'text': 'synthetic request', 'request_key': key, 'model_id': model})
    assert response.status_code == 200, response.text
    return conversation, response.json()['request_id']


def finish(browser, request):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        row = browser.get('/api/assistant/requests/' + request).json()
        if row['phase'] in ('done', 'failed'):
            return row
        time.sleep(.01)
    raise AssertionError('not finished')


def profile(path, base, served='local-adapter', category='local_base'):
    data = {'id': 'custom', 'name': 'Explicit local profile', 'category': category,
            'text_model_base_url': 'http://127.0.0.1:5101/v1',
            'text_served_model': served, 'text_model_path': str(base)}
    path.write_text(json.dumps(data))
    return data


def test_directory_discovery_categories_and_private_fields(environment):
    browser, app, _, _, _, base = environment
    result = options(browser)
    assert result['default_id'] == 'configured'
    assert {m['category'] for m in result['models']} == {'cloud', 'local_base'}
    assert all(m['selectable'] for m in result['models'])
    serialized = json.dumps(result)
    assert str(base) not in serialized and '127.0.0.1' not in serialized
    assert 'api_key' not in serialized and 'text_model_base_url' not in serialized
    assert not any(m['category'] == 'local_finetuned' for m in result['models'])


def test_trial_catalog_does_not_expose_stored_or_profiled_base_without_opt_in(environment):
    browser, app, default, _, profiles, base = environment
    ident = next(m['id'] for m in options(browser)['models'] if m['category']=='local_base')
    profile(profiles/'base.json', base)
    app.state.settings.allow_base_preview = False
    result = options(browser)
    assert [m['id'] for m in result['models']] == ['configured']
    assert result['unreadable_entries'] == 0
    conversation = browser.post('/api/assistant/conversations').json()['id']
    for model in (ident, 'custom'):
        response = browser.post('/api/assistant/conversations/'+conversation+'/messages',
            json={'text':'synthetic request','request_key':'hidden-base-request','model_id':model})
        assert response.status_code == 422
    assert default.calls == []
    assert base.is_dir() and (base/'model.safetensors').is_file()


def test_request_routes_to_selected_client_without_global_change(environment):
    browser, app, default, clients, _, _ = environment
    selected = next(m for m in options(browser)['models'] if m['category'] == 'local_base')
    _, request = start(browser, selected['id'])
    result = finish(browser, request)
    assert result['phase'] == 'done' and result['answer'] == 'model=local-base'
    assert result['metadata']['model_profile']['id'] == selected['id']
    assert default.calls == []
    assert app.state.settings.text_mode == 'approved_cloud'
    assert clients[-1].calls == [('text', 'local-base')]
    _, cloud = start(browser, 'configured', 'different-cloud-request')
    assert finish(browser, cloud)['answer'] == 'model=cloud-model'
    assert len(default.calls) == 1


def test_offline_local_is_not_selectable_and_does_not_fallback(environment):
    browser, app, default, _, _, _ = environment
    app.state.model_catalog.client_factory = lambda s: Client(s, online=False)
    result = options(browser)
    local = next(m for m in result['models'] if m['category'] == 'local_base')
    assert local['state'] == 'not_deployed' and not local['selectable']
    assert default.calls == []


def test_text_online_vision_base_offline_is_not_a_ready_multimodal_choice(environment):
    browser, app, default, _, _, _ = environment

    class TextOnlyClient(Client):
        async def available(self, kind):
            return kind == 'text'

    app.state.model_catalog.client_factory = lambda s: TextOnlyClient(s)
    result = options(browser)
    local = next(m for m in result['models'] if m['category'] == 'local_base')
    assert local['state'] == 'not_deployed' and not local['selectable']
    assert default.calls == []


def test_finetuned_artifact_listed_but_not_claimed_ready_without_verification(environment):
    browser, app, _, _, _, base = environment
    run = app.state.settings.model_directory / 'finetuned/Qwen3.5-9B-cosmetics/new-run'
    adapter = run / 'adapter'
    adapter.mkdir(parents=True)
    (adapter / 'adapter_config.json').write_text(json.dumps({'peft_type': 'LORA'}))
    (run / 'run-manifest.json').write_text(json.dumps({'model_dir': str(base)}))
    local = next(m for m in options(browser)['models'] if m['category'] == 'local_finetuned')
    assert local['state'] == 'artifact_verification_required'
    assert local['selectable'] is False
    assert local['text_model'] != app.state.settings.local_text_served_model


def test_profile_change_does_not_change_already_submitted_model(environment):
    browser, app, _, clients, profiles, base = environment
    file = profiles / 'local.json'
    profile(file, base, 'first-model')
    options(browser)
    for c in clients:
        c.delay = .08
    _, request = start(browser, 'custom')
    profile(file, base, 'second-model')
    result = finish(browser, request)
    assert result['answer'] == 'model=first-model'
    assert result['metadata']['model_profile']['text_model'] == 'first-model'
    assert next(m for m in options(browser)['models'] if m['id'] == 'custom')['text_model'] == 'second-model'


@pytest.mark.parametrize('model', ['https://evil.example/v1', '../models', 'not-in-server-catalog'])
def test_browser_cannot_supply_arbitrary_endpoint_or_path(environment, model):
    browser, _, default, _, _, _ = environment
    conversation = browser.post('/api/assistant/conversations').json()['id']
    response = browser.post('/api/assistant/conversations/' + conversation + '/messages',
        json={'text': 'test', 'request_key': 'invalid-model-request', 'model_id': model})
    assert response.status_code == 422
    assert default.calls == []


def test_same_request_key_with_different_model_conflicts(environment):
    browser, _, _, _, _, _ = environment
    local = next(m for m in options(browser)['models'] if m['category'] == 'local_base')
    conversation, request = start(browser, 'configured')
    finish(browser, request)
    response = browser.post('/api/assistant/conversations/' + conversation + '/messages',
        json={'text': 'synthetic request', 'request_key': 'model-choice-request', 'model_id': local['id']})
    assert response.status_code == 409


def test_invalid_profile_is_not_used_and_symlink_is_not_scanned(environment):
    browser, app, _, _, profiles, base = environment
    data = profile(profiles / 'bad.json', base)
    data['text_model_base_url'] = 'https://unapproved.example/v1'
    (profiles / 'bad.json').write_text(json.dumps(data))
    (app.state.settings.model_directory / 'escaped').symlink_to(base, target_is_directory=True)
    result = options(browser)
    assert result['unreadable_entries'] == 1
    assert not any(m['id'] == 'custom' for m in result['models'])
    assert len([m for m in result['models'] if m['category'] == 'local_base']) == 1


def test_missing_model_directory_keeps_configured_cloud(environment, tmp_path):
    _, app, _, _, _, _ = environment
    app.state.settings.model_directory = tmp_path / 'not-yet-created'
    entries, _ = app.state.model_catalog.entries()
    assert list(entries) == ['configured']


def test_different_base_model_cannot_reuse_the_9b_alias(environment):
    browser, app, _, _, _, _ = environment
    base_model(app.state.settings.model_directory / 'base/another-base')
    options_by_name = {m['name']: m for m in options(browser)['models']}
    assert options_by_name['another-base']['text_model'] != options_by_name['Qwen3.5-9B']['text_model']


def test_paired_service_is_one_entry_and_requires_both_components(environment,monkeypatch):
    browser, app, _, _, profiles, base = environment
    from assistant_app.backend import model_catalog
    data=profile(profiles/'qwen-light.json',base,category='local_finetuned')
    data.update(id='qwen-light',name='Qwen轻量级服务',vision_mode='finetuned_lora',
                vision_model_base_url='http://127.0.0.1:5102/v1',vision_served_model='paired-vision',
                vision_adapter_path=str(base.parent/'vision-adapter'))
    (profiles/'qwen-light.json').write_text(json.dumps(data))
    monkeypatch.setattr(model_catalog,'text_artifact_state',lambda s:'finetuned_lora')
    item=next(m for m in options(browser)['models'] if m['id']=='qwen-light')
    assert item['state']=='vision_artifact_verification_required'
    assert not item['selectable']
    monkeypatch.setattr(model_catalog,'vision_artifact_state',lambda s:'finetuned_lora')
    item=next(m for m in options(browser)['models'] if m['id']=='qwen-light')
    assert item['selectable'] and item['vision_model']=='paired-vision'
    async def text_only(kind): return kind=='text'
    route=app.state.model_catalog.resolve('qwen-light')
    route.client.available=text_only
    item=next(m for m in options(browser)['models'] if m['id']=='qwen-light')
    assert not item['selectable'] and item['state']=='not_deployed'
