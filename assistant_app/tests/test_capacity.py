"""Real ledger contention and async limits with synthetic model responses."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from assistant_app.backend.app import RequestGuard, create_app
from assistant_app.backend.orchestration import Jobs
from assistant_app.backend.schemas import MessageInput
from assistant_app.backend.sessions import Store
from assistant_app.backend.settings import Settings
from assistant_app.tests.test_assistant import FakeModels


def ledger(tmp_path, **extra):
    settings = Settings(data_dir=tmp_path, text_mode='base_preview', allow_base_preview=True, **extra)
    store = Store(settings)
    store.execute('INSERT INTO visitors(id,expires) VALUES (?,?)', ('owner', time.time()+300))
    return store


def test_32_concurrent_submissions_respect_global_queue_and_budget(tmp_path):
    store = ledger(tmp_path, queue_limit=8)
    conversations = [store.conversation('owner') for _ in range(32)]
    def enter(index):
        try:
            job, fresh = store.submit('owner', conversations[index], MessageInput(
                text='合成负载，不发送云端', request_key=f'concurrent-request-{index:02d}'))
            return ('accepted', job['id'], fresh)
        except HTTPException as exc:
            return ('rejected', exc.status_code, exc.detail['code'])
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(enter, range(32)))
    accepted = [r for r in results if r[0] == 'accepted']
    rejected = [r for r in results if r[0] == 'rejected']
    assert len(accepted) == 8 and len({r[1] for r in accepted}) == 8
    assert len(rejected) == 24 and all(r[1:] == (429, 'queue_full') for r in rejected)
    assert store.one('SELECT reserved FROM model_budget')['reserved'] == 8
    assert store.one('SELECT used FROM visitors')['used'] == 8


def test_concurrent_duplicate_submission_charges_once(tmp_path):
    store = ledger(tmp_path)
    conv = store.conversation('owner')
    data = MessageInput(text='合成幂等重连', request_key='same-request-key')
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: store.submit('owner', conv, data), range(24)))
    assert len({row['id'] for row, _ in results}) == 1
    assert sum(fresh for _, fresh in results) == 1
    assert store.one('SELECT reserved FROM model_budget')['reserved'] == 1


def test_execution_concurrency_and_cancellation_release_slots(tmp_path):
    async def run():
        store = ledger(tmp_path, concurrency=2)
        class CountingModels(FakeModels):
            def __init__(self):
                super().__init__()
                self.active = self.peak = 0
            async def call(self, kind, messages, json_mode=False):
                self.active += 1
                self.peak = max(self.peak, self.active)
                try:
                    await asyncio.sleep(.04)
                    return await super().call(kind, messages, json_mode)
                finally:
                    self.active -= 1
        models = CountingModels()
        jobs = Jobs(store, models)
        records = [store.submit('owner', store.conversation('owner'), MessageInput(
            text='合成并发', request_key=f'async-request-{i}'))[0] for i in range(8)]
        for row in records:
            jobs.start(row)
        await asyncio.sleep(.01)
        await jobs.cancel(records[0]['id'])
        await asyncio.gather(*list(jobs.tasks.values()))
        states = store.all('SELECT phase FROM jobs')
        assert models.peak == 2 and models.active == 0
        assert sum(r['phase'] == 'done' for r in states) == 7
        assert sum(r['phase'] == 'cancelled' for r in states) == 1
        assert len(models.calls) == 7
        assert store.one('SELECT reserved FROM model_budget')['reserved'] == 8
    asyncio.run(run())


def test_slow_bodies_are_bounded_and_capacity_recovers(tmp_path):
    async def run():
        settings = Settings(data_dir=tmp_path, body_concurrency=2)
        entered, release = asyncio.Queue(), asyncio.Event()
        async def app(scope, receive, send):
            await send({'type':'http.response.start', 'status':200, 'headers':[]})
            await send({'type':'http.response.body', 'body':b'ok'})
        guard = RequestGuard(app, settings)
        def scope():
            return {'type':'http', 'method':'POST', 'path':'/api/assistant/uploads',
                    'headers':[(b'host',b'127.0.0.1:5001')]}
        async def receive():
            await entered.put(True)
            await release.wait()
            return {'type':'http.request', 'body':b'', 'more_body':False}
        sent = []
        async def send(msg):
            sent.append(msg)
        first = [asyncio.create_task(guard(scope(), receive, send)) for _ in range(2)]
        await entered.get(); await entered.get()
        await guard(scope(), receive, send)
        rejected = next(m for m in sent if m['type']=='http.response.start')
        assert rejected['status'] == 429
        assert dict(rejected['headers'])[b'x-request-id']
        release.set()
        await asyncio.gather(*first)
        await guard(scope(), receive, send)
        assert [m['status'] for m in sent if m['type']=='http.response.start'] == [429,200,200,200]
    asyncio.run(run())


def test_request_logs_exclude_query_and_payload(tmp_path, caplog):
    settings = Settings(data_dir=tmp_path, public_origin='http://testserver')
    with TestClient(create_app(settings, FakeModels())) as client:
        with caplog.at_level('INFO', logger='coating_assistant'):
            response = client.get('/health/live?secret=private-query-token', headers={'X-Request-ID':'audit-test-123'})
        assert response.status_code == 200
        assert response.headers['x-request-id'] == 'audit-test-123'
        assert 'audit-test-123' in caplog.text and 'status=200' in caplog.text
        assert 'private-query-token' not in caplog.text
        invalid = client.get('/health/live', headers={'X-Request-ID':'bad<id>value'})
        assert invalid.headers['x-request-id'] != 'bad<id>value'
