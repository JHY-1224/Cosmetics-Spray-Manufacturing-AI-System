"""Real HTTP, durable source state, response loss and original-key reconciliation."""

import json
import threading
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from quality import models as m
from quality.backup import backup, restore
from quality.business_gateway import claim, run_once
from quality.business_reference import ReferenceStore, server
from quality.db import Database
from quality.digital_tasks import run_once as digital_once
from quality.security import current_time, sign
from .conftest import post
from .test_industrial_formulations import setup_formula
from .test_industrial_supply_cost import order_fixture


@pytest.fixture
def reference(tmp_path, monkeypatch):
    secret, admin = "reference-app-test-secret-2294", "reference-admin-test-secret-5541"
    monkeypatch.setenv("QUALITY_BUSINESS_SECRET_REFERENCE", secret)
    store = ReferenceStore(tmp_path / "source-state.sqlite", secret, admin, "synthetic-source")
    http = server(store)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(
        base_url=f"http://127.0.0.1:{http.server_port}",
        headers={"Authorization": "Bearer " + admin},
        trust_env=False,
    ) as client:
        yield store, client, secret
    http.shutdown()
    http.server_close()
    thread.join(timeout=2)


def connector(clients, line, ref, actions):
    store, http, _ = ref
    return post(
        clients["admin"],
        "/industrial/connectors",
        {
            "line_id": line["id"],
            "name": "隔离软件验证持久HTTP源",
            "system": "WMS",
            "endpoint": str(http.base_url).rstrip("/"),
            "credential_reference": "QUALITY_BUSINESS_SECRET_REFERENCE",
            "actions": actions,
            "enabled": True,
            "supports_idempotency": True,
            "supports_lookup": True,
            "authority": store.authority,
        },
    )


def policy(clients, line, file, actions, unit, level="D1"):
    row = post(
        clients["admin"],
        "/industrial/policies",
        {
            "code": "BUSINESS-" + actions[0],
            "name": "软件验证明确外部动作策略",
            "scope": {"line_id": line["id"], "process": line["data"]["process"], "quantity_unit": unit},
            "valid_until": (current_time() + timedelta(days=1)).isoformat(),
            "evidence_ids": [file],
            "automation_level": level,
            "actor_roles": ["admin"],
            "actions": actions,
            "max_quantity": "100",
            "max_amount": "10000",
            "currency": "CNY",
            "window_seconds": 3600,
            "max_frequency": 20,
            "offline_allowed": False,
            "required_evidence_kinds": ["evidence"],
        },
    )
    row = post(
        clients["admin"],
        f"/industrial/governance/policy/{row['id']}",
        {"version": row["version"], "action": "submit", "reason": "提交固定外部动作与业务影响范围"},
    )
    return post(
        clients["approver"],
        f"/industrial/governance/policy/{row['id']}",
        {"version": row["version"], "action": "publish", "reason": "独立核对数量金额和业务副作用"},
    )


def get(clients, id):
    response = clients["admin"].get("/api/industrial/business-commands/" + id)
    assert response.status_code == 200, response.text
    return response.json()


def authorize(core, body):
    settings, _, clients, _ = core
    row = post(clients["admin"], "/industrial/business-commands", body)
    result = post(
        clients["admin"],
        f"/industrial/business-commands/{row['id']}/control",
        {"version": row["version"], "action": "submit", "reason": "软件验证提交当前意图的受控授权"},
    )
    task = result["authorization"]["task"]
    if task["status"] == "awaiting_approval":
        task = post(
            clients["approver"] if task["kind"] == "retest.create" else clients["admin"],
            f"/industrial/digital-tasks/{task['id']}/review",
            {
                "version": task["version"],
                "approved": True,
                "reason": "另一个获授权账号逐次审核采购依据",
                "expires_at": (current_time() + timedelta(hours=1)).isoformat(),
            },
        )
    assert digital_once(settings, Database(settings))
    done = clients["admin"].get("/api/industrial/digital-tasks/" + task["id"]).json()
    assert done["status"] == "succeeded", done
    assert get(clients, row["id"])["status"] == "queued"
    return row


