"""Real persistent claims and bounded child execution; no simulated success writes."""

from datetime import timedelta
from quality.db import Database
from quality.digital_tasks import run_once
from quality.security import current_time
from .conftest import post
from .test_industrial_formulations import setup_formula, approve


def policy(clients, line, file, level="D1", frequency=10):
    body = {
        "code": "POLICY-TEST",
        "name": "软件验证受控算量策略",
        "scope": {"line_id": line["id"], "process": line["data"]["process"], "quantity_unit": "kg"},
        "valid_until": (current_time() + timedelta(days=2)).isoformat(),
        "evidence_ids": [file],
        "automation_level": level,
        "actor_roles": ["admin", "engineer"],
        "actions": ["formulation.calculate"],
        "max_quantity": "20",
        "max_amount": "0",
        "currency": "CNY",
        "window_seconds": 3600,
        "max_frequency": frequency,
        "offline_allowed": False,
        "required_evidence_kinds": ["evidence"],
    }
    row = post(clients["admin"], "/industrial/policies", body)
    row = post(
        clients["admin"],
        f"/industrial/governance/policy/{row['id']}",
        {"version": row["version"], "action": "submit", "reason": "管理员提交明确动作、单位、数量和频率边界"},
    )
    row = post(
        clients["approver"],
        f"/industrial/governance/policy/{row['id']}",
        {"version": row["version"], "action": "publish", "reason": "独立审核系统责任及预授权边界"},
    )
    return row


def job(line, formula, stocks, file):
    return {
        "line_id": line["id"],
        "action": "formulation.calculate",
        "object_id": formula["id"],
        "object_version": str(formula["version"]),
        "quantity": "10",
        "amount": "0",
        "currency": "CNY",
        "evidence_ids": [file],
        "payload": {
            "line_id": line["id"],
            "formulation_id": formula["id"],
            "formulation_version": formula["version"],
            "product_id": "software-test",
            "basis": "total",
            "quantity": "10",
            "unit": "kg",
            "inventory_ids": [r["id"] for r in stocks],
        },
    }


def get(clients, id):
    r = clients["admin"].get("/api/industrial/digital-tasks/" + id)
    assert r.status_code == 200, r.text
    return r.json()


def test_runtime_queue_metrics_preserve_role_and_line_scope(core):
    clients, line, file, _, stocks, body = setup_formula(core)
    formula = approve(clients, body)
    policy(clients, line, file)
    post(clients["engineer"], "/industrial/digital-tasks", job(line, formula, stocks, file))
    current = clients["admin"].get("/api/industrial/status", params={"line_id": line["id"]}).json()["runtime_metrics"]
    assert current["digital_tasks"]["by_status"] == {"queued": 1}
    assert current["digital_tasks"]["oldest_pending_age_seconds"] >= 0
    assert current["business_commands"]["awaiting_source_confirmation"] == 0
    other = next(row for row in core[3] if row["id"] != line["id"])
    empty = clients["admin"].get("/api/industrial/status", params={"line_id": other["id"]}).json()["runtime_metrics"]
    assert empty["digital_tasks"]["by_status"] == {}
    assert empty["digital_tasks"]["oldest_pending_age_seconds"] is None
    assert "runtime_metrics" not in clients["viewer"].get("/api/industrial/status").json()
    product = clients["admin"].get("/api/industrial/status", params={"product_id": "software-test"}).json()["runtime_metrics"]
    assert product["availability"] == "clear_product_filter" and "digital_tasks" not in product


def test_default_deny_typed_actions_frequency_and_real_child_execution(core):
    settings, _, _, _ = core
    clients, line, file, _, stocks, body = setup_formula(core)
    formula = approve(clients, body)
    data = job(line, formula, stocks, file)
    denied = post(clients["engineer"], "/industrial/digital-tasks", data)
    assert denied["status"] == "blocked" and not denied["task_created"]
    assert denied["policy_decision"]["allowed"] is False
    post(clients["engineer"], "/industrial/digital-tasks", {**data, "action": "plc.write"}, expected=422)
    post(
        clients["engineer"],
        "/industrial/digital-tasks",
        {**data, "payload": {"sql": "not-a-tool"}},
        expected=422,
    )
    row = policy(clients, line, file, frequency=1)
    created = post(clients["engineer"], "/industrial/digital-tasks", data, key="digital-stable-request")
    assert created["status"] == "queued"
    task = created["task"]
    again = post(clients["engineer"], "/industrial/digital-tasks", data, key="digital-stable-request")
    assert again["task"]["id"] == task["id"]
    assert post(clients["engineer"], "/industrial/digital-tasks", data)["status"] == "blocked"
    assert clients["admin"].get("/api/records/industrial-calculations").json()["total"] == 0
    assert run_once(settings, Database(settings))
    done = get(clients, task["id"])
    assert done["status"] == "succeeded", done
    assert done["result"]["record"]["status"] == "ready"
    assert done["responsibility"] == "system:policy:" + row["id"]
    assert "lease_token" not in done and "authorization_hash" not in done
    assert not run_once(settings, Database(settings))
    assert clients["admin"].get("/api/records/industrial-calculations").json()["total"] == 1
    assert clients["admin"].get("/api/records/learning-tasks").json()["total"] == 0


