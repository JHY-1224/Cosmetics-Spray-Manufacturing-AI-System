import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from quality.db import Database
from .conftest import post


def test_durable_raw_quarantine_reviewed_mapping_reprocess_and_scope(core, monkeypatch):
    settings, _, clients, lines = core
    line = lines[0]
    admin = clients['admin']
    config = {'endpoint': 'http://127.0.0.1:19999/records', 'line_id': line['id']}
    source = post(admin, '/adapters', {'name': '隔离协议夹具', 'protocol': 'http', 'enabled': True,
                                    'provenance': 'reference', 'config': config})
    registry = {'line_id': line['id'], 'source_system': '本机HTTP参考服务', 'owner': '测试负责人',
                'authentication': 'environment_reference', 'read_scope': '仅遥测读取', 'time_source': '源UTC时间',
                'points': [{'name': 'pressure', 'source_field': 'value', 'meaning': '参考压力实测',
                            'value_type': 'measured', 'unit': 'Pa'}]}
    post(admin, f"/adapters/{source['id']}/registry", registry)
    raw = {'record_id': 'boot-record-1', 'equipment_id': 'reference-equipment', 'metric': 'pressure',
           'value': 'invalid', 'alternative': 0, 'unit': 'kPa', 'occurred_at': datetime.now(UTC).isoformat(),
           'value_type': 'measured', 'quality': 'Good', 'password': 'sample-secret-must-be-redacted'}
    monkeypatch.setattr('quality.ingestion.read_adapter', lambda *a: {'records': [raw], 'cursor': 'next'})
    health = post(admin, f"/adapters/{source['id']}/poll")
    assert health['status'] == 'data_quality_error'
    records = admin.get('/api/raw-records').json()['items']
    assert len(records) == 1 and records[0]['status'] == 'quarantined'
    row = records[0]
    assert (settings.data_dir/'ingestion-raw'/row['object_key']).is_file()
    sample = admin.get(f"/api/raw-records/{row['id']}/sample").json()
    assert 'sample-secret' not in json.dumps(sample)
    adapter = next(a for a in admin.get('/api/adapters').json() if a['id'] == source['id'])
    fields = {name: name for name in ('record_id','equipment_id','metric','unit','occurred_at','quality','value_type')}
    mapping = post(admin, f"/adapters/{source['id']}/mapping", {
        'base_version': 'legacy-v1', 'config': {**adapter['config'], 'fields': {**fields, 'value': 'alternative'}},
        'reason': '使用来源真正的数值字段，保留零值',
    })
    review = {'version': mapping['version'], 'action': 'publish', 'reason': '核对原始点表及单位后批准'}
    post(admin, f"/adapters/{source['id']}/mapping/review", review, expected=403)
    post(clients['approver'], f"/adapters/{source['id']}/mapping/review", review)
    processed = post(admin, f"/raw-records/{row['id']}/reprocess", {'reason': '按已批准的新映射重处理'})
    assert processed['status'] == 'processed'
    points = admin.get('/api/measurements?line_id='+line['id']).json()['items']
    assert len(points) == 1 and points[0]['value'] == 0 and points[0]['unit'] == 'Pa'
    assert points[0]['data']['original_unit'] == 'kPa'
    assert points[0]['data']['mapping_version'] == 'mapping-v1'
    post(admin, f"/raw-records/{row['id']}/reprocess", {'reason': '再次核对不应重复产生事实'})
    assert admin.get('/api/measurements').json()['total'] == 1
    monkeypatch.setattr('quality.ingestion.read_adapter', lambda *a: {'records': [{**raw, 'alternative': 12}]})
    post(admin, f"/adapters/{source['id']}/poll")
    conflicts = admin.get('/api/raw-records?status=conflict').json()['items']
    assert len(conflicts) == 1
    assert admin.get('/api/measurements').json()['total'] == 1
    post(admin, f"/raw-records/{conflicts[0]['id']}/reprocess", {'reason': '不能强行覆盖冲突载荷'}, expected=409)
    assert clients['viewer'].get('/api/raw-records').status_code == 403


def test_read_only_config_and_append_only_audit(core):
    settings, _, clients, lines = core
    post(clients['engineer'], '/masters', {'kind': 'product', 'code': 'AUDIT-APPEND', 'name': '审计不可改测试产品'})
    for protocol, config in [('http', {'endpoint': 'http://127.0.0.1:9', 'mode': 'write'}),
                             ('mqtt', {'endpoint': 'mqtt://127.0.0.1:9', 'topic': 'equipment/commands/set'})]:
        post(clients['admin'], '/adapters', {'name': '不得写设备', 'protocol': protocol,
             'enabled': False, 'config': config, 'provenance': 'reference'}, expected=422)
    with pytest.raises(DBAPIError):
        with Database(settings).session() as db:
            db.execute(text("UPDATE audit SET action='tampered'"))
    with pytest.raises(DBAPIError):
        with Database(settings).session() as db:
            db.execute(text('DELETE FROM audit'))


