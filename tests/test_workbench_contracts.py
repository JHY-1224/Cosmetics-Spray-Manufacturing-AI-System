import csv
import io
from datetime import UTC, datetime

from .conftest import post
from .test_workflow import case, plan, file


def test_draft_partial_execution_supplement_release_and_reopen(core):
    _, _, clients, lines = core
    engineer, approver, executor, quality = [clients[r] for r in ('engineer','approver','executor','quality')]
    c = case(engineer, lines[0])
    template = plan(engineer, c, samples=1)
    c = template['case']
    draft = post(engineer, f"/cases/{c['id']}/drafts", {'version': c['version'], **template['plan']['content']})
    c, p = draft['case'], draft['plan']
    assert p['status'] == 'draft' and c['status'] == 'awaiting_approval'
    post(approver, f"/cases/{c['id']}/approve", {'version': c['version'], 'plan_id': p['id'],
         'approved': True, 'reason': '草稿不能直接作为审批对象'}, expected=409)
    submitted = post(engineer, f"/cases/{c['id']}/plans/{p['id']}/submit", {'version': c['version']})
    c = post(approver, f"/cases/{c['id']}/approve", {'version': submitted['case']['version'], 'plan_id': p['id'],
         'approved': True, 'reason': '核对批准真实人工核查任务'})
    evidence = file(engineer)
    c = post(engineer, f"/cases/{c['id']}/evidence", {'version': c['version'], 'evidence_ids': [evidence],
        'purpose': 'supplemental', 'reason': '补充归档说明，不作为本次批准依据'})
    assert c['status'] == 'approved'
    c = post(executor, f"/cases/{c['id']}/execute", {
        'version': c['version'], 'plan_id': p['id'], 'executed_at': datetime.now(UTC).isoformat(),
        'object_ids': ['SYNTHETIC-LOAD'], 'actual_actions': '只完成部分核查，等待重新制定方案',
        'deviations': '测试部分执行分支', 'evidence_ids': [evidence], 'success': False,
        'completion': 'partial', 'completed_action_indexes': [0],
    })
    assert c['status'] == 'partially_executed'
    revised = plan(engineer, c, samples=1)
    c, p = revised['case'], revised['plan']
    c = post(approver, f"/cases/{c['id']}/approve", {'version': c['version'], 'plan_id': p['id'],
         'approved': True, 'reason': '独立批准新的完整人工方案'})
    c = post(executor, f"/cases/{c['id']}/execute", {
        'version': c['version'], 'plan_id': p['id'], 'executed_at': datetime.now(UTC).isoformat(),
        'object_ids': ['SYNTHETIC-LOAD'], 'actual_actions': '已人工完成全部核查并保留证据',
        'deviations': '', 'evidence_ids': [evidence], 'success': True,
    })
    stamp = datetime.now(UTC).isoformat()
    c = post(quality, f"/cases/{c['id']}/retest", {
        'version': c['version'], 'plan_id': p['id'], 'started_at': stamp, 'finished_at': stamp,
        'sample_count': 1, 'defective_count': 0, 'sample_unit': 'bottle', 'evidence_ids': [evidence],
        'conditions_met': True, 'other_constraints_met': True, 'notes': '合成样本仅验证状态及权限',
    })
    c = post(quality, f"/cases/{c['id']}/close", {'version': c['version'], 'root_cause_confirmation': 'unverified',
        'quality_disposition': 'await_release', 'reason': '按批准复测充分性结案，尚未正式放行'})
    post(quality, f"/cases/{c['id']}/release", {'version': c['version']}, expected=422)
    release = {'version': c['version'], 'reason': '质量负责人独立核对后记录放行决定', 'evidence_ids': [evidence]}
    post(engineer, f"/cases/{c['id']}/release", release, expected=403)
    c = post(quality, f"/cases/{c['id']}/release", release, key='quality-release-unique')
    assert c['outcome']['released'] and c['outcome']['release_reason'] == release['reason']
    assert quality.get('/api/commands/quality-release-unique').json()['status'] == 'committed'
    assert engineer.get('/api/commands/quality-release-unique').json()['status'] == 'unknown'
    assert post(quality, f"/cases/{c['id']}/release", release, key='quality-release-unique')['version'] == c['version']
    c = post(quality, f"/cases/{c['id']}/reopen", {'version': c['version'], 'reason': '出现新的关键证据，需要受控重新调查',
             'evidence_ids': [evidence]})
    assert c['status'] == 'needs_evidence' and c['outcome']['released'] is False
    assert c['context']['closed_history'][-1]['outcome']['released'] is True
    detail = engineer.get('/api/cases/'+c['id']).json()
    assert len(detail['receipts']) == 2
    assert detail['receipts'][0]['content']['completion'] == 'partial'


