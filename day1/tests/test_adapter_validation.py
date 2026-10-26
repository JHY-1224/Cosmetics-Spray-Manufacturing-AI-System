"""Check failure gates without GPU imports or real model weights."""
import importlib.util
from contextlib import contextmanager, nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / '4-validation/3-test-adapter.py'
spec = importlib.util.spec_from_file_location('adapter_validation', SCRIPT)
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class AdapterValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.base = self.root / 'base'
        self.base.mkdir()
        for name in ('config.json', 'download-verified.json'):
            (self.base / name).write_text('{}')
        self.adapter = self.root / 'run-fixture' / 'adapter'
        self.adapter.mkdir(parents=True)
        config = {'base_model_name_or_path': str(self.base), 'peft_type': 'LORA',
                  'target_modules': ['q_proj'], 'r': 32, 'lora_alpha': 32}
        (self.adapter / 'adapter_config.json').write_text(json.dumps(config))
        for name in ('adapter_model.safetensors', 'tokenizer.json',
                     'tokenizer_config.json', 'chat_template.jinja'):
            (self.adapter / name).write_bytes(b'synthetic file inventory fixture, not real weights')
        (self.adapter.parent / 'run-manifest.json').write_text(json.dumps({
            'run_id': 'run-fixture', 'train_rows': 36, 'validation_rows': 0, 'test_rows': 0,
            'config': {'lora_r': 32, 'lora_alpha': 32}}))

    def test_inventory_has_real_hash_and_honest_scope(self):
        result = validation.inspect_adapter(self.adapter, self.base)
        self.assertEqual(result['files']['adapter_model.safetensors']['sha256'],
                         validation.file_digest(self.adapter / 'adapter_model.safetensors'))
        self.assertIn('not a comparison', result['integrity_scope'])

    def test_base_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, '基座'):
            validation.inspect_adapter(self.adapter, self.root / 'other-base')

    def test_incomplete_adapter_rejected(self):
        (self.adapter / 'adapter_model.safetensors').write_bytes(b'')
        with self.assertRaisesRegex(ValueError, '为空'):
            validation.inspect_adapter(self.adapter, self.base)

    def test_wrong_training_parameters_rejected(self):
        path = self.adapter / 'adapter_config.json'
        value = json.loads(path.read_text())
        value['r'] = 16
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, '参数'):
            validation.inspect_adapter(self.adapter, self.base)

    def test_duplicate_case_ids_rejected(self):
        path = self.root / 'cases.json'
        path.write_text(json.dumps([{'id': 'X', 'prompt': 'q', 'review': ['r']}] * 2))
        with self.assertRaisesRegex(ValueError, '重复'):
            validation.load_cases(path)

    def test_empty_prompts_rejected(self):
        path = self.root / 'cases.json'
        path.write_text(json.dumps([{'id': 'X', 'prompt': ' ', 'review': ['r']}]))
        with self.assertRaisesRegex(ValueError, 'prompt'):
            validation.load_cases(path)

    def test_default_cli_no_gpu_and_no_overwrite(self):
        output = self.root / 'report'
        command = [sys.executable, str(SCRIPT), '--adapter', str(self.adapter),
                   '--base-dir', str(self.base), '--report-dir', str(output), '--limit', '2']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((output / 'run.json').read_text())
        self.assertEqual(state['status'], 'files_checked_no_inference')
        self.assertEqual(state['quality_verdict'], 'not_evaluated')
        self.assertEqual(state['case_count'], 2)
        self.assertFalse(state['gpu_requested'])
        self.assertFalse((output / 'responses.jsonl').exists())
        previous = (output / 'run.json').read_bytes()
        repeated = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual((output / 'run.json').read_bytes(), previous)

    def test_failure_record_is_preserved(self):
        output = self.root / 'failed-report'
        result = subprocess.run([sys.executable, str(SCRIPT), '--adapter', str(self.root / 'missing'),
                                 '--report-dir', str(output)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads((output / 'run.json').read_text())['status'], 'failed')

    def test_paired_inference_switches_adapter_without_leaking_review(self):
        class Tensor:
            shape = (1, 3)

            def to(self, device):
                return self

        class Generated:
            def __init__(self, ids):
                self.ids = ids

            def __getitem__(self, key):
                return self

            def tolist(self):
                return self.ids

        class Model:
            disabled = False
            device = 'cuda:0'
            peft_config = {'default': {}}
            active_adapters = ['default']
            generation_config = SimpleNamespace(eos_token_id=2)

            def eval(self):
                pass

            @contextmanager
            def disable_adapter(self):
                self.disabled = True
                try:
                    yield
                finally:
                    self.disabled = False

            def generate(self, **kwargs):
                self_check.append(self.disabled)
                return Generated([10 if self.disabled else 20, 2])

        prompts, self_check = [], []
        model = Model()
        tokenizer = SimpleNamespace(
            apply_chat_template=lambda messages, **kwargs: prompts.append(messages) or 'prompt only',
            eos_token_id=2, pad_token_id=0,
            decode=lambda ids, **kwargs: 'base answer' if ids[0] == 10 else 'tuned answer')

        class Tokenizer:
            def __getattr__(self, name):
                return getattr(tokenizer, name)

            def __call__(self, text, **kwargs):
                return {'input_ids': Tensor()}

        cuda = SimpleNamespace(is_available=lambda: True, get_device_name=lambda n: 'fake GPU',
                               synchronize=lambda: None, reset_peak_memory_stats=lambda: None,
                               max_memory_allocated=lambda: 1024**3)
        torch = SimpleNamespace(cuda=cuda, manual_seed=lambda n: None, inference_mode=nullcontext)
        fast = SimpleNamespace(from_pretrained=lambda **kwargs: (model, Tokenizer()),
                               for_inference=lambda model: None)
        output = self.root / 'inference-fixture'
        output.mkdir()
        cases = [{'id': 'X', 'prompt': 'synthetic question', 'review': ['secret rubric']}]
        with patch.dict(sys.modules, {'torch': torch, 'unsloth': SimpleNamespace(FastModel=fast)}), \
                patch.dict('os.environ', {}, clear=False), \
                patch.object(validation.importlib.metadata, 'version', return_value='fake'), \
                redirect_stdout(io.StringIO()):
            validation.infer(self.adapter, cases, output, 4096, 768)
        rows = [json.loads(line) for line in (output / 'responses.jsonl').read_text().splitlines()]
        self.assertEqual(self_check, [True, False])
        self.assertFalse(model.disabled)
        self.assertEqual(prompts, [[{'role': 'user', 'content': 'synthetic question'}]])
        self.assertEqual([row['answer'] for row in rows], ['base answer', 'tuned answer'])
        self.assertTrue(all(row['ended_with_eos'] for row in rows))
        self.assertTrue(all(row['review_status'] == 'pending_human_review' for row in rows))


if __name__ == '__main__':
    unittest.main()
