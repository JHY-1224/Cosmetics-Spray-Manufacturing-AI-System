"""Customer deployment safety/portability tests, no GPU or model generation."""
import hashlib
import io
import json
from pathlib import Path
import socket
import tarfile

import pytest

from scripts import run_finetuned_service as customer


def bundle(tmp_path):
    archives = tmp_path / 'archives'
    archives.mkdir()
    manifest = {'release_id': 'synthetic-customer-result', 'models': {}}
    for kind in ('text', 'vision'):
        config = json.dumps({'base_model_name_or_path': '/original/' + kind, 'r': 8}).encode()
        payloads = {'adapter_config.json': config, 'adapter_model.safetensors': b'synthetic test tensors'}
        path = archives / (kind + '.tar.gz')
        with tarfile.open(path, 'w:gz') as tf:
            for name, blob in payloads.items():
                member = tarfile.TarInfo(kind + '/' + name)
                member.size = len(blob)
                tf.addfile(member, io.BytesIO(blob))
        blob = path.read_bytes()
        pieces = []
        for i, part in enumerate((blob[:50], blob[50:]), 1):
            name = path.name + '.part' + str(i)
            (archives / name).write_bytes(part)
            pieces.append({'path': name, 'bytes': len(part), 'sha256': hashlib.sha256(part).hexdigest()})
        manifest['models'][kind] = {
            'run_id': 'actual-run-' + kind, 'base_model_id': 'Qwen/' + kind,
            'lineage': {'run_id': 'actual-run-' + kind, 'model_dir': '/original/' + kind,
                        'train_rows': 4, 'config': {'lora_r': 8, 'lora_alpha': 8}},
            'downloads': pieces, 'archive_bytes': len(blob), 'archive_sha256': hashlib.sha256(blob).hexdigest(),
            'files': [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                      for name, data in payloads.items()]}
    return manifest, archives


def test_portable_paths_preserve_original_bytes_and_resume(tmp_path):
    manifest, archives = bundle(tmp_path)
    runtime, bases = tmp_path / 'runtime', tmp_path / 'new-bases'
    adapters = customer.prepare_artifacts(manifest, archives, runtime, bases)
    for kind, adapter in adapters.items():
        assert json.loads((adapter / 'adapter_config.json').read_text())['base_model_name_or_path'] == str(bases / kind)
        original = runtime / 'original' / kind / 'adapter_config.json'
        assert json.loads(original.read_text())['base_model_name_or_path'] == '/original/' + kind
        assert (adapter / 'adapter_model.safetensors').read_bytes() == b'synthetic test tensors'
        assert json.loads((adapter.parent / 'run-manifest.json').read_text())['train_rows'] == 4
    stamp = (adapters['text'] / 'adapter_config.json').stat().st_mtime_ns
    assert customer.prepare_artifacts(manifest, archives, runtime, bases) == adapters
    assert (adapters['text'] / 'adapter_config.json').stat().st_mtime_ns == stamp


def test_corrupt_runtime_is_not_reused(tmp_path):
    manifest, archives = bundle(tmp_path)
    runtime = tmp_path / 'runtime'
    adapters = customer.prepare_artifacts(manifest, archives, runtime, tmp_path / 'bases')
    (adapters['vision'] / 'adapter_model.safetensors').write_bytes(b'changed')
    with pytest.raises(ValueError, match='integrity'):
        customer.prepare_artifacts(manifest, archives, runtime, tmp_path / 'bases')


def test_other_model_root_cannot_overwrite_previous_deployment(tmp_path):
    manifest, archives = bundle(tmp_path)
    customer.prepare_artifacts(manifest, archives, tmp_path / 'runtime', tmp_path / 'bases')
    with pytest.raises(ValueError, match='另一份配置'):
        customer.prepare_artifacts(manifest, archives, tmp_path / 'runtime', tmp_path / 'other')


@pytest.mark.parametrize('changes', [{'id': 'qwen-light-text-base'}, {'parent': None}, {'root': '/other/adapter'}])
def test_base_or_other_adapter_cannot_pass_as_trained_result(changes):
    card = dict(id='qwen-light-text', root='/actual/adapter', parent='qwen-light-text-base')
    card.update(changes)
    with pytest.raises(ValueError, match='LoRA'):
        customer.validate_model_card({'data': [card]}, 'qwen-light-text', Path('/actual/adapter'))


def test_occupied_port_is_preserved():
    with socket.socket() as existing:
        existing.bind(('127.0.0.1', 0))
        existing.listen()
        with pytest.raises(ValueError, match='占用'):
            customer.available_ports([existing.getsockname()[1], 5101, 5102])
        assert existing.fileno() >= 0


def test_incomplete_generation_does_not_produce_activation_proof(monkeypatch):
    def response(url, payload=None, timeout=10):
        if url.endswith('/models'):
            return {'data': [{'id': 'qwen-light-text', 'root': '/adapter', 'parent': 'qwen-light-text-base'}]}
        return {'model': 'qwen-light-text', 'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]}
    monkeypatch.setattr(customer, 'request_json', response)
    with pytest.raises(ValueError, match='生成检查'):
        customer.probe_lora('http://127.0.0.1:5101/v1', 'qwen-light-text', Path('/adapter'), 'text')
