"""Temporary synthetic queues verify scope/filter/count before pagination and column contracts."""

from datetime import timedelta
from sqlalchemy import select, text
from quality import models as m
from quality.db import Database
from quality.security import Actor, current_time
from quality.service import Service
from quality.cli import migrate
from .conftest import post
from .test_workflow import case
from .test_expert_learning import publish_condition, review
from quality.learning_tasks import claim, run_once


def test_secondary_queues_filter_before_count_and_preserve_stable_sort(core):
    settings, _, clients, lines = core
    db = Database(settings)
    with db.session() as session:
        svc = Service(session, settings, Actor("fixture", "admin", settings.sites))
        for n in range(1207):
            svc.add(
                m.Knowledge,
                scope_line_id="",
                title=f"分页夹具 {n:04}",
                category="case",
                author_id="fixture",
                content="开发夹具",
                status="published",
                revision=1,
                scope={"line_ids": [lines[0]["id"]] if n >= 1200 else [lines[1]["id"]], "process": "all"},
                source={},
                rules={},
                valid_until=current_time() + timedelta(days=1),
            )
    url = (
        "/api/records/knowledge?line_id="
        + lines[0]["id"]
        + "&q=分页夹具&page_size=3&sort_by=title&sort_order=asc"
    )
    first = clients["engineer"].get(url).json()
    second = clients["engineer"].get(url + "&page=2").json()
    assert first["total"] == second["total"] == 7
    assert [r["title"] for r in first["items"]] == [f"分页夹具 {n}" for n in ["1200", "1201", "1202"]]
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
    assert clients["engineer"].get(url + "&sort_by=payload;drop").status_code == 422
    assert clients["engineer"].get("/api/records/users").status_code == 404
    assert clients["engineer"].get("/api/records/raw-records").status_code == 403
    assert clients["engineer"].get("/api/records/cases?page_size=201").status_code == 422
    assert clients["engineer"].get("/api/records/knowledge?q=%25").json()["total"] == 0
    for resource in [
        "masters",
        "routes",
        "inspections",
        "files",
        "lab-results",
        "adapters",
        "sites",
        "snapshots",
        "remote-requests",
        "dispositions",
        "recipes",
        "distributions",
        "audit",
        "runs",
        "events",
        "measurements",
        "learning-records",
        "learning-tasks",
        "tasks",
    ]:
        response = clients["admin"].get("/api/records/" + resource + "?page_size=1")
        assert response.status_code == 200, (resource, response.text)
        data = response.json()
        assert len(data["items"]) <= 1 and data["total"] >= len(data["items"])


def test_tasks_count_excludes_authors_and_final_released_objects_before_paging(core):
    settings, _, clients, lines = core
    database = Database(settings)
    engineer_id = clients["engineer"].get("/api/auth/me").json()["id"]
    with database.session() as session:
        svc = Service(session, settings, Actor("fixture", "admin", settings.sites))
        for n in range(135):
            row = svc.add(
                m.Case,
                scope_line_id=lines[0]["id"],
                line_id=lines[0]["id"],
                process=lines[0]["data"]["process"],
                title=f"待办夹具 {n:03}",
                status="awaiting_approval",
                author_id="fixture",
                context={"defect": "颗粒"},
                diagnosis={},
                outcome={},
            )
            svc.add(
                m.Plan,
                scope_line_id=lines[0]["id"],
                case_id=row.id,
                revision=1,
                author_id=engineer_id if n >= 130 else "other-expert",
                status="awaiting_approval",
                content={},
                fingerprint="fixture",
                expires_at=current_time() + timedelta(hours=1),
                context_hash="fixture",
            )
    own = clients["engineer"].get("/api/records/tasks?q=待办夹具&page_size=2").json()
    # Engineers are not independent approvers in the actual role matrix.
    assert own["total"] == 0
    data = (
        clients["approver"]
        .get("/api/records/tasks?q=待办夹具&page=3&page_size=50&sort_by=title&sort_order=asc")
        .json()
    )
    assert data["total"] == 135 and len(data["items"]) == 35
    assert data["counts_by_state"] == {"awaiting_approval": 135}
    legacy = clients["approver"].get("/api/workspace/tasks").json()
    assert legacy["total"] >= 135 and len(legacy["items"]) == 100


