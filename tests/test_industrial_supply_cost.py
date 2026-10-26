"""Order/BOM/inventory/cost API flows retain shortage, quantities, source documents and differences."""

from datetime import timedelta
from decimal import Decimal
from quality.security import current_time
from .conftest import post
from .test_delivery_integrity import published_route
from .test_industrial_formulations import setup_formula


def order_fixture(core):
    clients, line, file, materials, stocks, _ = setup_formula(core)
    route = published_route(clients, line)
    bom = post(
        clients["engineer"],
        "/industrial/boms",
        {
            "code": "BOM-1",
            "name": "软件验证喷涂材料BOM",
            "scope": {"line_id": line["id"], "process": line["data"]["process"]},
            "valid_until": (current_time() + timedelta(days=10)).isoformat(),
            "evidence_ids": [file],
            "product_id": "P-TEST",
            "product_version": "v1",
            "output_quantity": "1",
            "output_unit": "piece",
            "route_id": route["id"],
            "components": [
                {
                    "material_id": materials[0]["id"],
                    "quantity": "2",
                    "unit": "kg",
                    "loss_fraction": "0.2",
                    "conversion_factor": "1",
                    "conversion_basis": "已批准每件净量及20%得率损耗",
                }
            ],
        },
    )
    bom = post(
        clients["engineer"],
        f"/industrial/governance/bom/{bom['id']}",
        {"version": bom["version"], "action": "submit", "reason": "提交完整BOM计算依据"},
    )
    bom = post(
        clients["approver"],
        f"/industrial/governance/bom/{bom['id']}",
        {"version": bom["version"], "action": "publish", "reason": "独立核对损耗与计量口径"},
    )
    body = {
        "line_id": line["id"],
        "external_id": "ORDER-TEST",
        "customer_reference": "软件夹具",
        "source_system": "source-erp",
        "source_version": "1",
        "due_at": (current_time() + timedelta(days=7)).isoformat(),
        "currency": "CNY",
        "tax_basis": "tax_exclusive",
        "lines": [
            {
                "external_line": str(i),
                "product_id": "P-TEST",
                "product_version": "v1",
                "quantity": str(q),
                "unit": "piece",
                "bom_id": bom["id"],
            }
            for i, q in [(1, 30), (2, 20)]
        ],
    }
    order = post(clients["admin"], "/industrial/orders", body)
    return clients, line, file, materials, stocks, bom, order, body


def test_bom_loss_pool_allocation_delivery_unknown_and_order_revision_stale(core):
    clients, line, file, materials, stocks, bom, order, body = order_fixture(core)
    # Independent BOM review does not grant access to orders, supply plans or finance.
    reviewer = clients["approver"]
    assert reviewer.get(f"/api/industrial/boms/{bom['id']}").status_code == 200
    assert reviewer.get("/api/records/industrial-boms").json()["total"] == 1
    for resource in ("orders", "supply-plans", "cost-reports"):
        assert reviewer.get(f"/api/records/industrial-{resource}").status_code == 403
    plan = post(
        clients["admin"],
        "/industrial/supply-plans",
        {"order_id": order["id"], "order_version": order["version"], "inventory_ids": [stocks[0]["id"]]},
    )
    assert [Decimal(d["required_quantity"]) for d in plan["demands"]] == [Decimal(75), Decimal(50)]
    assert sum(Decimal(d["available_quantity"]) for d in plan["demands"]) == 100
    assert sum(Decimal(d["shortage"]) for d in plan["demands"]) == 25
    assert plan["delivery_assessment"]["conclusion"] == "unknown"
    assert "capacity_per_day" in plan["delivery_assessment"]["missing_facts"]
    post(
        clients["admin"],
        "/industrial/supply-plans",
        {
            "order_id": order["id"],
            "order_version": order["version"],
            "inventory_ids": [stocks[0]["id"], stocks[0]["id"]],
        },
        expected=422,
    )
    hold = post(
        clients["admin"],
        "/industrial/reservations",
        {"inventory_id": stocks[0]["id"], "plan_id": plan["id"], "quantity": "5"},
    )
    assert hold["status"] == "proposed" and not hold["command_id"]
    cancelled = post(
        clients["admin"],
        f"/industrial/reservations/{hold['id']}/control",
        {"version": hold["version"], "action": "cancel", "reason": "取消尚未发送的内部预占建议"},
    )
    assert cancelled["status"] == "cancelled"
    revised = post(
        clients["admin"],
        f"/industrial/orders/{order['id']}/revision",
        {**body, "version": order["version"], "source_version": "2"},
    )
    assert revised["version"] == 2
    assert clients["admin"].get("/api/industrial/supply-plans/" + plan["id"]).json()["status"] == "stale"
    post(
        clients["admin"],
        "/industrial/reservations",
        {"inventory_id": stocks[0]["id"], "plan_id": plan["id"], "quantity": "5"},
        expected=409,
    )
    # Restricted readers use the same filters before server totals and pages.
    assert clients["admin"].get("/api/records/industrial-orders?line_id=" + line["id"]).json()["total"] == 1