def inventory_case(core, reference):
    clients, line, file, materials, stocks, _ = setup_formula(core)
    settings = core[0]
    settings.business_send_enabled = True
    conn = connector(clients, line, reference, ["inventory.reserve", "inventory.release"])
    pol = policy(clients, line, file, conn["actions"], "kg")
    stock, material = stocks[0], materials[0]
    response = reference[1].post(
        "/admin/control",
        json={
            "inventory": [
                {
                    "material_code": material["code"],
                    "warehouse": stock["warehouse"],
                    "lot": stock["lot"],
                    "on_hand": "100",
                    "held": "0",
                    "reserved": "0",
                    "unit": "kg",
                    "version": 1,
                }
            ]
        },
    )
    assert response.status_code == 200
    hold = post(clients["admin"], "/industrial/reservations", {"inventory_id": stock["id"], "quantity": "5"})
    body = {
        "line_id": line["id"],
        "connector_id": conn["id"],
        "policy_id": pol["id"],
        "evidence_ids": [file],
        "expected_result": "inventory_reserved",
        "payload": {
            "action": "inventory.reserve",
            "target_id": stock["id"],
            "target_version": stock["source_version"],
            "material_id": material["id"],
            "quantity": "5",
            "unit": "kg",
            "reference_id": hold["id"],
            "reason": "隔离软件验证真实预占源状态",
        },
    }
    return clients, line, file, material, stock, hold, body


def lookup_now(clients, id):
    row = get(clients, id)
    return post(
        clients["admin"],
        f"/industrial/business-commands/{id}/control",
        {"version": row["version"], "action": "lookup", "reason": "按原幂等键核对外部结果"},
    )


def test_response_lost_keeps_outbox_and_reconciles_original_key_once(core, reference):
    clients, _, _, _, _, hold, body = inventory_case(core, reference)
    row = authorize(core, body)
    reference[1].post("/admin/control", json={"fault": "response_lost"}).raise_for_status()
    assert run_once(core[0], Database(core[0]))
    unknown = get(clients, row["id"])
    assert unknown["status"] == "unknown" and unknown["fencing"] == 1
    state = reference[1].post("/admin/state", json={}).json()
    assert state["applied_count"] == 1 and Decimal(next(iter(state["inventory"].values()))["reserved"]) == 5
    # The ambiguous source effect blocks further availability instead of deducting a guessed amount.
    post(
        clients["admin"],
        "/industrial/reservations",
        {"inventory_id": body["payload"]["target_id"], "quantity": "1"},
        expected=409,
    )
    post(
        clients["admin"],
        f"/industrial/business-commands/{row['id']}/control",
        {"version": unknown["version"], "action": "send", "reason": "不允许换键盲重试"},
        expected=409,
    )
    lookup_now(clients, row["id"])
    assert run_once(core[0], Database(core[0]))
    applied = get(clients, row["id"])
    assert applied["status"] == "applied" and applied["idempotency_key"] == row["idempotency_key"]
    assert clients["admin"].get("/api/industrial/reservations/" + hold["id"]).json()["status"] == "confirmed"
    assert reference[1].post("/admin/state", json={}).json()["applied_count"] == 1
    attempts = (
        clients["admin"]
        .get("/api/records/industrial-business-attempts?parent_id=" + row["id"])
        .json()["items"]
    )
    assert {a["kind"] for a in attempts} == {"send", "lookup"}
    assert "lease_token" not in applied