def test_upgrade_0010_keeps_old_case_and_creates_learning_tables(core):
    settings, _, clients, lines = core
    row = case(clients["engineer"], lines[0])
    database = Database(settings)
    with database.engine.begin() as db:
        for name in ["study_groups", "learning_tasks", "learning_records"]:
            db.execute(text("DROP TABLE " + name))
        db.execute(text("UPDATE alembic_version SET version_num='0010'"))
    assert clients["admin"].get("/api/ready").status_code == 503
    migrate(settings)
    restored = clients["engineer"].get("/api/cases/" + row["id"]).json()
    assert restored == {**restored, **row}
    assert clients["admin"].get("/api/ready").status_code == 200
    assert clients["engineer"].get("/api/records/learning-records").json()["total"] == 0


def test_learning_queue_capacity_lease_recovery_and_preserved_ingestion(core):
    settings, app, clients, lines = core
    from .test_expert_learning import opinion

    def experience_body(row):
        return {
            "title": "持久队列夹具",
            "case_id": row["id"],
            "case_version": row["version"],
            "opinions": [opinion()],
        }

    row = case(clients["engineer"], lines[0])
    record = post(clients["engineer"], "/learning/experiences", experience_body(row))
    record = review(clients["engineer"], record, "submit")
    record = review(clients["approver"], record, "publish")
    app.state.settings.learning_queue_limit = 1
    other = post(clients["engineer"], "/learning/experiences", experience_body(row))
    other = review(clients["engineer"], other, "submit")
    post(
        clients["approver"],
        f"/learning/{other['id']}/review",
        {"version": other["version"], "action": "publish", "reason": "开发夹具审核排队"},
        expected=429,
    )
    database = Database(settings)
    claimed = claim(settings, database)
    assert claimed
    with database.session() as db:
        task = db.get(m.LearningTask, claimed[0])
        task.lease_until = current_time() - timedelta(seconds=1)
        task.attempts = 3
    assert claim(settings, database) is None
    failed = clients["engineer"].get("/api/learning/" + record["id"]).json()
    assert failed["status"] == "apply_failed"
    assert (
        clients["engineer"].get("/api/records/learning-tasks").json()["items"][0]["error_code"]
        == "lease_attempts_exhausted"
    )
    # A failed learning task leaves ordinary acceptance available.
    assert case(clients["engineer"], lines[0])["status"] == "needs_evidence"
    assert not run_once(settings, database)


def test_condition_freshness_nonfinite_and_rule_type_are_explicit(core):
    from types import SimpleNamespace
    from quality.diagnostics import evaluate_condition

    definition = publish_condition(core)
    _, _, clients, lines = core
    row = case(clients["engineer"], lines[0])
    from .test_expert_learning import value

    old = value(
        definition,
        0,
        occurred_at=(current_time() - timedelta(hours=2)).isoformat(),
        available_at=(current_time() - timedelta(hours=2)).isoformat(),
    )
    result = post(
        clients["engineer"], f"/cases/{row['id']}/conditions", {"version": row["version"], "values": [old]}
    )
    assert result["conditions"]["fields"][0]["state"] == "stale"
    c = {"metric": "m", "operator": "gt", "value": 0, "unit": "degC"}
    invalid = SimpleNamespace(
        id="invalid",
        equipment_id="e",
        metric="m",
        value=float("nan"),
        unit="degC",
        value_type="measured",
        quality="Good",
        occurred_at=current_time(),
    )
    assert evaluate_condition(c, [invalid])["state"] == "invalid_quality"
    body = {
        "title": "拒绝混淆人工与实测",
        "scope": definition["payload"]["scope"],
        "definition_ids": [definition["id"]],
        "rationale": "核对数值性质",
        "rule": {
            "dimension": "process",
            "defects": ["颗粒"],
            "cause": "候选原因",
            "next_checks": ["人工检查"],
            "conditions": [
                {
                    "metric": definition["payload"]["stable_id"],
                    "unit": "degC",
                    "operator": "eq",
                    "value": False,
                    "value_type": "manual",
                }
            ],
        },
    }
    post(clients["engineer"], "/learning/rules", body, expected=422)