def test_partial_returns_invoices_actual_usage_cost_and_open_differences(core):
    clients, line, file, materials, stocks, bom, order, _ = order_fixture(core)
    order_line = order["lines"][0]
    base = {
        "line_id": line["id"],
        "source_system": "source-erp",
        "order_id": order["id"],
        "currency": "CNY",
        "tax_basis": "tax_exclusive",
        "tax_rate": "0.13",
        "occurred_at": current_time().isoformat(),
        "evidence_ids": [file],
    }
    item = {
        "external_line": "1",
        "order_line_id": order_line["id"],
        "material_id": materials[0]["id"],
        "lot": stocks[0]["lot"],
        "quantity": "20",
        "unit": "kg",
        "unit_price": "3",
    }
    receipt_body = {**base, "kind": "receipt", "external_id": "RCPT-1", "lines": [item]}
    receipt = post(clients["admin"], "/industrial/business-documents", receipt_body)
    post(clients["admin"], "/industrial/business-documents", receipt_body, expected=409)
    ret = {
        **base,
        "kind": "return",
        "external_id": "RET-1",
        "lines": [{**item, "quantity": "2", "reference_line_id": receipt["lines"][0]["id"]}],
    }
    post(clients["admin"], "/industrial/business-documents", ret)
    post(
        clients["admin"],
        "/industrial/business-documents",
        {**ret, "external_id": "RET-EXCESS", "lines": [{**ret["lines"][0], "quantity": "19"}]},
        expected=422,
    )
    post(
        clients["admin"],
        "/industrial/business-documents",
        {**base, "kind": "invoice", "external_id": "INV-1", "lines": [{**item, "quantity": "10"}]},
    )
    for i, (purpose, amount) in enumerate([("production", "5"), ("rework", "2"), ("scrap", "1")]):
        post(
            clients["admin"],
            "/industrial/actual-usage",
            {
                "line_id": line["id"],
                "material_id": materials[0]["id"],
                "inventory_id": stocks[0]["id"],
                "order_line_id": order_line["id"],
                "source_id": "usage-" + str(i),
                "deviation_reason": "实际用量事实留档；源库存尚未纳入，继续保持待对账",
                "quantity": amount,
                "unit": "kg",
                "purpose": purpose,
                "occurred_at": current_time().isoformat(),
                "evidence_ids": [file],
            },
        )
    cost_body = {
        "order_id": order["id"],
        "allocation_version": "actual-lot-v1",
        "evidence_ids": [file],
        "labor_hours": "2",
        "hourly_cost": "10",
        "labor_basis": "现场工时单2小时，仅归集此订单行",
        "estimated_unit_cost": "4",
    }
    cost = post(clients["admin"], "/industrial/cost-reports", cost_body)
    assert Decimal(cost["actual_amount"]) == 44
    assert Decimal(cost["estimated_amount"]) == 200
    assert cost["status"] == "differences_open"
    diff = cost["reconciliation"]["differences"][0]
    assert Decimal(diff["quantity_difference"]) == -8
    assert Decimal(diff["amount_difference"]) == -24
    explained = post(
        clients["admin"],
        "/industrial/cost-reports",
        {**cost_body, "difference_resolutions": {diff["key"]: "已通知核对剩余8公斤对应发票；仍保留差异"}},
    )
    assert explained["status"] == "differences_open"
    assert explained["reconciliation"]["differences"][0]["state"] == "explained_still_open"
    assert explained["reconciliation"]["payment"] == "not_performed"
    assert clients["engineer"].get("/api/industrial/cost-reports/" + cost["id"]).status_code == 403


def test_actual_usage_requires_explicit_source_reconciliation_without_double_deduction(core):
    from quality.enterprise_schemas import InventoryInput

    clients, line, file, materials, stocks, _, order, _ = order_fixture(core)
    stock = stocks[0]
    usage = post(clients['admin'], '/industrial/actual-usage', {
        'line_id': line['id'], 'material_id': materials[0]['id'], 'inventory_id': stock['id'],
        'order_line_id': order['lines'][0]['id'], 'source_id': 'actual-consumption-unreconciled',
        'quantity': '5', 'unit': 'kg', 'purpose': 'production',
        'occurred_at': current_time().isoformat(), 'evidence_ids': [file],
    })
    def state(id):
        return clients['admin'].get('/api/industrial/inventory/' + id).json()

    assert state(stock['id'])['availability']['available'] is None
    post(clients['admin'], '/industrial/reservations', {'inventory_id': stock['id'], 'quantity': '1'}, expected=409)
    body = {key: stock[key] for key in InventoryInput.model_fields if key in stock}
    body.update(line_id=line['id'], source_version='2', on_hand='95', observed_at=current_time().isoformat())
    unconfirmed = post(clients['admin'], '/industrial/inventory', body)
    assert state(unconfirmed['id'])['availability']['available'] is None  # A newer timestamp is not an acknowledgement.
    post(clients['admin'], '/industrial/inventory', {**body, 'source_version': 'wrong-lot', 'lot': 'other-lot', 'included_usage_ids': [usage['id']]}, expected=422)
    post(clients['admin'], '/industrial/inventory', {**body, 'source_version': 'duplicate-proof', 'included_usage_ids': [usage['id'], usage['id']]}, expected=422)
    reconciled = post(clients['admin'], '/industrial/inventory', {**body, 'source_version': '3', 'included_usage_ids': [usage['id']]})
    detail = state(reconciled['id'])
    assert detail['included_usage_ids'] == [usage['id']]
    assert Decimal(detail['availability']['available']) == Decimal(95)  # Do not deduct the already included 5kg again.
    assert Decimal(state(stock['id'])['on_hand']) == Decimal(100)  # Original source snapshot remains intact.
    held = post(clients['admin'], '/industrial/reservations', {'inventory_id': reconciled['id'], 'quantity': '1'})
    assert held['status'] == 'proposed'