def test_accepted_not_applied_signed_callback_duplicates_ordering_and_release(core, reference):
    clients, line, file, material, _, hold, body = inventory_case(core, reference)
    row = authorize(core, body)
    reference[1].post("/admin/control", json={"fault": "accepted"}).raise_for_status()
    assert run_once(core[0], Database(core[0]))
    assert get(clients, row["id"])["status"] == "accepted"
    state = reference[1].post("/admin/state", json={}).json()
    accepted = next(iter(state["commands"].values()))["receipt"]
    assert state["applied_count"] == 0
    reference[1].post("/admin/tick", json={}).raise_for_status()
    applied = next(iter(reference[1].post("/admin/state", json={}).json()["commands"].values()))["receipt"]
    url = "/api/industrial/business-callbacks/" + row["id"]
    assert clients["admin"].post(url, json=applied).status_code == 403
    headers = {"Authorization": "Bearer " + reference[2]}
    assert clients["admin"].post(url, content=b"x" * 65537, headers=headers).status_code == 413
    assert clients["admin"].post(url, content=b"not-json", headers=headers).status_code == 422
    assert get(clients, row["id"])["status"] == "accepted"
    forged = json.loads(json.dumps(applied))
    forged["body"]["target_id"] = "forged-other-target"
    forged["signature"] = sign(forged["body"], reference[2])
    assert clients["admin"].post(url, json=forged, headers=headers).status_code == 409
    assert clients["admin"].post(url, json=applied, headers=headers).json()["outcome"] == "recorded"
    assert clients["admin"].post(url, json=applied, headers=headers).json()["outcome"] == "duplicate"
    assert clients["admin"].post(url, json=accepted, headers=headers).json()["outcome"] == "duplicate"
    assert get(clients, row["id"])["status"] == "applied"
    confirmed = clients["admin"].get("/api/industrial/reservations/" + hold["id"]).json()
    release = {
        **body,
        "expected_result": "inventory_released",
        "payload": {
            "action": "inventory.release",
            "target_id": confirmed["id"],
            "target_version": str(confirmed["version"]),
            "material_id": material["id"],
            "quantity": "5",
            "unit": "kg",
            "reference_id": confirmed["source_reference"],
            "reason": "明确源预占引用的完整释放",
        },
    }
    released = authorize(core, release)
    assert run_once(core[0], Database(core[0]))
    assert get(clients, released["id"])["status"] == "applied"
    assert (
        Decimal(
            next(iter(reference[1].post("/admin/state", json={}).json()["inventory"].values()))["reserved"]
        )
        == 0
    )


def test_default_switch_restore_freeze_and_source_epoch_fence(core, reference, tmp_path):
    clients, _, _, _, _, _, body = inventory_case(core, reference)
    row = authorize(core, body)
    settings = core[0]
    settings.business_send_enabled = False
    assert not run_once(settings, Database(settings))
    settings.business_send_enabled = True
    item = claim(settings, Database(settings))
    assert item and get(clients, row["id"])["status"] == "sending"
    # The source processes a request only after its durable outbox/attempt is committed.
    with httpx.Client(trust_env=False) as http:
        response = http.post(
            item["endpoint"] + "/commands",
            json=item["request"],
            headers={"Authorization": "Bearer " + reference[2]},
        )
        assert response.status_code == 200 and response.json()["body"]["status"] == "applied"
    backup(settings, tmp_path / "backup")
    restored = settings.model_copy(
        update={
            "data_dir": tmp_path / "restored",
            "database_url": f"sqlite:///{tmp_path / 'restored' / 'business.db'}",
        }
    )
    assert restore(restored, tmp_path / "backup")["business_send_frozen"]
    from fastapi.testclient import TestClient
    from quality.app import create_app

    recovered = TestClient(create_app(restored))
    recovered.cookies.update(clients["admin"].cookies)
    runtime = recovered.get("/api/industrial/business-runtime").json()
    recovery_request = {
        "node_epoch": runtime["node_epoch"],
        "source_handover_confirmed": True,
        "reason": "软件恢复验证：已核对独立源消费者交接和全部原键结果",
    }
    post(recovered, "/industrial/business-runtime/reconcile", recovery_request, expected=409)
    with Database(restored).session() as db:
        command = db.scalar(select(m.BusinessCommand).where(m.BusinessCommand.id == row["id"]))
        assert command.status == "unknown" and command.fencing > item["fencing"]
    assert run_once(restored, Database(restored))  # frozen writes still permit original-key reads
    with Database(restored).session() as db:
        assert db.get(m.BusinessCommand, row["id"]).status == "applied"
    node = json.dumps([restored.tenant_id, restored.site_id, restored.node_id])
    reference[1].post("/admin/control", json={"handover": {node: runtime["node_epoch"]}}).raise_for_status()
    post(recovered, "/industrial/business-runtime/reconcile", recovery_request)
    assert recovered.get("/api/industrial/business-runtime").json()["send_frozen"] is False
    clone = {
        **item["request"],
        "node_epoch": "a-different-restored-node-epoch",
        "idempotency_key": "business:another-safe-test-intent",
    }
    with httpx.Client(trust_env=False) as http:
        blocked = http.post(
            item["endpoint"] + "/commands", json=clone, headers={"Authorization": "Bearer " + reference[2]}
        )
    assert blocked.status_code == 409 and "epoch_fenced" in blocked.text