def test_all_matching_export_is_not_silently_cut_at_one_thousand(core):
    import csv
    import io

    settings, _, clients, lines = core
    with Database(settings).session() as db:
        svc = Service(db, settings, Actor("fixture", "admin", settings.sites))
        for n in range(1007):
            svc.add(
                m.Case,
                scope_line_id=lines[0]["id"],
                line_id=lines[0]["id"],
                process=lines[0]["data"]["process"],
                title=f"完整导出夹具 {n:04}",
                author_id="fixture",
                context={"defect": "颗粒"},
                diagnosis={},
                outcome={},
            )
    query = "q=完整导出夹具&line_id=" + lines[0]["id"]
    records = clients["engineer"].get("/api/cases?" + query + "&page=41&page_size=25").json()
    assert records["total"] == 1007 and len(records["items"]) == 7
    response = clients["engineer"].get("/api/export/cases?" + query)
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 1007 and len({r["id"] for r in rows}) == 1007
    assert {r["line_id"] for r in rows} == {lines[0]["id"]}
    assert {r["export_scope"] for r in rows} == {"全部授权筛选结果"}


def test_missing_condition_has_no_imputed_zero_and_does_not_enter_measurements(core):
    from .test_expert_learning import value

    definition = publish_condition(core)
    _, _, clients, lines = core
    row = case(clients["engineer"], lines[0])
    response = post(
        clients["engineer"],
        f"/cases/{row['id']}/conditions",
        {"version": row["version"], "values": [value(definition, None, quality="missing")]},
    )
    field = response["conditions"]["fields"][0]
    assert field["observation"]["value"] is None and field["state"] == "missing"
    diagnosed = post(clients["engineer"], f"/cases/{row['id']}/diagnose", {"version": row["version"]})
    assert not any(
        r["metric"] == definition["payload"]["stable_id"]
        for r in diagnosed["context"]["analysis_snapshot"]["measurements"]
    )
    post(
        clients["engineer"],
        f"/cases/{row['id']}/conditions",
        {"version": diagnosed["version"], "values": [value(definition, 0, quality="missing")]},
        expected=422,
    )


def test_learning_backpressure_counts_other_authorized_factory_lines_without_disclosing_rows(core):
    from .test_expert_learning import opinion

    settings, app, clients, lines = core
    settings.learning_queue_limit = 1
    first = case(clients["engineer"], lines[0])

    def body(c):
        return {
            "title": "跨线队列容量夹具",
            "case_id": c["id"],
            "case_version": c["version"],
            "opinions": [opinion()],
        }

    row = post(clients["engineer"], "/learning/experiences", body(first))
    row = review(clients["engineer"], row, "submit")
    review(clients["admin"], row, "publish")
    second = case(clients["engineer"], lines[1])
    row = post(clients["engineer"], "/learning/experiences", body(second))
    row = review(clients["engineer"], row, "submit")
    with Database(settings).session() as db:
        reviewer = db.scalar(select(m.User).where(m.User.username == "approver"))
        reviewer.allowed_lines = [lines[1]["id"]]
        reviewer.auth_revision += 1
    # Revoking grants invalidates old sessions; a fresh session remains line-scoped.
    assert clients["approver"].get("/api/records/learning-tasks").status_code == 401
    import json

    credentials = json.loads((settings.data_dir / "demo-credentials.json").read_text())
    post(clients["approver"], "/auth/login", {"username": "approver", "password": credentials["approver"]})
    # Reading another line's job is forbidden, but it still occupies site capacity.
    assert clients["approver"].get("/api/records/learning-tasks").json()["total"] == 0
    response = post(
        clients["approver"],
        f"/learning/{row['id']}/review",
        {"version": row["version"], "action": "publish", "reason": "跨线容量统一核对"},
        expected=429,
    )
    assert response["detail"]["code"] == "learning_backpressure"
