import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location('cosmetics_service', Path(__file__).resolve().parents[1] / '5-service/service-v2.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        # No lifespan: these are contract checks using a fake engine, never GPU claims.
        self.client = TestClient(service.app)
        service.app.state.context_length = 64
        service.app.state.tokenizer = SimpleNamespace(apply_chat_template=lambda *a, **k: [1, 2, 3])

    def test_unknown_model(self):
        result = self.client.post('/v1/chat/completions', json={
            'model': 'other', 'messages': [{'role': 'user', 'content': '问题'}]})
        self.assertEqual(result.status_code, 404)

    def test_rejects_stream_and_extra_fields(self):
        result = self.client.post('/v1/chat/completions', json={
            'messages': [{'role': 'user', 'content': '问题'}], 'stream': True})
        self.assertEqual(result.status_code, 422)

    def test_context_limit(self):
        result = self.client.post('/v1/chat/completions', json={
            'messages': [{'role': 'user', 'content': '问题'}], 'max_tokens': 64})
        self.assertEqual(result.status_code, 422)

    def test_invalid_history(self):
        result = self.client.post('/v1/chat/completions', json={
            'messages': [{'role': 'assistant', 'content': '不是用户问题'}], 'max_tokens': 8})
        self.assertEqual(result.status_code, 422)

    def test_response_contract_and_real_token_counts(self):
        import asyncio
        class Engine:
            async def generate(self, prompt, params, request_id):
                yield SimpleNamespace(outputs=[SimpleNamespace(text='回答', token_ids=[8, 9], finish_reason='stop')])
        vllm = ModuleType('vllm')
        vllm.SamplingParams = lambda **kwargs: kwargs
        sampling = ModuleType('vllm.sampling_params')
        sampling.StructuredOutputsParams = lambda **kwargs: kwargs
        service.app.state.engine = Engine()
        service.app.state.slots = asyncio.Semaphore(2)
        with patch.dict('sys.modules', {'vllm': vllm, 'vllm.sampling_params': sampling}):
            result = self.client.post('/v1/chat/completions', json={
                'messages': [{'role': 'user', 'content': '问题'}], 'max_tokens': 8})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['usage'], {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5})
        self.assertEqual(result.json()['choices'][0]['message']['content'], '回答')


if __name__ == '__main__':
    unittest.main()
