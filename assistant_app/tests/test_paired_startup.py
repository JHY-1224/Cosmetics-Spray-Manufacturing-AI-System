from contextlib import nullcontext
import importlib.util
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

path = Path(__file__).resolve().parents[1] / 'scripts/launch_qwen_light.py'
spec = importlib.util.spec_from_file_location('paired_startup', path)
paired = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paired)


def test_readiness_requires_exact_identity_and_a_live_child(monkeypatch):
    responses = iter([b'{"data":[{"id":"wrong-model"}]}', b'{"data":[{"id":"chosen-model"}]}'])
    opener = SimpleNamespace(open=Mock(side_effect=lambda *a, **k: BytesIO(next(responses))))
    monkeypatch.setattr(paired, 'build_opener', lambda *a: opener)
    monkeypatch.setattr(paired.time, 'sleep', lambda _: None)
    paired.wait_ready(SimpleNamespace(poll=lambda: None), 'http://127.0.0.1:5101/v1', 'chosen-model', 5)
    assert opener.open.call_count == 2
    with pytest.raises(RuntimeError, match='exited'):
        paired.wait_ready(SimpleNamespace(poll=lambda: 4), 'http://127.0.0.1:5101/v1', 'chosen-model', 5)
    assert opener.open.call_count == 2


def test_wrong_identity_times_out_and_redirect_is_not_followed(monkeypatch):
    monkeypatch.setattr(paired, 'build_opener', lambda *a: SimpleNamespace(
        open=lambda *a, **k: BytesIO(b'{"data":[{"id":"other"}]}')))
    clock = iter([0, 1])
    monkeypatch.setattr(paired.time, 'monotonic', lambda: next(clock))
    with pytest.raises(TimeoutError, match='identity'):
        paired.wait_ready(SimpleNamespace(poll=lambda: None), 'http://127.0.0.1:5101/v1', 'chosen', .1)
    assert paired.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.test') is None


@pytest.mark.parametrize('first_fails', [False, True])
def test_second_engine_waits_for_first_and_only_owned_children_are_cleaned(monkeypatch, tmp_path, first_fails):
    import assistant_app.backend.settings as settings_module
    events, signals = [], []
    env = tmp_path/'paired.env'
    env.write_text('')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0')
    for kind in ('TEXT', 'VISION'):
        monkeypatch.setenv('ASSISTANT_'+kind+'_GPU_MEMORY_UTILIZATION', '.4')
        monkeypatch.delenv('ASSISTANT_'+kind+'_PORT', raising=False)
    key = SimpleNamespace(get_secret_value=lambda: '')
    settings = SimpleNamespace(text_model_base_url='http://127.0.0.1:5101/v1', text_served_model='text',
        text_api_key=key, vision_model_base_url='http://127.0.0.1:5102/v1', vision_served_model='vision',
        vision_api_key=key, text_mode='finetuned_lora', vision_mode='finetuned_lora')
    monkeypatch.setattr(settings_module, 'Settings', lambda: settings)
    monkeypatch.setattr(paired.sys, 'argv', ['launch', '--env-file', str(env)])
    monkeypatch.setattr(paired.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(paired.socket, 'socket', lambda: nullcontext(SimpleNamespace(
        bind=lambda _: None, setsockopt=lambda *a: None)))
    monkeypatch.setattr(paired.os, 'killpg', lambda pid, signal: signals.append(pid))
    children = []

    def spawn(argv, **kwargs):
        role = argv[2]
        events.append('spawn-'+role)
        child = SimpleNamespace(pid=900+len(children), poll=Mock(return_value=None), wait=lambda **kw: 0)
        children.append(child)
        return child

    def ready(child, endpoint, model, timeout, key):
        events.append('ready-'+model)
        if first_fails:
            raise TimeoutError('first engine failed readiness')
        if model == 'vision':
            children[0].poll.return_value = 3

    monkeypatch.setattr(paired.subprocess, 'Popen', spawn)
    monkeypatch.setattr(paired, 'wait_ready', ready)
    if first_fails:
        with pytest.raises(TimeoutError):
            paired.main()
        assert events == ['spawn-text', 'ready-text']
        assert signals == [900]
    else:
        with pytest.raises(SystemExit):
            paired.main()
        assert events == ['spawn-text', 'ready-text', 'spawn-vision', 'ready-vision']
        assert signals == [901]
