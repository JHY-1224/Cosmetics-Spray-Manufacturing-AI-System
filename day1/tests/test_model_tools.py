"""Offline download-integrity and text/vision launcher boundaries; no real GPU."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


download = load('download_tools', ROOT / 'day1/0-download-model/1-download.py')
chat = load('chat_tools', ROOT / 'day1/4-validation/2-chat.py')


class ModelToolsTests(unittest.TestCase):
    def test_full_hash_required_and_model_identity_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dest = root / 'models/base/Qwen3.5-9B'
            dest.mkdir(parents=True)
            weight = dest / 'model.safetensors'
            weight.write_bytes(b'fixture weights')
            item = {'Path': weight.name, 'Size': weight.stat().st_size,
                    'Sha256': hashlib.sha256(weight.read_bytes()).hexdigest(), 'Revision': 'a' * 40}
            manifest = {'model_id': 'Qwen/Qwen3.5-9B', 'files': [item]}
            (root / 'models/Qwen3.5-9B.manifest.json').write_text(json.dumps(manifest))
            with patch.object(download, 'ROOT', root):
                weight.write_bytes(b'corrupt weights')  # Same size, different contents.
                with self.assertRaisesRegex(RuntimeError, 'SHA256 mismatch'):
                    download.download_model('Qwen3.5-9B', True)
                self.assertFalse((dest / 'download-verified.json').exists())
                weight.write_bytes(b'fixture weights')
                download.download_model('Qwen3.5-9B', True)
                report = json.loads((dest / 'download-verified.json').read_text())
                self.assertEqual(report['files'][0]['sha256'], item['Sha256'])
            with self.assertRaisesRegex(ValueError, 'Unexpected'):
                download.validate_manifest(manifest, 'Qwen3-VL-8B-Instruct')
            item['Path'] = '../outside'
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                download.validate_manifest(manifest, 'Qwen3.5-9B')

    def test_vision_mode_does_not_disable_vision(self):
        with tempfile.TemporaryDirectory() as temporary:
            stub = Path(temporary) / 'uv'
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            stub.chmod(0o700)
            env = dict(os.environ, PATH=temporary + os.pathsep + os.environ.get('PATH', ''),
                       COSMETICS_MODEL_DIR='/explicit/model', COSMETICS_MODEL_PORT='8102',
                       COSMETICS_SERVED_MODEL_NAME='cosmetics-qwen3vl-8b')
            launcher = ['sh', str(ROOT / 'day1/4-validation/1-launch-vllm.sh')]
            for mode in ('text', 'vision'):
                result = subprocess.run(launcher, env=dict(env, COSMETICS_MODEL_MODE=mode),
                                        capture_output=True, text=True, check=True)
                args = result.stdout.splitlines()
                self.assertEqual('--language-model-only' in args, mode == 'text')
                self.assertIn('cosmetics-qwen3vl-8b', args)
                self.assertIn('8102', args)
            result = subprocess.run(launcher, env=dict(env, COSMETICS_MODEL_MODE='invalid'),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)

    def test_explicit_local_image_becomes_inline_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'fixture.png'
            image.write_bytes(b'image payload fixture')
            body = chat.build_body('vision-model', 'Describe only visible details', image)
            content = body['messages'][0]['content']
            self.assertEqual(body['model'], 'vision-model')
            self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
            image.write_bytes(b'')
            with self.assertRaises(ValueError):
                chat.build_body('vision-model', 'question', image)


if __name__ == '__main__':
    unittest.main()