def test_filtered_server_pages_export_and_six_assistant_contracts(core):
    _, _, clients, lines = core
    engineer = clients['engineer']
    c = case(engineer, lines[0])
    other = case(engineer, lines[1])
    path = '/api/cases?line_id='+lines[0]['id']
    page = engineer.get(path+'&page=1&page_size=1').json()
    assert page['total'] == 1 and page['items'][0]['id'] == c['id']
    assert engineer.get(path+'&page=2&page_size=1').json()['items'] == []
    exported = engineer.get('/api/export/cases?line_id='+lines[0]['id'])
    rows = list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert len(rows) == 1 and rows[0]['id'] == c['id'] and rows[0]['id'] != other['id']
    assert engineer.get('/api/cases?site_id=ungranted-factory').status_code == 403
    assert engineer.get('/api/cases?start=2026-01-01T00:00:00').status_code == 422
    for role in ('A1','A2','A3','A4','A5','A6'):
        result = post(engineer, '/assistants/'+role, {'case_id': c['id'], 'version': c['version']})
        assert result['engine'] == 'deterministic_tools' and result['role'] == role
        assert result['scope']['line_id'] == c['line_id']
        if role == 'A3':
            assert len(result['data']['dimensions']) == 5
        if role == 'A4':
            assert result['data']['executable'] is False
            assert 'min_samples' in result['data']['required_retest_fields']
    post(engineer, '/assistants/shell', {'query': 'execute anything'}, expected=404)
    post(engineer, '/assistants/A1', {'query': 'text', 'command': 'arbitrary'}, expected=422)
    post(engineer, '/assistants/A3', {'case_id': c['id'], 'version': c['version']-1}, expected=409)


def test_command_cache_rechecks_changed_authorization_without_repeating_business(core):
    import json
    from fastapi.testclient import TestClient
    from quality import models as m
    from quality.db import Database
    settings, app, clients, lines=core
    engineer=clients['engineer']
    body={'title':'缓存授权回归夹具','line_id':lines[0]['id'],'process':lines[0]['data']['process'],'defect':'颗粒'}
    original=post(engineer,'/cases',body,key='scope-cache-invariant')
    user_id=engineer.get('/api/auth/me').json()['id']
    with Database(settings).session() as db:
        user=db.get(m.User,user_id)
        user.allowed_lines=[lines[1]['id']]
        user.auth_revision+=1
    password=json.loads((settings.data_dir/'demo-credentials.json').read_text())['engineer']
    narrowed=TestClient(app)
    post(narrowed,'/auth/login',{'username':'engineer','password':password})
    assert narrowed.get('/api/cases/'+original['id']).status_code==404
    restricted=narrowed.get('/api/commands/scope-cache-invariant').json()
    assert restricted['status']=='committed_restricted' and 'response' not in restricted
    assert original['id'] not in json.dumps(restricted) and original['title'] not in json.dumps(restricted)
    post(narrowed,'/cases',body,key='scope-cache-invariant',expected=403)
    with Database(settings).session() as db:
        user=db.get(m.User,user_id)
        user.allowed_lines=[]
        user.auth_revision+=1
    restored=TestClient(app)
    post(restored,'/auth/login',{'username':'engineer','password':password})
    result=restored.get('/api/commands/scope-cache-invariant').json()
    assert result['status']=='committed' and result['response']['id']==original['id']
    assert post(restored,'/cases',body,key='scope-cache-invariant')['id']==original['id']
    assert len(restored.get('/api/cases').json())==1
    with Database(settings).session() as db:
        from sqlalchemy import select
        cached=db.scalar(select(m.Command).where(m.Command.key=='scope-cache-invariant'))
        cached.authorization_hash=''
    legacy=restored.get('/api/commands/scope-cache-invariant').json()
    assert legacy['status']=='committed_restricted' and 'response' not in legacy
    post(restored,'/cases',body,key='scope-cache-invariant',expected=403)
    assert len(restored.get('/api/cases').json())==1