def test_purchase_cannot_hide_amount_from_policy_and_requires_independent_review(core, reference):
    clients, line, file, materials, stocks, _, order, _ = order_fixture(core)
    reference[0].authority = "source-erp"
    conn = connector(clients, line, reference, ["purchase.draft", "purchase.submit"])
    pol = policy(clients, line, file, conn["actions"], "kg", "D2")
    plan = post(
        clients["admin"],
        "/industrial/supply-plans",
        {"order_id": order["id"], "order_version": order["version"], "inventory_ids": [stocks[0]["id"]]},
    )
    body = {
        "line_id": line["id"],
        "connector_id": conn["id"],
        "policy_id": pol["id"],
        "evidence_ids": [file],
        "expected_result": "purchase_draft_created",
        "payload": {
            "action": "purchase.draft",
            "target_id": order["id"],
            "target_version": str(order["version"]),
            "material_id": materials[0]["id"],
            "quantity": "5",
            "unit": "kg",
            "reference_id": plan["id"],
            "unit_price": "3",
            "tax_rate": "0.13",
            "tax_basis": "tax_exclusive",
            "currency": "CNY",
            "reason": "软件验证有依据的材料缺口采购草稿",
        },
    }
    task_body = {
        "line_id": line["id"],
        "action": "purchase.draft",
        "object_id": order["id"],
        "object_version": str(order["version"]),
        "quantity": "5",
        "amount": "0",
        "currency": "CNY",
        "evidence_ids": [file],
        "payload": body,
    }
    post(clients["admin"], "/industrial/digital-tasks", task_body, expected=422)
    task_body["amount"] = "16.95"
    created = post(clients["admin"], "/industrial/digital-tasks", task_body)
    task = created["task"]
    assert task["status"] == "awaiting_approval"
    review = {
        "version": task["version"],
        "approved": True,
        "reason": "逐次核对采购订单、单价及13%税额",
        "expires_at": (current_time() + timedelta(hours=1)).isoformat(),
    }
    post(clients["admin"], f"/industrial/digital-tasks/{task['id']}/review", review, expected=409)
    # Finance role is created through the actual user management API, never by editing database rows.
    from fastapi.testclient import TestClient
    from .conftest import PASSWORD

    post(
        clients["admin"],
        "/users",
        {
            "username": "finance-test",
            "password": PASSWORD,
            "role": "finance",
            "allowed_sites": [core[0].site_id],
            "allowed_lines": [line["id"]],
        },
    )
    finance = TestClient(core[1])
    post(finance, "/auth/login", {"username": "finance-test", "password": PASSWORD})
    assert post(finance, f"/industrial/digital-tasks/{task['id']}/review", review)["status"] == "queued"
    assert digital_once(core[0], Database(core[0]))
    command = clients["admin"].get("/api/records/industrial-business-commands").json()["items"][0]
    assert command["status"] == "queued"
    core[0].business_send_enabled = True
    assert run_once(core[0], Database(core[0]))
    assert get(clients, command["id"])["status"] == "applied"
    state = reference[1].post("/admin/state", json={}).json()
    assert next(iter(state["purchases"].values()))["status"] == "draft"
    assert state["applied_count"] == 1

    submitted_payload = {
        **body,
        "expected_result": "purchase_submitted",
        "payload": {**body["payload"], "action": "purchase.submit", "reference_id": command["id"]},
    }
    submitted_task = post(
        clients["admin"],
        "/industrial/digital-tasks",
        {**task_body, "action": "purchase.submit", "payload": submitted_payload},
    )["task"]
    post(
        finance,
        f"/industrial/digital-tasks/{submitted_task['id']}/review",
        {**review, "version": submitted_task["version"]},
    )
    assert digital_once(core[0], Database(core[0]))
    assert run_once(core[0], Database(core[0]))
    state = reference[1].post("/admin/state", json={}).json()
    assert next(iter(state["purchases"].values()))["status"] == "submitted"
    assert state["applied_count"] == 2


