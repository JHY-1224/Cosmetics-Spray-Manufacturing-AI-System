"""Real local HTTP model contracts and human business workflows; no production data."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality.app import create_app
from .conftest import post
from .test_protocols import free_port
from .test_workflow import file


@contextmanager
def reference_model(entered=None, release=None):
    app, calls = FastAPI(), []

    @app.post('/v1/chat/completions')
    def complete(body: dict):
        payload = json.loads(body['messages'][1]['content'])['data']
        assert set(payload) == {'process', 'defect', 'evidence_refs'}
        calls.append(payload)
        if entered:
            entered.set()
            assert release.wait(12), 'Test did not release the synthetic model'
        output = {'candidates': [{'dimension': 'environment', 'cause': '合成环境核查候选',
            'support_refs': [], 'counter_refs': [], 'missing_evidence': ['待现场核验'],
            'ranking_basis': '缺少现场依据，仅提出核查', 'applicability': payload['process'],
            'next_checks': ['人工核查清洁记录'], 'status': 'unverified', 'relative_score': 0}],
            'actions': [{'type': 'inspect', 'description': '核对原始清洁记录并由现场人员补证',
                         'owner_id': 'model-invented-person', 'owner_role': 'executor'}],
            'summary': '合成参考模型建议，未经人工确认'}
        return {'model': 'reference-model', 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 30, 'total_tokens': 50}}

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.01)
    try:
        yield f'http://127.0.0.1:{port}/v1', calls
    finally:
        if release:
            release.set()
        server.should_exit = True
        thread.join(timeout=3)


def configured_clients(core, settings):
    app = create_app(settings)
    clients = {}
    for role, original in core[2].items():
        client = TestClient(app)
        client.cookies.update(original.cookies)
        clients[role] = client
    return clients


def reviewed_model_workflow(core, settings, process):
    """Also used by the explicit live-provider verifier, with a synthetic database only."""
    clients = configured_clients(core, settings)
    engineer, approver, executor, quality = [clients[r] for r in ('engineer', 'approver', 'executor', 'quality')]
    line = next(line for line in core[3] if line['data']['process'] == process)
    proof = file(engineer)
    case = post(engineer, '/cases', {'title': '合成模型闭环 '+process, 'line_id': line['id'],
        'process': process, 'defect': '颗粒', 'notes': '合成保密备注，不授权外发', 'evidence_ids': [proof]})
    case = post(engineer, f"/cases/{case['id']}/diagnose", {'version': case['version']})
    assert case['diagnosis']['model_status'] == 'validated'
    body = {'case_id': case['id'], 'version': case['version']}
    post(clients['viewer'], '/assistants/A4', body, expected=403)
    draft = post(engineer, '/assistants/A4', body)['data']
    assert draft['actions'] and draft['executable'] is False
    assert all(not action['owner_id'] for action in draft['actions'])
    assert draft['model']['usage']['total_tokens'] > 0
    assert draft['model']['estimated_cost'] is None
    assert post(engineer, '/assistants/A3', body)['data']['model']['status'] == 'validated'
    source = draft['model']['run_id'] if draft['model']['draft_available'] else ''
    retest = {'method': '合成软件流程验收标准', 'target_defects': ['颗粒'], 'sample_unit': 'bottle',
              'min_samples': 2, 'max_defect_rate': 0, 'stabilization_minutes': 0, 'observation_minutes': 0}
    saved = post(engineer, f"/cases/{case['id']}/drafts", {'version': case['version'],
        'source_run_id': source, 'actions': draft['actions'], 'retest': retest})
    case, plan = saved['case'], saved['plan']
    assert plan['status'] == 'draft'
    if source:
        assert plan['content']['assistance']['run_id'] == source
        assert plan['content']['assistance']['approval_granted'] is False
    submitted = post(engineer, f"/cases/{case['id']}/plans/{plan['id']}/submit", {'version': case['version']})
    case = submitted['case']
    post(engineer, f"/cases/{case['id']}/approve", {'version': case['version'], 'plan_id': plan['id'], 'approved': True, 'reason': '作者不能自行审批'}, expected=403)
    case = post(approver, f"/cases/{case['id']}/approve", {'version': case['version'], 'plan_id': plan['id'], 'approved': True, 'reason': '独立核对人工核查范围和复测标准'})
    case = post(executor, f"/cases/{case['id']}/execute", {'version': case['version'], 'plan_id': plan['id'],
        'executed_at': datetime.now(UTC).isoformat(), 'object_ids': ['SYNTHETIC-LOAD'], 'actual_actions': '合成流程登记人工核查结果',
        'deviations': '合成验收，无生产动作', 'evidence_ids': [proof], 'success': True})
    stamp = datetime.now(UTC).isoformat()
    observation = {'plan_id': plan['id'], 'started_at': stamp, 'finished_at': stamp, 'sample_count': 1,
        'defective_count': 0, 'sample_unit': 'bottle', 'evidence_ids': [proof], 'conditions_met': True,
        'other_constraints_met': True, 'notes': '合成逐件复测记录'}
    case = post(quality, f"/cases/{case['id']}/retest", {**observation, 'version': case['version']})
    assert case['status'] == 'inconclusive'
    case = post(quality, f"/cases/{case['id']}/retest", {**observation, 'sample_count': 2, 'version': case['version']})
    case = post(quality, f"/cases/{case['id']}/close", {'version': case['version'], 'root_cause_confirmation': 'unverified',
        'quality_disposition': 'await_release', 'reason': '合成复测达标，仍需独立放行'})
    assert case['status'] == 'closed' and not case['outcome']['released']
    detail = engineer.get('/api/cases/'+case['id']).json()
    assert len(detail['receipts']) == 1 and len(detail['retests']) == 2 and len(detail['runs']) == 1
    return {'process': process, 'model': draft['model'], 'model_actions_adopted': bool(source),
            'final_state': case['status'], 'released': False, 'retests': 2,
            'data_nature': 'synthetic_only', 'live_provider': bool(settings.model_external)}


@pytest.mark.parametrize('process', ['plastic_spray', 'glass_vacuum'])
def test_model_draft_to_independent_human_closure(core, process):
    with reference_model() as (url, calls):
        settings = core[0].model_copy(update={'model_url': url, 'model_name': 'reference-model', 'model_approved': True})
        result = reviewed_model_workflow(core, settings, process)
        assert result['model_actions_adopted'] is True
        assert len(calls) == 1  # Reading/adopting an existing draft never calls a model again.


@pytest.mark.parametrize('change', ['unrelated_write', 'case_changed', 'authorization_revoked'])
def test_slow_model_does_not_block_writes_and_rechecks_before_commit(core, change):
    entered, release = threading.Event(), threading.Event()
    with reference_model(entered, release) as (url, calls):
        settings = core[0].model_copy(update={'model_url': url, 'model_name': 'reference-model', 'model_approved': True})
        clients = configured_clients(core, settings)
        engineer, admin = clients['engineer'], clients['admin']
        line = core[3][0]
        body = {'title': '并发合成案件', 'line_id': line['id'], 'process': line['data']['process'], 'defect': '颗粒'}
        case = post(engineer, '/cases', body)
        engineer.headers['X-CSRF-Token'] = engineer.cookies['quality_csrf']
        with ThreadPoolExecutor(max_workers=3) as pool:
            pending = pool.submit(engineer.post, f"/api/cases/{case['id']}/diagnose", json={'version': case['version']}, headers={'Idempotency-Key': 'slow-model-request'})
            try:
                assert entered.wait(5)
                other = pool.submit(post, admin, '/cases', {**body, 'title': '模型等待期间仍可录入'})
                assert other.result(timeout=3)['title'] == '模型等待期间仍可录入'
                concurrent = post(admin, f"/cases/{case['id']}/diagnose", {'version': case['version']}, expected=409)
                assert concurrent['detail']['code'] == 'analysis_in_progress'
                if change == 'case_changed':
                    post(admin, f"/cases/{case['id']}/evidence", {'version': case['version'], 'inspection_ids': [], 'evidence_ids': [], 'purpose': 'critical', 'reason': '模型处理中追加合成核对依据'})
                if change == 'authorization_revoked':
                    user = engineer.get('/api/auth/me').json()
                    post(admin, '/users/'+user['id']+'/revoke')
            finally:
                release.set()
            response = pending.result(timeout=8)
        assert len(calls) == 1
        if change == 'unrelated_write':
            assert response.status_code == 200, response.text
            assert response.json()['diagnosis']['model_status'] == 'validated'
        else:
            assert response.status_code in {401, 403, 409}, response.text
            detail = admin.get('/api/cases/'+case['id']).json()
            assert detail['diagnosis'].get('run_id') is None and not detail['runs']
            records = list((settings.data_dir/'model-journal').glob('*/*.json'))
            assert len(records) == 1 and json.loads(records[0].read_text())['status'] == 'completed'


def test_draft_rejects_other_case_and_stale_evidence_without_new_model_call(core):
    with reference_model() as (url, calls):
        settings = core[0].model_copy(update={'model_url': url, 'model_name': 'reference-model', 'model_approved': True})
        engineer = configured_clients(core, settings)['engineer']
        line = core[3][0]
        body = {'title': '草稿来源核对', 'line_id': line['id'], 'process': line['data']['process'], 'defect': '颗粒'}
        case = post(engineer, '/cases', body)
        case = post(engineer, f"/cases/{case['id']}/diagnose", {'version': case['version']})
        draft = post(engineer, '/assistants/A4', {'case_id': case['id'], 'version': case['version']})['data']
        plan = {'source_run_id': draft['model']['run_id'], 'actions': draft['actions'],
                'retest': {'method': '软件合成检验', 'target_defects': ['颗粒'], 'sample_unit': 'bottle',
                           'min_samples': 2, 'max_defect_rate': 0, 'stabilization_minutes': 0, 'observation_minutes': 0}}
        other = post(engineer, '/cases', {**body, 'title': '另一案件'})
        rejected = post(engineer, f"/cases/{other['id']}/drafts", {**plan, 'version': other['version']}, expected=409)
        assert rejected['detail']['code'] == 'model_draft_stale'
        case = post(engineer, f"/cases/{case['id']}/evidence", {'version': case['version'], 'inspection_ids': [],
                    'evidence_ids': [], 'purpose': 'critical', 'reason': '新增合成现场核验依据'})
        updated = post(engineer, '/assistants/A4', {'case_id': case['id'], 'version': case['version']})['data']
        assert updated['model']['status'] == 'stale' and not updated['model']['draft_available']
        rejected = post(engineer, f"/cases/{case['id']}/drafts", {**plan, 'version': case['version']}, expected=409)
        assert rejected['detail']['code'] == 'model_draft_stale'
        assert len(calls) == 1
        assert not engineer.get('/api/cases/'+case['id']).json()['plans']
