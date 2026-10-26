from pathlib import Path
import os

import fasteners
import pgserver
import psycopg
import pytest
from pgserver.postgres_server import PostgresServer
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from quality import models as m
from quality.cli import initialize, migrate
from quality.config import Settings
from quality.db import Database
from quality.security import Actor, current_time, digest
from quality.service import Service
from .conftest import PASSWORD


@pytest.fixture(scope="module")
def postgres(tmp_path_factory):
    root = tmp_path_factory.mktemp("postgres-integration")
    import tempfile

    runtime = Path(tempfile.mkdtemp(prefix="qpg-", dir="/tmp"))
    PostgresServer.runtime_path = runtime
    PostgresServer._lock = fasteners.InterProcessLock(runtime / "lock")
    server = pgserver.get_server(root / "data", cleanup_mode="stop")
    uri = server.get_uri()
    with psycopg.connect(uri, autocommit=True) as c:
        c.execute("CREATE ROLE quality_runtime LOGIN NOSUPERUSER NOBYPASSRLS")
        import json

        target = Path(os.environ.get("PYTEST_ARTIFACT_DIR", str(root))) / (root.name + "-environment.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        assert not target.exists(), "Each PostgreSQL run must retain its own environment evidence"
        target.write_text(
            json.dumps(
                {
                    "server_version": c.execute("SELECT version()").fetchone()[0],
                    "transport": "temporary Unix socket; not production network TLS authentication",
                    "vector_version": None,
                    "customer_acceptance": "not_performed",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    settings = Settings(
        _env_file=None,
        mode="integration",
        data_dir=root / "runtime",
        tenant_id="customer-a",
        site_id="factory-a",
        database_url=uri.replace("postgresql://postgres:@", "postgresql+psycopg://quality_runtime@"),
        migration_database_url=uri.replace("postgresql://", "postgresql+psycopg://"),
    )
    migrate(settings)
    initialize(settings, "admin", PASSWORD)
    with psycopg.connect(uri, autocommit=True) as c:
        c.execute("GRANT USAGE ON SCHEMA public TO quality_runtime")
        c.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO quality_runtime")
        c.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO quality_runtime")
        recorded = json.loads(target.read_text())
        recorded["vector_version"] = c.execute(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"
        ).fetchone()[0]
        target.write_text(json.dumps(recorded, ensure_ascii=False, indent=2))
    yield settings, uri
    server.cleanup()


def test_postgres_rls_factory_customer_and_pool_reset(postgres):
    settings, uri = postgres
    db = Database(settings)
    db.check_runtime_role()
    with psycopg.connect(uri, autocommit=True) as c:
        for tenant, site, code in [
            ("customer-a", "factory-a", "A"),
            ("customer-a", "factory-b", "B"),
            ("customer-b", "factory-a", "PRIVATE"),
        ]:
            c.execute(
                "INSERT INTO masters (id,tenant_id,site_id,created_at,kind,code,name,data,version,scope_line_id) VALUES (%s,%s,%s,now(),%s,%s,%s,%s,1,%s)",
                (code, tenant, site, "line", code, code, "{}", code),
            )
    with db.session(["factory-a"]) as session:
        assert [r.code for r in session.scalars(select(m.Master))] == ["A"]
    with db.session(["factory-b"]) as session:
        assert [r.code for r in session.scalars(select(m.Master))] == ["B"]
    with db.session(["factory-a"], ["other-line"]) as session:
        assert session.scalars(select(m.Master)).all() == []
    with db.session(["factory-a"], ["A"]) as session:
        assert [r.code for r in session.scalars(select(m.Master))] == ["A"]
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM masters")).scalar() == 0
    with pytest.raises(DBAPIError):
        with db.session(["factory-a"]) as session:
            session.add(
                m.Master(
                    tenant_id="customer-b",
                    site_id="factory-a",
                    kind="line",
                    code="forged",
                    name="forged",
                    data={},
                )
            )
    assert db.engine.url.username == "quality_runtime"


def test_postgres_vector_scope_filter_and_chinese_keyword(postgres):
    from datetime import timedelta
    from quality.retrieval import search

    settings, uri = postgres
    db = Database(settings)
    with db.session(["factory-a"]) as session:
        svc = Service(session, settings, Actor("expert", "admin", ["factory-a"]))
        knowledge = svc.add(
            m.Knowledge,
            category="sop",
            title="颗粒缺陷的现场核验",
            content="颗粒 尘点 应核查清洁记录",
            status="published",
            author_id="author",
            scope={"process": "plastic_spray", "factory_ids": ["factory-a"], "line_ids": ["A", "A2"]},
            source={"provenance": "manual"},
            valid_until=current_time() + timedelta(days=1),
        )
        vector = [1.0] + [0.0] * 383
        svc.add(
            m.KnowledgeVector,
            knowledge_id=knowledge.id,
            embedding=vector,
            model_version="controlled-test-vector",
            content_hash=digest(knowledge.content),
        )
        assert search(svc, "颗粒", "plastic_spray", line_id='A')[0]["entry"]["id"] == knowledge.id
        distance = session.scalar(
            select(m.KnowledgeVector.embedding.cosine_distance(vector)).where(
                m.KnowledgeVector.knowledge_id == knowledge.id
            )
        )
        assert abs(float(distance)) < 1e-6
    with db.session(["factory-b"]) as session:
        assert session.scalars(select(m.KnowledgeVector)).all() == []
    with db.session(["factory-a"], ["other-line"]) as session:
        assert session.scalars(select(m.Knowledge)).all() == []
        assert session.scalars(select(m.KnowledgeVector)).all() == []
    with db.session(["factory-a"], ["A"]) as session:
        svc = Service(session, settings, Actor("line-expert", "viewer", ["factory-a"], lines=["A"]))
        assert len(session.scalars(svc.scoped(m.Knowledge)).all()) == 1
        assert len(session.scalars(svc.scoped(m.KnowledgeVector)).all()) == 1


def test_postgres_empty_scope_raw_rls_and_immutable_audit(postgres):
    settings, uri = postgres
    database = Database(settings)
    with database.session([]) as db:
        assert not db.scalars(select(m.Master)).all()
        assert db.execute(text("SELECT current_setting('app.sites',true)")).scalar() == ''
    with database.session(['factory-a'], ['A']) as db:
        svc = Service(db, settings, Actor('collector', 'admin', ['factory-a'], lines=['A']))
        from quality.ingestion import stage_raw
        from quality.adapters import AdapterConfig
        adapter = svc.add(m.Adapter, name='隔离持久记录参考', protocol='http', enabled=False,
                          config={'endpoint':'http://127.0.0.1:9','line_id':'A'}, provenance='reference', scope_line_id='A')
        row = stage_raw(svc, adapter, {'record_id':'one'}, AdapterConfig.model_validate(adapter.config))
        raw_id = row.id
        svc.audit('raw.integration_test', row.id)
    for sites, lines in [(['factory-b'], ['*']), (['factory-a'], ['other-line']), ([], ['*'])]:
        with database.session(sites, lines) as db:
            assert db.get(m.RawRecord, raw_id) is None
    with database.session(['factory-a'], ['A']) as db:
        assert db.get(m.RawRecord, raw_id) is not None
    for statement in ["UPDATE audit SET action='tampered'", 'DELETE FROM audit']:
        with pytest.raises(DBAPIError):
            with database.session(['factory-a']) as db:
                db.execute(text(statement))


def test_postgres_business_and_langgraph_persistence(postgres):
    from fastapi.testclient import TestClient
    from quality.app import create_app
    from .conftest import post

    settings, uri = postgres
    client = TestClient(create_app(settings))
    post(client, "/auth/login", {"username": "admin", "password": PASSWORD})
    lines = client.get("/api/masters").json()
    line = next(r for r in lines if r["code"] == "A")
    case = post(
        client,
        "/cases",
        {
            "title": "PostgreSQL真实持久化异常",
            "line_id": line["id"],
            "process": "plastic_spray",
            "defect": "颗粒",
        },
    )
    case = post(client, f"/cases/{case['id']}/diagnose", {"version": case["version"]})
    assert case["rounds"] == 1
    with psycopg.connect(uri) as c:
        assert c.execute("SELECT count(*) FROM checkpoints").fetchone()[0] > 0
    other = TestClient(create_app(settings))
    other.cookies.update(client.cookies)
    assert other.get("/api/cases/" + case["id"]).json()["diagnosis"]["run_id"] == case["diagnosis"]["run_id"]


def test_postgres_concurrent_approval_commits_once(postgres):
    import concurrent.futures
    import threading
    import uuid
    from fastapi.testclient import TestClient
    from quality.app import create_app
    from .conftest import post
    from .test_workflow import plan

    settings, _ = postgres
    app=create_app(settings)
    admin=TestClient(app)
    post(admin,'/auth/login',{'username':'admin','password':PASSWORD})
    post(admin,'/users',{'username':'pg-concurrent-author','password':PASSWORD,'role':'engineer',
                         'allowed_sites':['factory-a'],'allowed_lines':[]})
    engineer=TestClient(app)
    post(engineer,'/auth/login',{'username':'pg-concurrent-author','password':PASSWORD})
    line=post(engineer,'/masters',{'kind':'line','code':'PG-CONCURRENT','name':'PG并发测试产线',
                                 'data':{'process':'plastic_spray'}})
    c=post(engineer,'/cases',{'title':'PG并发审批夹具','line_id':line['id'],'process':'plastic_spray','defect':'颗粒'})
    c=post(engineer,f"/cases/{c['id']}/diagnose",{'version':c['version']})
    saved=plan(engineer,c,samples=1)
    c,p=saved['case'],saved['plan']
    body={'version':c['version'],'plan_id':p['id'],'approved':True,'reason':'并发请求独立核对相同方案版本'}
    contenders=[TestClient(app),TestClient(app)]
    for client in contenders:
        client.cookies.update(admin.cookies)
        client.headers['X-CSRF-Token'] = admin.cookies['quality_csrf']
    barrier=threading.Barrier(2)
    def submit(client):
        barrier.wait(timeout=5)
        return client.post(f"/api/cases/{c['id']}/approve",json=body,headers={'Idempotency-Key':str(uuid.uuid4())})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        replies=list(pool.map(submit,contenders))
    assert sorted(r.status_code for r in replies)==[200,409]
    detail=admin.get('/api/cases/'+c['id']).json()
    assert detail['status']=='approved' and len(detail['approvals'])==1


def test_postgres_dump_restore_new_database_objects_and_rls(postgres, tmp_path):
    from quality.backup import backup, restore
    from quality.app import create_app
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import make_url
    from .conftest import post
    from .test_workflow import file

    settings, uri = postgres
    source = TestClient(create_app(settings))
    post(source, "/auth/login", {"username": "admin", "password": PASSWORD})
    evidence = file(source)
    archive = tmp_path / "pg-backup"
    assert backup(settings, archive)["objects"] == 1
    with psycopg.connect(uri, autocommit=True) as connection:
        connection.execute("CREATE DATABASE quality_restore_test")
    runtime = (
        make_url(settings.database_url)
        .set(database="quality_restore_test")
        .render_as_string(hide_password=False)
    )
    migration = (
        make_url(settings.migration_database_url)
        .set(database="quality_restore_test")
        .render_as_string(hide_password=False)
    )
    fresh = settings.model_copy(
        update={
            "database_url": runtime,
            "migration_database_url": migration,
            "data_dir": tmp_path / "restored-runtime",
        }
    )
    assert restore(fresh, archive)["objects"] == 1
    with psycopg.connect(
        make_url(migration).set(drivername="postgresql").render_as_string(hide_password=False),
        autocommit=True,
    ) as connection:
        connection.execute("GRANT USAGE ON SCHEMA public TO quality_runtime")
        connection.execute(
            "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO quality_runtime"
        )
        connection.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO quality_runtime")
    migrate(fresh)
    Database(fresh).check_runtime_role()
    restored = TestClient(create_app(fresh))
    post(restored, "/auth/login", {"username": "admin", "password": PASSWORD})
    assert restored.get("/api/files/" + evidence).content == b"manual measurement evidence"
    assert restored.get("/api/cases").json()


def test_postgres_0009_shaped_database_upgrade_preserves_business(postgres, tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import make_url
    from quality.app import create_app
    from .conftest import post
    settings, uri=postgres
    with psycopg.connect(uri,autocommit=True) as db:
        db.execute('CREATE DATABASE quality_legacy_upgrade')
    def database_url(value):
        return make_url(value).set(database='quality_legacy_upgrade').render_as_string(hide_password=False)
    fresh=settings.model_copy(update={'database_url':database_url(settings.database_url),
        'migration_database_url':database_url(settings.migration_database_url),'data_dir':tmp_path/'legacy-runtime'})
    migrate(fresh)
    initialize(fresh,'upgrade-admin',PASSWORD)
    admin_uri=database_url(uri)
    with psycopg.connect(admin_uri,autocommit=True) as db:
        db.execute('GRANT USAGE ON SCHEMA public TO quality_runtime')
        db.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO quality_runtime')
        db.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO quality_runtime')
    client=TestClient(create_app(fresh))
    post(client,'/auth/login',{'username':'upgrade-admin','password':PASSWORD})
    line=post(client,'/masters',{'kind':'line','code':'UPGRADE','name':'旧库升级隔离产线','data':{'process':'plastic_spray'}})
    case=post(client,'/cases',{'title':'PG旧库保留事实','line_id':line['id'],'process':'plastic_spray','defect':'颗粒'})
    # Convert only this newly created, isolated database to the historical shape.
    with psycopg.connect(admin_uri,autocommit=True) as db:
        db.execute('DROP TRIGGER quality_audit_immutable ON audit')
        db.execute('DROP TABLE raw_records')
        db.execute('ALTER TABLE adapters DROP COLUMN source_registry')
        db.execute('ALTER TABLE cases DROP COLUMN review_flags')
        db.execute('ALTER TABLE commands DROP COLUMN authorization_hash')
        db.execute("UPDATE alembic_version SET version_num='0009'")
    migrate(fresh)
    with psycopg.connect(admin_uri,autocommit=True) as db:
        db.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO quality_runtime')
    Database(fresh).check_runtime_role()
    restored=client.get('/api/cases/'+case['id']).json()
    assert {key:restored[key] for key in case}==case
    assert client.get('/api/ready').status_code==200


def test_postgres_learning_publication_lock_queue_and_scoped_json_pages(postgres):
    """Exercise new tables and JSON paging under the real non-superuser runtime role."""
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from quality.app import create_app
    from quality.learning_tasks import claim, run_once
    from .conftest import post
    from .test_expert_learning import review

    settings, _ = postgres
    app = create_app(settings)
    admin = TestClient(app)
    post(admin, "/auth/login", {"username": "admin", "password": PASSWORD})
    clients = []
    for username, role in [("learning-expert", "engineer"), ("learning-reviewer", "approver")]:
        post(
            admin,
            "/users",
            {"username": username, "password": PASSWORD, "role": role, "allowed_sites": ["factory-a"]},
        )
        client = TestClient(app)
        post(client, "/auth/login", {"username": username, "password": PASSWORD})
        clients.append(client)
    expert, reviewer = clients
    line = post(
        admin,
        "/masters",
        {
            "kind": "line",
            "code": "LEARN-PG",
            "name": "真实PG学习隔离夹具",
            "data": {"process": "plastic_spray"},
        },
    )
    condition = post(
        expert,
        "/learning/conditions",
        {
            "stable_id": "manual_air_pg",
            "name": "人工核对空气温度",
            "scope": {"line_id": line["id"], "process": "plastic_spray"},
            "dimension": "process",
            "data_type": "number",
            "unit": "degC",
            "value_type": "manual",
            "event_semantics": "人工核对仪表记录",
            "quality_required": "manual_verified",
            "meaning": "仅为PG事务验证，不是生产工艺参数",
        },
    )
    pending = review(expert, condition, "submit")

    def publish(_):
        return reviewer.post(
            "/api/learning/" + pending["id"] + "/review",
            json={"version": pending["version"], "action": "publish", "reason": "真实PG独立审核夹具"},
            headers={"Idempotency-Key": m.uid()},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(publish, range(2)))
    assert sorted(statuses) == [200, 409]
    database = Database(settings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(settings, database), range(2)))
    acquired = [c for c in claims if c]
    assert len(acquired) == 1
    # Reclaim after a simulated crashed dispatcher without running duplicate children.
    with database.session() as db:
        task = db.get(m.LearningTask, acquired[0][0])
        task.status = "queued"
        task.lease_token = ""
        task.lease_until = None
    assert run_once(settings, database)
    effective = expert.get("/api/learning/" + condition["id"]).json()
    assert effective["status"] == "effective"
    result = expert.get(
        "/api/records/learning-records?kind=condition&line_id=" + line["id"] + "&page_size=1"
    ).json()
    assert result["total"] == 1 and result["items"][0]["id"] == effective["id"]
    foreign = settings.model_copy(update={"tenant_id": "other-customer"})
    with Database(foreign).session() as db:
        assert db.scalars(select(m.LearningRecord)).all() == []
    with database.session(sites=["factory-b"]) as db:
        assert db.scalars(select(m.LearningRecord)).all() == []
    with database.session() as db:
        flags = db.execute(
            text(
                "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname IN ('learning_records','learning_tasks','study_groups')"
            )
        ).all()
        assert len(flags) == 3 and all(r[1] and r[2] for r in flags)


def test_postgres_group_reservation_is_atomic_and_holdout_is_not_development(postgres):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from quality.app import create_app
    from .conftest import post

    settings, _ = postgres
    admin = TestClient(create_app(settings))
    post(admin, "/auth/login", {"username": "admin", "password": PASSWORD})
    line = post(
        admin,
        "/masters",
        {"kind": "line", "code": "GROUP-PG", "name": "PG分组隔离夹具", "data": {"process": "plastic_spray"}},
    )
    case = post(
        admin,
        "/cases",
        {"title": "锁定集PG合成案例", "line_id": line["id"], "process": "plastic_spray", "defect": "颗粒"},
    )
    now = current_time().isoformat()
    body = {
        "title": "PG锁定组",
        "scope": {"line_id": line["id"], "process": "plastic_spray"},
        "split": "locked_test",
        "task": "post_inspection_diagnosis",
        "usage_rights": "仅合成验证，无现场数据",
        "target_definition": "核对来源分组互斥",
        "items": [
            {
                "case_id": case["id"],
                "case_version": case["version"],
                "sample_role": "abnormal",
                "expected_match": True,
                "label_basis": "人工登记PG事务测试",
                "decision_time": now,
                "label_time": now,
            }
        ],
    }

    def reserve(_):
        return admin.post(
            "/api/learning/datasets", json=body, headers={"Idempotency-Key": m.uid()}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(reserve, range(2)))
    assert sorted(statuses) == [200, 409]
    with Database(settings).session() as db:
        assert db.scalar(select(m.StudyGroup).where(m.StudyGroup.split == "locked_test"))


def test_postgres_secondary_json_filters_and_center_tasks_count_before_page(postgres):
    from quality.records import collection
    from quality.querying import QueryScope
    settings,_=postgres
    database=Database(settings)
    with database.session() as db:
        svc=Service(db,settings,Actor('json-fixture','admin',['factory-a']))
        line=svc.add(m.Master,kind='line',code='JSON-PAGING',name='JSON分页PG夹具',data={'process':'plastic_spray'})
        line_id=line.id
        for n in range(3):
            svc.add(m.Knowledge,title=f'JSON过滤夹具{n}',category='case',content='合成验证',author_id='fixture',status='published',scope={'factory_ids':['factory-a'],'line_ids':[line_id] if n==2 else ['foreign-line'],'process':'all'},source={},rules={},valid_until=current_time()+__import__('datetime').timedelta(days=1))
        for n,ids in enumerate([[line_id],[line_id,'foreign-line']]):
            svc.add(m.Distribution,scope_line_id='',target_node='fixture',author_id='fixture',kind='knowledge',record_id=f'json-filter-{n}',action='publish',package={'body':{'record':{'scope':{'line_ids':ids}}}},expires_at=current_time()+__import__('datetime').timedelta(hours=1))
        for n,author in enumerate(['json-reviewer','other-expert']):
            svc.add(m.Snapshot,scope_line_id=line_id,source_node='json-fixture',entity_id=f'center-task-{n}',version=1,data={'title':'中心JSON待办夹具','status':'awaiting_approval','context':{},'records':{'plans':[{'id':str(n),'status':'awaiting_approval','author_id':author,'expires_at':(current_time()+__import__('datetime').timedelta(hours=1)).isoformat()}]}})
    with database.session(sites=['factory-a'],lines=[line_id]) as db:
        svc=Service(db,settings,Actor('json-reviewer','approver',['factory-a'],lines=[line_id]))
        result=collection(svc,'knowledge',QueryScope(line_id=line_id),q='JSON过滤夹具',page_size=1)
        assert result['total']==1 and result['items'][0]['title']=='JSON过滤夹具2'
        distributions=collection(svc,'distributions',QueryScope(),q='json-filter-',page_size=1)
        assert distributions['total']==1 and distributions['items'][0]['record_id']=='json-filter-0'
        center=Service(db,settings.model_copy(update={'node_role':'center','site_id':''}),svc.actor)
        tasks=collection(center,'tasks',QueryScope(line_id=line_id),q='中心JSON待办夹具',page_size=1)
        assert tasks['total']==1 and tasks['items'][0]['case_id']=='center-task-1'


def test_postgres_learning_data_and_group_assignments_survive_full_backup_restore(postgres,tmp_path):
    from quality.backup import backup, restore
    from sqlalchemy.engine import make_url
    from quality.service import serialize
    settings,uri=postgres
    classes=[m.LearningRecord,m.LearningTask,m.StudyGroup]
    with Database(settings).session() as db:
        before={cls.__tablename__:sorted([serialize(r) for r in db.scalars(select(cls))],key=lambda r:r['id']) for cls in classes}
    assert all(before.values()),'Earlier synthetic learning records, jobs and groups must exist'
    archive=tmp_path/'learning-complete-backup'
    backup(settings,archive)
    with psycopg.connect(uri,autocommit=True) as db:
        db.execute('CREATE DATABASE quality_learning_restore')
    migration=make_url(settings.migration_database_url).set(database='quality_learning_restore').render_as_string(hide_password=False)
    fresh=settings.model_copy(update={'database_url':make_url(settings.database_url).set(database='quality_learning_restore').render_as_string(hide_password=False),'migration_database_url':migration,'data_dir':tmp_path/'learning-restored-runtime'})
    assert restore(fresh,archive)['restored']
    with psycopg.connect(make_url(migration).set(drivername='postgresql').render_as_string(hide_password=False),autocommit=True) as db:
        db.execute('GRANT USAGE ON SCHEMA public TO quality_runtime')
        db.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO quality_runtime')
        db.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO quality_runtime')
    migrate(fresh)
    Database(fresh).check_runtime_role()
    with Database(fresh).session() as db:
        after={cls.__tablename__:sorted([serialize(r) for r in db.scalars(select(cls))],key=lambda r:r['id']) for cls in classes}
        assert after==before
