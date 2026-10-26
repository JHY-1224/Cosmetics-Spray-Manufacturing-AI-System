"""Risk regressions for current source facts, dependency changes and incomplete business evidence."""

from datetime import timedelta
from decimal import Decimal
import pytest
from quality.enterprise_schemas import InventoryInput
from quality.security import current_time
from .conftest import post
from .test_industrial_supply_cost import order_fixture


def new_stock(client, line, stock, **patch):
    body = {key: stock[key] for key in InventoryInput.model_fields if key in stock}
    body.update(line_id=line["id"], source_version="source-new-version", observed_at=current_time().isoformat())
    return post(client, "/industrial/inventory", {**body, **patch})


def supply_plan(client, order, stocks):
    return post(client, "/industrial/supply-plans", {
        "order_id": order["id"], "order_version": order["version"],
        "inventory_ids": [r["id"] for r in stocks],
    })


def validation(client, plan):
    response = client.get("/api/industrial/supply-plans/" + plan["id"])
    assert response.status_code == 200, response.text
    return response.json()["current_validation"]


@pytest.mark.parametrize("change", ["new_version", "additional_lot", "competing_hold", "retired_route"])
def test_supply_plan_reuse_blocks_changed_inputs_without_rewriting_history(core, change):
    clients, line, _, _, stocks, bom, order, _ = order_fixture(core)
    admin = clients["admin"]
    plan = supply_plan(admin, order, stocks[:1])
    assert validation(admin, plan)["current"]
    if change == "new_version":
        new_stock(admin, line, stocks[0], on_hand="150")
    elif change == "additional_lot":
        new_stock(admin, line, stocks[0], lot="additional-authoritative-lot", on_hand="50")
    elif change == "competing_hold":
        post(admin, "/industrial/reservations", {"inventory_id": stocks[0]["id"], "quantity": "1"})
    else:
        post(clients["approver"], f"/routes/{bom['route_id']}/review", {"action": "retire", "reason": "独立停用原计划路线依据"})
    check = validation(admin, plan)
    assert not check["current"]
    assert check["code"] in {"plan_inventory_stale", "bom_route"}
    post(admin, "/industrial/reservations", {"inventory_id": stocks[0]["id"], "quantity": "1", "plan_id": plan["id"]}, expected=409)
    history = admin.get("/api/industrial/supply-plans/" + plan["id"]).json()
    assert history["snapshot"] == plan["snapshot"]
    assert sum(Decimal(r["shortage"]) for r in history["demands"]) == 25


def test_plan_reservations_preserve_own_allocations_but_prevent_excess_and_unplanned_material(core):
    clients, _, _, _, stocks, _, order, body = order_fixture(core)
    admin = clients["admin"]
    small = post(admin, f"/industrial/orders/{order['id']}/revision", {
        **body, "version": order["version"], "source_version": "small-order-v2",
        "lines": [{**part, "quantity": "1"} for part in body["lines"]],
    })
    plan = supply_plan(admin, small, stocks)
    assert sum(Decimal(r["required_quantity"]) for r in plan["demands"]) == 5
    first = post(admin, "/industrial/reservations", {"inventory_id": stocks[0]["id"], "quantity": "4", "plan_id": plan["id"]})
    assert validation(admin, plan)["current"]  # Its own proposal must not invalidate itself.
    denied_replan = post(admin, "/industrial/supply-plans", {
        "order_id": small["id"], "order_version": small["version"], "inventory_ids": [stocks[0]["id"]],
    }, expected=409)
    assert denied_replan["detail"]["code"] == "order_allocation_pending"
    denied = post(admin, "/industrial/reservations", {"inventory_id": stocks[0]["id"], "quantity": "2", "plan_id": plan["id"]}, expected=409)
    assert denied["detail"]["code"] == "reservation_plan_quantity"
    post(admin, "/industrial/reservations", {"inventory_id": stocks[1]["id"], "quantity": "1", "plan_id": plan["id"]}, expected=409)
    post(admin, f"/industrial/reservations/{first['id']}/control", {"version": first["version"], "action": "cancel", "reason": "取消未发送的内部建议后重新核对"})
    assert validation(admin, plan)["current"]
    post(admin, "/industrial/reservations", {"inventory_id": stocks[0]["id"], "quantity": "5", "plan_id": plan["id"]})