def test_source_sessions_and_unknown_quality_remain_distinct(core, monkeypatch):
    _, _, clients, lines = core
    source = post(clients['admin'], '/adapters', {'name': '设备重启夹具', 'protocol': 'http', 'enabled': True,
                  'provenance': 'reference', 'config': {'endpoint': 'http://127.0.0.1:9', 'line_id': lines[0]['id']}})
    record = {'record_id': '1', 'equipment_id': 'sensor', 'metric': 'temperature', 'value': 0, 'unit': 'degC',
              'occurred_at': datetime.now(UTC).isoformat(), 'value_type': 'measured'}
    monkeypatch.setattr('quality.ingestion.read_adapter', lambda *a: {'records': [
        {**record, 'source_session_id': 'boot-a'}, {**record, 'source_session_id': 'boot-b'}]})
    post(clients['admin'], f"/adapters/{source['id']}/poll")
    records = clients['admin'].get('/api/raw-records').json()['items']
    assert len(records) == 2 and all(r['status'] == 'quarantined' for r in records)
    points = clients['admin'].get('/api/measurements').json()['items']
    assert len(points) == 2 and all(r['value'] == 0 and r['quality'] == 'Uncertain' for r in points)


def test_fsynced_raw_survives_database_rollback_and_worker_restart(core):
    from quality import models as m
    from quality.adapters import AdapterConfig
    from quality.ingestion import stage_raw, recover_raw
    from quality.security import Actor
    from quality.service import Service
    settings, _, clients, lines = core
    source = post(clients['admin'], '/adapters', {'name': '断电窗口恢复夹具', 'protocol': 'http',
                  'enabled': True, 'provenance': 'reference',
                  'config': {'endpoint': 'http://127.0.0.1:9', 'line_id': lines[0]['id']}})
    raw = {'record_id': 'received-before-crash', 'equipment_id': 'sensor', 'metric': 'pressure',
           'value': 0, 'unit': 'Pa', 'quality': 'Good', 'value_type': 'measured',
           'occurred_at': datetime.now(UTC).isoformat(), 'source_session_id': 'boot-1'}
    database = Database(settings)
    with pytest.raises(RuntimeError):
        with database.session() as db:
            svc = Service(db, settings, Actor('collector', 'admin', settings.sites))
            adapter = svc.get(m.Adapter, source['id'])
            row = stage_raw(svc, adapter, raw, AdapterConfig.model_validate(adapter.config))
            original_received = row.received_at
            path = settings.data_dir/'ingestion-raw'/row.object_key
            raise RuntimeError('synthetic process death before commit')
    assert path.exists() and not path.with_suffix('.done').exists()
    with database.session() as db:
        svc = Service(db, settings, Actor('collector', 'admin', settings.sites))
        assert recover_raw(svc) == 1
    assert path.with_suffix('.done').exists()
    records = clients['admin'].get('/api/raw-records').json()['items']
    assert len(records) == 1 and records[0]['status'] == 'processed'
    assert datetime.fromisoformat(records[0]['received_at']).replace(tzinfo=UTC) == original_received
    assert clients['admin'].get('/api/measurements').json()['total'] == 1
    with database.session() as db:
        assert recover_raw(Service(db, settings, Actor('collector', 'admin', settings.sites))) == 0


def test_boolean_future_and_bad_quality_are_retained_but_not_trusted(core, monkeypatch):
    from datetime import timedelta
    _, _, clients, lines = core
    source = post(clients['admin'], '/adapters', {'name':'质量与时钟故障夹具', 'protocol':'http',
                  'enabled':True, 'provenance':'reference',
                  'config':{'endpoint':'http://127.0.0.1:9','line_id':lines[0]['id']}})
    raw = {'equipment_id':'sensor','metric':'temperature','unit':'degC','value_type':'measured',
           'occurred_at':datetime.now(UTC).isoformat(),'quality':'Good','value':0}
    monkeypatch.setattr('quality.ingestion.read_adapter',lambda *a:{'records':[
        {**raw,'record_id':'boolean','value':False},
        {**raw,'record_id':'future','occurred_at':(datetime.now(UTC)+timedelta(hours=1)).isoformat()},
        {**raw,'record_id':'bad','quality':'Bad'}, {**raw,'record_id':'real-zero'}]})
    post(clients['admin'],f"/adapters/{source['id']}/poll")
    rows = clients['admin'].get('/api/raw-records').json()['items']
    assert len(rows)==4 and sum(r['status']=='quarantined' for r in rows)==3
    errors={r['record_id']:r['errors'] for r in rows}
    assert errors['future'][0]['error']=='source_clock_ahead'
    assert errors['boolean'][0]['error']=='boolean_is_not_measurement'
    values = clients['admin'].get('/api/measurements').json()['items']
    assert len(values)==2 and sum(v['quality']=='Good' and v['value']==0 for v in values)==1
