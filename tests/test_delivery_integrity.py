"""Customer delivery invariants using identified, synthetic observations only."""

from datetime import UTC, datetime, timedelta

from .conftest import post


def published_route(clients, line):
    engineer, approver = clients['engineer'], clients['approver']
    reference = next(r for r in engineer.get('/api/routes').json() if r['process'] == line['data']['process'])
    route = post(engineer, '/routes', {
        'name': '交付夹具已审核路线', 'process': reference['process'],
        'steps': reference['steps'], 'disposition': reference['disposition'],
        'previous_id': reference['id'],
    })
    post(engineer, f"/routes/{route['id']}/review", {'action': 'submit', 'reason': '隔离验证提交'})
    route = post(approver, f"/routes/{route['id']}/review", {'action': 'publish', 'reason': '隔离验证独立审核'})
    post(engineer, f"/lines/{line['id']}/bind/{route['id']}")
    return route


def test_first_pass_100_identified_bottles_reinspection_views_and_retransmission(core):
    settings, _, clients, lines = core
    line = lines[0]
    route = published_route(clients, line)
    terminal = route['steps'][-1]['id']
    inspector = clients['inspector']
    stamp = datetime.now(UTC)
    originals = []
    for i in range(100):
        object_id = f'DELIVERY-BOTTLE-{i:03}'
        passage = post(inspector, '/passages', {
            'line_id': line['id'], 'object_id': object_id, 'granularity': 'bottle',
            'route_id': route['id'], 'step_id': terminal, 'sequence': 1,
            'entered_at': (stamp-timedelta(minutes=2)).isoformat(),
            'exited_at': (stamp-timedelta(minutes=1)).isoformat(), 'data': {'cycle': 1},
        })
        payload = {
            'record_id': f'initial-{i}', 'line_id': line['id'], 'object_id': object_id,
            'granularity': 'bottle', 'association': 'manual_confirmed',
            'occurred_at': stamp.isoformat(), 'status': 'NG' if i >= 96 else 'OK',
            'defects': [{'category': '颗粒', 'count': 1}] if i >= 96 else [],
            'model_version': 'manual-test', 'config_version': 'test-v1',
            'inspected_count': 1, 'defective_count': int(i >= 96),
            'process': line['data']['process'], 'passage_id': passage['id'],
            'inspection_round': 1, 'inspection_kind': 'initial',
            'inspection_event_id': f'event-{i}', 'view_id': 'front',
        }
        post(inspector, '/inspections', payload)
        originals.append(payload)
    for payload in originals[96:]:
        post(inspector, '/inspections', {
            **payload, 'record_id': payload['record_id']+'-retest',
            'inspection_event_id': payload['inspection_event_id']+'-retest',
            'inspection_round': 2, 'inspection_kind': 'reinspection',
            'status': 'OK', 'defects': [], 'defective_count': 0,
        })
        post(inspector, '/inspections', {**payload, 'record_id': payload['record_id']+'-back', 'view_id': 'back'})
        post(inspector, '/inspections', payload)
    result = clients['viewer'].get('/api/statistics?granularity=bottle').json()
    assert result['first_pass_inspected'] == 100
    assert result['first_pass_defective'] == 4
    assert result['first_pass_yield'] == .96
    assert result['reinspection_objects'] == 4
    assert result['reinspection_ok_objects'] == 4
    assert result['inspection_observations'] == 108
    assert result['inspected_count'] == 104
    assert result['final_yield'] is None  # Customer terminal disposition policy is not invented.
    from quality import models as m
    from quality.db import Database
    from sqlalchemy import select
    # Isolated fixture representing the old persisted one-based stage semantics.
    with Database(settings).session() as db:
        for row in db.scalars(select(m.Inspection)):
            value={**row.result,'verified_cycle':1}
            value.pop('cycle_semantics',None)
            row.result=value
    upgraded=clients['viewer'].get('/api/statistics?granularity=bottle').json()
    assert upgraded['first_pass_inspected']==100 and upgraded['first_pass_yield']==.96
    assert upgraded['legacy_stage_requires_review']==0


def test_route_binding_rejects_different_process(core):
    _, _, clients, lines = core
    plastic = next(r for r in lines if r['data']['process'] == 'plastic_spray')
    glass = next(r for r in lines if r['data']['process'] == 'glass_vacuum')
    route = published_route(clients, glass)
    post(clients['engineer'], f"/lines/{plastic['id']}/bind/{route['id']}", expected=422)


def test_ng_only_and_multiview_version_conflict_do_not_claim_population_yield(core):
    _, _, clients, lines = core
    inspector=clients['inspector']
    line=lines[0]
    body={'record_id':'ng-only','line_id':line['id'],'object_id':'NG-ONLY-1','granularity':'bottle',
          'association':'manual_confirmed','occurred_at':datetime.now(UTC).isoformat(),'status':'NG',
          'defects':[{'category':'颗粒','count':1}],'model_version':'test-v1','config_version':'v1',
          'inspected_count':1,'defective_count':1,'inspection_event_id':'event-one',
          'inspection_kind':'initial','collection_scope':'ng_only','process':line['data']['process']}
    post(inspector,'/inspections',body)
    result=clients['viewer'].get('/api/statistics').json()
    assert result['first_pass_yield'] is None and result['population_yield'] is None
    assert not result['coverage']['denominator_confirmed']
    post(inspector,'/inspections',{**body,'record_id':'other-view','view_id':'back','model_version':'incompatible-v2'})
    result=clients['viewer'].get('/api/statistics').json()
    assert result['excluded_conflicting_observation_groups']==1 and result['inspected_count']==0
