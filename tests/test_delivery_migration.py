"""Upgrade an isolated 0009-shaped fixture without replacing business records."""

from sqlalchemy import text
from quality.cli import migrate
from quality.db import Database
from quality.config import Settings
from pydantic import ValidationError
import pytest
from .conftest import post


def test_upgrade_0009_preserves_case_and_removes_cached_one_time_secret(core):
    settings, _, clients, lines = core
    client = clients['engineer']
    case = post(client, '/cases', {'title': '升级前保留的案件', 'line_id': lines[0]['id'],
                                 'process': lines[0]['data']['process'], 'defect': '颗粒'})
    source = post(clients['admin'], '/adapters', {'name': '升级前视觉源', 'protocol': 'visual',
                  'config': {'line_id': lines[0]['id']}, 'provenance': 'reference', 'enabled': True})
    database = Database(settings)
    # Only this pytest temporary database is changed into the historical schema.
    with database.engine.begin() as db:
        db.execute(text('DROP TRIGGER quality_audit_no_update'))
        db.execute(text('DROP TRIGGER quality_audit_no_delete'))
        db.execute(text('DROP TABLE raw_records'))
        db.execute(text('ALTER TABLE adapters DROP COLUMN source_registry'))
        db.execute(text('ALTER TABLE cases DROP COLUMN review_flags'))
        db.execute(text('ALTER TABLE commands DROP COLUMN authorization_hash'))
        db.execute(text("UPDATE alembic_version SET version_num='0009'"))
        db.execute(text("UPDATE commands SET response=:payload WHERE json_extract(response,'$.id')=:source"),
                   {'payload': '{"id":"legacy","token":"synthetic-legacy-one-time-secret"}', 'source': source['id']})
    migrate(settings)
    restored = client.get('/api/cases/'+case['id']).json()
    assert {key: restored[key] for key in case} == case
    assert clients['admin'].get('/api/health').json()['status'] == 'ready'
    sources = clients['admin'].get('/api/adapters').json()
    assert next(r for r in sources if r['id'] == source['id'])['source_registry'] == {}
    with database.engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM commands WHERE response LIKE '%synthetic-legacy-one-time-secret%' ")).scalar() == 0
        assert db.execute(text('SELECT count(*) FROM raw_records')).scalar() == 0


def test_liveness_and_readiness_are_separate_on_schema_mismatch(core):
    settings, _, clients, _=core
    database=Database(settings)
    with database.engine.begin() as db:
        db.execute(text("UPDATE alembic_version SET version_num='0009'"))
    assert clients['admin'].get('/api/live').status_code==200
    assert clients['admin'].get('/api/ready').status_code==503
    assert clients['admin'].get('/api/health').status_code==503
    migrate(settings)
    assert clients['admin'].get('/api/ready').status_code==200


def test_configuration_errors_do_not_print_secret_inputs():
    marker='Synthetic-Secret-Do-Not-Log-9357'
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None,mode='production',model_api_key=marker)
    assert marker not in str(error.value)
