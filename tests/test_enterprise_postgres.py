import hashlib
import json
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from quality import models as m
from quality.cli import migrate, initialize
from quality.db import Database
from quality.security import Actor
from quality.service import Service
from .conftest import PASSWORD
from .test_postgres import postgres  # noqa: F401


@pytest.fixture(name="enterprise_pg")
def _enterprise_pg(request):
    return request.getfixturevalue("postgres")


def test_enterprise_tables_actual_nonowner_rls_append_only_and_pool_reset(enterprise_pg):
    settings, _ = enterprise_pg
    database = Database(settings)
    database.check_runtime_role()
    with database.session(["factory-a"], ["enterprise-line-a"]) as db:
        svc = Service(db, settings, Actor("rls-test", "admin", ["factory-a"], lines=["enterprise-line-a"]))
        row = svc.add(
            m.MaterialItem,
            code="ENTERPRISE-RLS",
            name="隔离RLS材料",
            unit="kg",
            quality_requirements={},
            scope_line_id="enterprise-line-a",
        )
        id = row.id
    with database.session(["factory-a"], ["enterprise-line-b"]) as db:
        assert db.get(m.MaterialItem, id) is None
    with database.session(["factory-b"]) as db:
        assert db.get(m.MaterialItem, id) is None
    with database.engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM material_items")).scalar() == 0
    with pytest.raises(DBAPIError):
        with database.session(["factory-a"], ["enterprise-line-b"]) as db:
            db.add(
                m.MaterialItem(
                    tenant_id=settings.tenant_id,
                    site_id="factory-a",
                    scope_line_id="enterprise-line-a",
                    code="FORGED",
                    name="越权行",
                    unit="kg",
                    quality_requirements={},
                )
            )
    # 0012 append-only triggers are present on actual PostgreSQL, not just SQLite.
    with database.session() as db:
        assert (
            db.execute(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid='business_receipts'::regclass"
                )
            ).scalar()
            >= 1
        )
    database.engine.dispose()


def test_true_historical_postgres_0011_upgrade_keeps_original_rows(enterprise_pg, tmp_path):
    settings, uri = enterprise_pg
    fixture = Path(__file__).parent / "fixtures/schema_0011.postgres.sql"
    provenance = json.loads(fixture.with_suffix(".provenance.json").read_text())
    assert provenance["business_table_count"] == 31 and provenance["business_rows"] == 0
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == provenance["fixture_sha256"]
    name = "historical_" + uuid4().hex[:12]
    with psycopg.connect(uri, autocommit=True) as db:
        db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    admin_url = make_url(settings.migration_database_url).set(database=name)
    target_uri = admin_url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(target_uri, autocommit=True) as db:
        db.execute(fixture.read_text(), prepare=False)
        db.execute("SET search_path TO public")
        # pg_dump --schema-only excludes version data; this stamp belongs to the
        # independently generated 31-table historic DDL, never current ORM tables.
        db.execute("INSERT INTO alembic_version VALUES ('0011')")
        assert db.execute("SELECT to_regclass('public.material_formulations')").fetchone()[0] is None
    target = settings.model_copy(
        update={
            "data_dir": tmp_path / "historical",
            "migration_database_url": admin_url.render_as_string(hide_password=False),
            "database_url": make_url(settings.database_url)
            .set(database=name)
            .render_as_string(hide_password=False),
        }
    )
    initialize(target, "historic-admin", PASSWORD)
    with Database(target, migration=True).session() as db:
        original = db.scalar(select(m.User).where(m.User.username == "historic-admin")).id
    migrate(target)
    with psycopg.connect(target_uri, autocommit=True) as db:
        db.execute("GRANT USAGE ON SCHEMA public TO quality_runtime")
        db.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO quality_runtime")
        db.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO quality_runtime")
    database = Database(target)
    database.check_runtime_role()
    with database.session() as db:
        assert db.scalar(select(m.User).where(m.User.username == "historic-admin")).id == original
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0012"
        assert (
            db.execute(
                text(
                    "SELECT count(*) FROM pg_class WHERE relname=ANY(:tables) AND relrowsecurity AND relforcerowsecurity"
                ),
                {"tables": [c.__tablename__ for c in m.ENTERPRISE_MODELS]},
            ).scalar()
            == 30
        )
    database.engine.dispose()


def test_concurrent_plan_reservations_cannot_exceed_allocated_demand(enterprise_pg):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from fastapi.testclient import TestClient
    from quality.app import create_app
    from quality.cli import seed_demo
    from .conftest import post
    from .test_industrial_supply_cost import order_fixture
    from .test_industrial_revalidation import supply_plan

    settings, _ = enterprise_pg
    seed_demo(settings)
    app = create_app(settings)
    clients = {}
    passwords = {'admin': PASSWORD, **json.loads((settings.data_dir / 'demo-credentials.json').read_text())}
    for role, password in passwords.items():
        client = TestClient(app)
        post(client, '/auth/login', {'username': role, 'password': password})
        clients[role] = client
    lines = clients['admin'].get('/api/masters?kind=line').json()
    clients, _, _, _, stocks, _, order, body = order_fixture((settings, app, clients, lines))
    small = post(clients['admin'], f"/industrial/orders/{order['id']}/revision", {
        **body, 'version': order['version'], 'source_version': 'small-concurrent-order',
        'lines': [{**part, 'quantity': '1'} for part in body['lines']],
    })
    plan = supply_plan(clients['admin'], small, stocks[:1])
    barrier = threading.Barrier(2)
    def reserve_once():
        client = TestClient(app)
        client.cookies.update(clients['admin'].cookies)
        client.headers['X-CSRF-Token'] = client.cookies['quality_csrf']
        barrier.wait(timeout=10)
        return client.post('/api/industrial/reservations', json={
            'inventory_id': stocks[0]['id'], 'quantity': '4', 'plan_id': plan['id'],
        }, headers={'Idempotency-Key': str(uuid4())})
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: reserve_once(), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    denied = next(r for r in results if r.status_code == 409)
    assert denied.json()['detail']['code'] == 'reservation_plan_quantity'
    holds = clients['admin'].get('/api/records/industrial-reservations').json()
    assert holds['total'] == 1
    assert holds['items'][0]['quantity'] == '4.000000000'