def test_retest_work_order_has_actual_source_entity_and_no_fabricated_test_result(core, reference):
    _, _, clients, lines = core
    line = lines[0]
    from .test_industrial_quality import original

    file = original(clients, "admin")
    case = post(
        clients["admin"],
        "/cases",
        {
            "line_id": line["id"],
            "process": line["data"]["process"],
            "title": "隔离外部复测工单验证",
            "defect": "颗粒",
            "evidence_ids": [file],
        },
    )
    conn = connector(clients, line, reference, ["retest.create"])
    pol = policy(clients, line, file, conn["actions"], "operation")
    body = {
        "line_id": line["id"],
        "connector_id": conn["id"],
        "policy_id": pol["id"],
        "evidence_ids": [file],
        "expected_result": "work_order_created",
        "payload": {
            "action": "retest.create",
            "target_id": case["id"],
            "target_version": str(case["version"]),
            "quantity": "1",
            "unit": "operation",
            "reason": "明确案件版本的外部工单意图",
        },
    }
    row = authorize(core, body)
    core[0].business_send_enabled = True
    assert run_once(core[0], Database(core[0]))
    assert get(clients, row["id"])["status"] == "applied"
    workorder = next(iter(reference[1].post("/admin/state", json={}).json()["work_orders"].values()))
    assert workorder["case_reference"] == case["id"] and workorder["actual_test_result"] is None
    assert clients["admin"].get("/api/cases/" + case["id"]).json()["status"] == case["status"]


def test_partial_source_hold_is_not_full_confirmation_and_blocks_availability(core, reference):
    clients, _, _, _, stock, hold, body = inventory_case(core, reference)
    row = authorize(core, body)
    reference[1].post("/admin/control", json={"fault": "partial"}).raise_for_status()
    assert run_once(core[0], Database(core[0]))
    assert get(clients, row["id"])["status"] == "partial"
    source = reference[1].post("/admin/state", json={}).json()
    assert Decimal(next(iter(source["inventory"].values()))["reserved"]) == Decimal("2.5")
    post(
        clients["admin"],
        "/industrial/reservations",
        {"inventory_id": stock["id"], "quantity": "1"},
        expected=409,
    )

    current = clients["admin"].get("/api/industrial/reservations/" + hold["id"]).json()
    post(
        clients["admin"],
        "/industrial/reservations/" + hold["id"] + "/control",
        {"version": current["version"], "action": "cancel", "reason": "未知源效果不能本地抹除"},
        expected=409,
    )