def test_d2_independent_review_expiry_cancel_and_policy_revalidation(core):
    settings, _, _, _ = core
    clients, line, file, _, stocks, body = setup_formula(core)
    formula = approve(clients, body)
    pol = policy(clients, line, file, level="D2")
    data = job(line, formula, stocks, file)
    created = post(clients["admin"], "/industrial/digital-tasks", data)
    task = created["task"]
    assert task["status"] == "awaiting_approval"
    assert not run_once(settings, Database(settings))
    review = {
        "version": task["version"],
        "approved": True,
        "reason": "逐次核对数量及当前批准材料配方",
        "expires_at": (current_time() + timedelta(hours=1)).isoformat(),
    }
    post(clients["admin"], f"/industrial/digital-tasks/{task['id']}/review", review, expected=409)
    approved = post(clients["approver"], f"/industrial/digital-tasks/{task['id']}/review", review)
    assert approved["status"] == "queued"
    paused = post(
        clients["admin"],
        f"/industrial/digital-tasks/{task['id']}/control",
        {"version": approved["version"], "action": "pause", "reason": "暂停等待材料复核"},
    )
    assert not run_once(settings, Database(settings))
    resumed = post(
        clients["admin"],
        f"/industrial/digital-tasks/{task['id']}/control",
        {"version": paused["version"], "action": "resume", "reason": "复核原数据后恢复"},
    )
    assert resumed["status"] == "queued"
    post(
        clients["approver"],
        f"/industrial/governance/policy/{pol['id']}",
        {"version": pol["version"], "action": "retire", "reason": "软件验证：执行前停用预授权"},
    )
    assert run_once(settings, Database(settings))
    failed = get(clients, task["id"])
    assert failed["status"] == "failed" and failed["error_code"] == "digital_policy_changed"
    assert clients["admin"].get("/api/records/industrial-calculations").json()["total"] == 0
    cancelled = post(
        clients["admin"],
        f"/industrial/digital-tasks/{task['id']}/control",
        {"version": failed["version"], "action": "cancel", "reason": "取消未提交的旧策略任务"},
    )
    assert cancelled["status"] == "cancelled"


def test_batch_preview_never_enqueues_and_mixed_result_is_explicit(core):
    _, _, _, _ = core
    clients, line, file, _, stocks, body = setup_formula(core)
    formula = approve(clients, body)
    policy(clients, line, file)
    data = job(line, formula, stocks, file)
    tasks = [data, {**data, "quantity": "30"}]
    result = post(
        clients["engineer"], "/industrial/digital-tasks/batch", {"tasks": tasks, "execute_valid": False}
    )
    assert result["created_count"] == 0 and result["blocked_count"] == 1
    assert result["results"][0]["status"] == "eligible"
    assert clients["admin"].get("/api/records/industrial-digital-tasks").json()["total"] == 0
    result = post(
        clients["engineer"], "/industrial/digital-tasks/batch", {"tasks": tasks, "execute_valid": True}
    )
    assert result["created_count"] == 1 and result["blocked_count"] == 1


def test_claim_lease_fencing_cancel_and_revoked_authorization(core):
    from quality.digital_tasks import claim, run_claimed

    settings, _, _, _ = core
    clients, line, file, _, stocks, body = setup_formula(core)
    formula = approve(clients, body)
    policy(clients, line, file)
    data = job(line, formula, stocks, file)
    task = post(clients["engineer"], "/industrial/digital-tasks", data)["task"]
    claimed = claim(settings, Database(settings))
    assert claimed and claimed[0] == task["id"]
    assert claim(settings, Database(settings)) is None
    current = get(clients, task["id"])
    cancelled = post(
        clients["engineer"],
        f"/industrial/digital-tasks/{task['id']}/control",
        {"version": current["version"], "action": "cancel", "reason": "核对后取消此在途算量任务"},
    )
    assert cancelled["status"] == "cancelled"
    run_claimed(settings, *claimed)
    assert get(clients, task["id"])["status"] == "cancelled"
    assert clients["admin"].get("/api/records/industrial-calculations").json()["total"] == 0
    task = post(clients["engineer"], "/industrial/digital-tasks", data)["task"]
    actor = clients["engineer"].get("/api/auth/me").json()["id"]
    post(clients["admin"], f"/users/{actor}/revoke")
    assert run_once(settings, Database(settings))
    failed = get(clients, task["id"])
    assert failed["status"] == "failed" and failed["error_code"] == "authorization_revoked"
    assert clients["admin"].get("/api/records/industrial-calculations").json()["total"] == 0


def test_digital_tasks_are_scoped_before_counts_and_detail_reads(core):
    from fastapi.testclient import TestClient
    from .conftest import PASSWORD

    clients, line, file, _, stocks, body = setup_formula(core)
    formula = approve(clients, body)
    policy(clients, line, file)
    created = post(clients["admin"], "/industrial/digital-tasks", job(line, formula, stocks, file))["task"]
    assert created["scope_line_id"] == line["id"]
    other = next(r for r in core[3] if r["id"] != line["id"])
    post(
        clients["admin"],
        "/users",
        {
            "username": "other-line-engineer",
            "password": PASSWORD,
            "role": "engineer",
            "allowed_sites": [core[0].site_id],
            "allowed_lines": [other["id"]],
        },
    )
    restricted = TestClient(core[1])
    post(restricted, "/auth/login", {"username": "other-line-engineer", "password": PASSWORD})
    assert restricted.get("/api/records/industrial-digital-tasks").json()["total"] == 0
    assert restricted.get("/api/industrial/digital-tasks/" + created["id"]).status_code == 404
