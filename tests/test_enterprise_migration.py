"""Start with actual pre-upgrade 0011 DDL, not today's models stamped as an old version."""
from pathlib import Path
import hashlib
import json
import sqlite3
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from quality import models as m
from quality.app import create_app
from quality.cli import initialize, migrate, seed_demo
from quality.config import Settings
from quality.db import Database
from .conftest import PASSWORD, post


def test_actual_historical_0011_upgrade_preserves_rows_and_adds_frozen_enterprise_tables(tmp_path):
    fixture = Path(__file__).parent/'fixtures/schema_0011.sqlite.sql'
    provenance = json.loads(fixture.with_suffix('.provenance.json').read_text())
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == provenance['fixture_sha256']
    assert provenance['business_rows'] == 0
    file = tmp_path/'historical.db'
    with sqlite3.connect(file) as db:
        db.executescript(fixture.read_text())
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '0011'
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='material_formulations'").fetchone()[0] == 0
    settings = Settings(_env_file=None, mode='integration', data_dir=tmp_path, database_url='sqlite:///'+str(file))
    initialize(settings, 'upgrade-admin', PASSWORD)
    seed_demo(settings)
    client = TestClient(create_app(settings))
    post(client, '/auth/login', {'username':'upgrade-admin','password':PASSWORD})
    line = client.get('/api/masters?kind=line').json()[0]
    case = post(client, '/cases', {'title':'真实旧DDL上的保留案件', 'line_id':line['id'],
                                  'process':line['data']['process'], 'defect':'颗粒'})
    assert client.get('/api/ready').status_code == 503
    database = Database(settings)
    old_tables = set(inspect(database.engine).get_table_names())
    assert len(old_tables - {'alembic_version'}) == 31
    migrate(settings)
    assert client.get('/api/ready').status_code == 200
    assert client.get('/api/cases/'+case['id']).json()['context'] == case['context']
    tables = set(inspect(database.engine).get_table_names())
    assert tables-old_tables == {c.__tablename__ for c in m.ENTERPRISE_MODELS}
    with database.engine.connect() as db:
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0012'