def test_signed_callback_wins_race_with_lost_http_response(core, reference, monkeypatch):
    clients, _, _, _, _, _, body = inventory_case(core, reference)
    row = authorize(core, body)
    store, http, secret = reference
    original_handle = store.handle
    callback_status = []

    def handle(path, body, authorization):
        result = original_handle(path, body, authorization)
        if path == "/commands" and result[2]:
            callback_status.append(
                clients["admin"]
                .post(
                    "/api/industrial/business-callbacks/" + row["id"],
                    json=result[1],
                    headers={"Authorization": "Bearer " + secret},
                )
                .status_code
            )
        return result

    monkeypatch.setattr(store, "handle", handle)
    http.post("/admin/control", json={"fault": "response_lost"}).raise_for_status()
    assert run_once(core[0], Database(core[0]))
    assert callback_status == [200]
    assert get(clients, row["id"])["status"] == "applied"
    assert http.post("/admin/state", json={}).json()["applied_count"] == 1


def test_applied_purchase_draft_does_not_authorize_submission_after_stock_changes(core, reference):
    from fastapi.testclient import TestClient
    from .conftest import PASSWORD
    from .test_industrial_revalidation import new_stock, supply_plan

    clients, line, file, materials, stocks, _, order, _ = order_fixture(core)
    reference[0].authority = 'source-erp'
    core[0].business_send_enabled = True
    conn = connector(clients, line, reference, ['purchase.draft', 'purchase.submit'])
    pol = policy(clients, line, file, conn['actions'], 'kg', 'D2')
    post(clients['admin'], '/users', {
        'username': 'finance-stock-recheck', 'password': PASSWORD, 'role': 'finance',
        'allowed_sites': [core[0].site_id], 'allowed_lines': [line['id']],
    })
    reviewer = TestClient(core[1])
    post(reviewer, '/auth/login', {'username': 'finance-stock-recheck', 'password': PASSWORD})
    plan = supply_plan(clients['admin'], order, stocks[:1])
    body = {
        'line_id': line['id'], 'connector_id': conn['id'], 'policy_id': pol['id'],
        'evidence_ids': [file], 'expected_result': 'purchase_draft_created',
        'payload': {'action': 'purchase.draft', 'target_id': order['id'], 'target_version': str(order['version']),
                    'material_id': materials[0]['id'], 'quantity': '5', 'unit': 'kg', 'reference_id': plan['id'],
                    'unit_price': '3', 'tax_rate': '0', 'tax_basis': 'tax_exclusive', 'currency': 'CNY',
                    'reason': '软件验证库存变化后的采购提交边界'},
    }
    def queue(payload):
        command = post(clients['admin'], '/industrial/business-commands', payload)
        task = post(clients['admin'], f"/industrial/business-commands/{command['id']}/control", {
            'version': command['version'], 'action': 'submit', 'reason': '核对当前缺口并提交独立审核',
        })['authorization']['task']
        post(reviewer, f"/industrial/digital-tasks/{task['id']}/review", {
            'version': task['version'], 'approved': True, 'reason': '独立核对当时有效的库存与缺口',
            'expires_at': (current_time() + timedelta(hours=1)).isoformat(),
        })
        assert digital_once(core[0], Database(core[0]))
        assert get(clients, command['id'])['status'] == 'queued'
        return command
    draft = queue(body)
    assert run_once(core[0], Database(core[0]))
    assert get(clients, draft['id'])['status'] == 'applied'
    submit_body = {**body, 'expected_result': 'purchase_submitted',
                   'payload': {**body['payload'], 'action': 'purchase.submit', 'reference_id': draft['id']}}
    submission = queue(submit_body)
    new_stock(clients['admin'], line, stocks[0], on_hand='150')
    assert not run_once(core[0], Database(core[0]))  # Claim blocks before any second source write.
    blocked = get(clients, submission['id'])
    assert blocked['status'] == 'blocked' and blocked['error_code'] == 'plan_inventory_stale'
    state = reference[1].post('/admin/state', json={}).json()
    assert state['applied_count'] == 1 and next(iter(state['purchases'].values()))['status'] == 'draft'
    assert get(clients, draft['id'])['status'] == 'applied'
    post(clients['admin'], '/industrial/business-commands', submit_body, expected=409)