def test_historical_actual_usage_cannot_borrow_future_stock_availability(core):
    clients, line, file, materials, stocks, _, order, _ = order_fixture(core)
    body = {
        "line_id": line["id"], "material_id": materials[0]["id"], "inventory_id": stocks[0]["id"],
        "order_line_id": order["lines"][0]["id"], "source_id": "prior-use-before-source-snapshot",
        "quantity": "1", "unit": "kg", "purpose": "rework", "evidence_ids": [file],
        "occurred_at": (current_time() - timedelta(hours=1)).isoformat(),
    }
    denied = post(clients["admin"], "/industrial/actual-usage", body, expected=422)
    assert denied["detail"]["code"] == "usage_deviation"
    recorded = post(clients["admin"], "/industrial/actual-usage", {**body, "deviation_reason": "事后登记原件；当时库存状态待源系统核对"})
    assert recorded["purpose"] == "rework" and recorded["deviation_reason"]


def test_cost_without_usage_stays_incomplete_and_matching_preserves_original_line_basis(core):
    clients, line, file, materials, stocks, _, order, _ = order_fixture(core)
    admin = clients["admin"]
    report_body = {"order_id": order["id"], "allocation_version": "risk-recheck-v1", "evidence_ids": [file]}
    empty = post(admin, "/industrial/cost-reports", report_body)
    assert empty["actual_amount"] is None and empty["status"] == "incomplete_cost"
    doc_body = {
        "line_id": line["id"], "source_system": "synthetic-source", "order_id": order["id"],
        "currency": "CNY", "tax_basis": "tax_exclusive", "tax_rate": "0.13",
        "occurred_at": current_time().isoformat(), "evidence_ids": [file],
        "lines": [{"external_line": "1", "order_line_id": order["lines"][0]["id"],
                   "material_id": materials[0]["id"], "lot": stocks[0]["lot"], "quantity": "10", "unit": "kg", "unit_price": "5"}],
    }
    receipt = post(admin, "/industrial/business-documents", {**doc_body, "kind": "receipt", "external_id": "receipt-matching"})
    invoice = post(admin, "/industrial/business-documents", {**doc_body, "kind": "invoice", "external_id": "invoice-matching"})
    use = post(admin, "/industrial/actual-usage", {
        "line_id": line["id"], "material_id": materials[0]["id"], "inventory_id": stocks[0]["id"],
        "order_line_id": order["lines"][0]["id"], "source_id": "rework-valued",
        "quantity": "2000", "unit": "g", "purpose": "rework", "evidence_ids": [file], "occurred_at": current_time().isoformat(),
    })
    report = post(admin, "/industrial/cost-reports", report_body)
    assert Decimal(report["actual_amount"]) == 10
    assert report["reconciliation"]["differences"] == []
    match = report["reconciliation"]["matches"][0]
    assert match["state"] == "matched"
    assert match["receipt_lines"] == [receipt["lines"][0]["id"]]
    assert match["invoice_lines"] == [invoice["lines"][0]["id"]]
    valued = report["snapshot"]["valued_usage"][0]
    assert valued["usage_id"] == use["id"] and valued["purpose"] == "rework"
    assert valued["unit"] == "kg" and Decimal(valued["quantity"]) == 2
    assert valued["price_basis_document_line_ids"] == match["receipt_lines"]
    assert len(report["snapshot"]["order_lines"]) == 2
    assert report["reconciliation"]["payment"] == "not_performed"
