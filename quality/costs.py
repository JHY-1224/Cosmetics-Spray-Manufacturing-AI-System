"""Append-only source documents and explicit reconciliation; no payments or implicit write-offs."""

from collections import defaultdict
from decimal import Decimal, localcontext, ROUND_HALF_UP
from sqlalchemy import select, func
from . import models as m
from .enterprise import evidence, json_value, line_scope, lock
from .material_inventory import quantity
from .security import current_time, fail
from .service import serialize

PRECISION = {"CNY": 2, "USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}


def document(svc, data):
    line_scope(svc, data.line_id, "finance")
    evidence(svc, data.evidence_ids, data.line_id)
    order = svc.get(m.BusinessOrder, data.order_id)
    if order.scope_line_id != data.line_id or data.occurred_at > current_time():
        fail(422, "document_scope_time", "单据须对应同产线订单及实际发生时点")
    if data.currency not in PRECISION:
        fail(422, "currency_precision", "币种尚未纳入明确精度合同，不能猜测或自动换汇")
    if len({r.external_line for r in data.lines}) != len(data.lines):
        fail(422, "document_duplicate_line", "源单据行标识不能重复")
    for part in data.lines:
        line = svc.get(m.BusinessOrderLine, part.order_line_id)
        material = svc.get(m.MaterialItem, part.material_id)
        if line.order_id != order.id or material.scope_line_id not in {"", data.line_id}:
            fail(422, "document_line_identity", "单据行须属于当前订单和材料适用范围")
        quantity(part.quantity, part.unit, material.unit, material)
        if data.kind == "return":
            if not part.reference_line_id:
                fail(422, "return_reference", "退料必须逐行明确引用原收料行")
            lock(svc, ["return", part.reference_line_id])
            original = svc.get(m.BusinessDocumentLine, part.reference_line_id)
            header = svc.get(m.BusinessDocument, original.document_id)
            if (
                header.kind,
                header.order_id,
                header.currency,
                header.tax_basis,
                header.tax_rate,
                original.material_id,
                original.lot,
                original.order_line_id,
                original.unit,
                original.unit_price,
            ) != (
                "receipt",
                order.id,
                data.currency,
                data.tax_basis,
                data.tax_rate,
                part.material_id,
                part.lot,
                part.order_line_id,
                part.unit,
                part.unit_price,
            ):
                fail(422, "return_identity", "退料须匹配原收料的材料、批次、数量单位、价格及税币种口径")
            returned = svc.db.scalar(
                select(func.coalesce(func.sum(m.BusinessDocumentLine.quantity), 0)).where(
                    m.BusinessDocumentLine.id.in_(
                        svc.scoped(m.BusinessDocumentLine).with_only_columns(m.BusinessDocumentLine.id)
                    ),
                    m.BusinessDocumentLine.reference_line_id == original.id,
                )
            ) or Decimal(0)
            same_request = sum(
                (r.quantity for r in data.lines if r.reference_line_id == original.id), Decimal(0)
            )
            if returned + same_request > original.quantity:
                fail(422, "return_excess", "累计退料数量不能超过原收料数量")
        elif part.reference_line_id:
            fail(422, "document_reference", "只有退料行在当前合同中引用原收料行")
    row = svc.add(
        m.BusinessDocument,
        scope_line_id=data.line_id,
        author_id=svc.actor.id,
        **data.model_dump(exclude={"line_id", "lines"}),
    )
    lines = [
        svc.add(m.BusinessDocumentLine, document_id=row.id, scope_line_id=data.line_id, **part.model_dump())
        for part in data.lines
    ]
    svc.audit("cost.document_recorded", row.id, {"kind": data.kind, "source": data.source_system})
    return {**serialize(row), "lines": [serialize(r) for r in lines]}


def reconcile(svc, data):
    order = svc.get(m.BusinessOrder, data.order_id)
    line_scope(svc, order.scope_line_id, "finance")
    evidence(svc, data.evidence_ids, order.scope_line_id)
    if order.currency not in PRECISION:
        fail(422, "currency_precision", "订单币种缺少明确精度合同")
    if (data.labor_hours is None) != (data.hourly_cost is None):
        fail(422, "labor_basis", "工时与小时成本必须同时提供")
    if data.labor_hours is not None and len(data.labor_basis.strip()) < 5:
        fail(422, "labor_basis", "实际工时计价须提供事实与分摊口径依据")
    docs = svc.db.scalars(
        svc.scoped(m.BusinessDocument)
        .where(m.BusinessDocument.order_id == order.id)
        .order_by(m.BusinessDocument.occurred_at, m.BusinessDocument.id)
    ).all()
    headers = {r.id: r for r in docs}
    lines = (
        svc.db.scalars(
            svc.scoped(m.BusinessDocumentLine).where(m.BusinessDocumentLine.document_id.in_(headers))
        ).all()
        if headers
        else []
    )
    order_lines = svc.db.scalars(
        svc.scoped(m.BusinessOrderLine).where(m.BusinessOrderLine.order_id == order.id)
    ).all()
    usages = svc.db.scalars(
        svc.scoped(m.ActualMaterialUsage).where(
            m.ActualMaterialUsage.order_line_id.in_([r.id for r in order_lines])
        ).order_by(m.ActualMaterialUsage.occurred_at, m.ActualMaterialUsage.id)
    ).all()
    groups = defaultdict(
        lambda: {
            "receipt_quantity": Decimal(0),
            "receipt_value": Decimal(0),
            "invoice_quantity": Decimal(0),
            "invoice_value": Decimal(0),
            "receipt_lines": [],
            "invoice_lines": [],
        }
    )
    differences, matches = [], []
    usable = []
    quantum = Decimal(1).scaleb(-PRECISION[order.currency])
    with localcontext() as ctx:
        ctx.prec = 128
        for part in lines:
            header = headers[part.document_id]
            if header.currency != order.currency or header.tax_basis != order.tax_basis:
                differences.append(
                    {
                        "key": "basis:" + part.id,
                        "kind": "currency_or_tax_basis",
                        "document_line_id": part.id,
                        "expected": [order.currency, order.tax_basis],
                        "actual": [header.currency, header.tax_basis],
                        "state": "open",
                    }
                )
                continue
            material = svc.get(m.MaterialItem, part.material_id)
            q = quantity(part.quantity, part.unit, material.unit, material)
            # Reconciliation values retain the original tax basis. Cost valuation is tax exclusive.
            value = part.quantity * part.unit_price
            exclusive = value / (1 + header.tax_rate) if header.tax_basis == "tax_inclusive" else value
            group = groups[(part.order_line_id, material.id, part.lot, str(header.tax_rate))]
            sign = Decimal(-1) if header.kind == "return" else Decimal(1)
            category = "invoice" if header.kind == "invoice" else "receipt"
            group[category + "_quantity"] += q * sign
            group[category + "_value"] += value * sign
            group[category + "_lines"].append(part.id)
            usable.append(
                {
                    "line": part,
                    "header": header,
                    "material": material,
                    "quantity": q,
                    "exclusive_value": exclusive * sign,
                }
            )
        for key, group in groups.items():
            qdiff = group["invoice_quantity"] - group["receipt_quantity"]
            vdiff = group["invoice_value"] - group["receipt_value"]
            matches.append({
                "key": "match:" + ":".join(key), "identity": key, **json_value(group),
                "quantity_difference": str(qdiff), "amount_difference": str(vdiff),
                "state": "difference" if qdiff or vdiff else "matched",
            })
            if qdiff or vdiff:
                name = "match:" + ":".join(key)
                differences.append(
                    {
                        "key": name,
                        "kind": "quantity_or_amount",
                        "identity": key,
                        **json_value(group),
                        "quantity_difference": str(qdiff),
                        "amount_difference": str(vdiff),
                        "state": "open",
                    }
                )
        prices, price_sources = {}, defaultdict(list)
        for item in usable:
            if item["header"].kind == "invoice":
                continue
            key = (item["line"].order_line_id, item["material"].id, item["line"].lot)
            q, v = prices.get(key, (Decimal(0), Decimal(0)))
            sign = Decimal(-1) if item["header"].kind == "return" else Decimal(1)
            prices[key] = (q + item["quantity"] * sign, v + item["exclusive_value"])
            price_sources[key].append(item["line"].id)
        valued, unvalued = [], []
        material_total = Decimal(0)
        cumulative = defaultdict(Decimal)
        for use in usages:
            stock = svc.get(m.InventorySnapshot, use.inventory_id)
            material = svc.get(m.MaterialItem, use.material_id)
            key = (use.order_line_id, material.id, stock.lot)
            q = quantity(use.quantity, use.unit, material.unit, material)
            available_q, available_v = prices.get(key, (Decimal(0), Decimal(0)))
            cumulative[key] += q
            if available_q <= 0 or cumulative[key] > available_q:
                unvalued.append(
                    {
                        "usage_id": use.id,
                        "reason": "缺少足量且税币种口径一致的净收料价格依据",
                        "quantity": str(q),
                        "unit": material.unit,
                        "price_basis_document_line_ids": price_sources[key],
                    }
                )
                continue
            unit_price = available_v / available_q
            value = q * unit_price
            material_total += value
            valued.append(
                {
                    "usage_id": use.id,
                    "purpose": use.purpose,
                    "quantity": str(q),
                    "unit": material.unit,
                    "unit_price_tax_exclusive": str(unit_price),
                    "unrounded_cost": str(value),
                    "basis": "order_line_material_lot_weighted_net_receipts",
                    "price_basis_document_line_ids": price_sources[key],
                }
            )
        labor = data.labor_hours * data.hourly_cost if data.labor_hours is not None else None
        actual = (
            (material_total + (labor or Decimal(0))).quantize(quantum, rounding=ROUND_HALF_UP)
            if usages and not unvalued
            else None
        )
        estimated = None
        if data.estimated_unit_cost is not None:
            if len({r.unit for r in order_lines}) != 1:
                fail(422, "estimate_unit", "不同产出单位不能共用一个估算单价")
            estimated = (
                sum((r.quantity for r in order_lines), Decimal(0)) * data.estimated_unit_cost
            ).quantize(quantum, rounding=ROUND_HALF_UP)
    known = {d["key"] for d in differences}
    if set(data.difference_resolutions) - known:
        fail(422, "difference_identity", "差异说明必须引用本次实际差异键")
    for diff in differences:
        note = data.difference_resolutions.get(diff["key"], "")
        if note and len(note.strip()) < 5:
            fail(422, "difference_reason", "差异说明需明确依据")
        diff["resolution_note"] = note
        # A note explains the difference. It never changes invoice/receipt facts or writes it off.
        diff["state"] = "explained_still_open" if note else "open"
    row = svc.add(
        m.CostReport,
        order_id=order.id,
        author_id=svc.actor.id,
        currency=order.currency,
        allocation_version=data.allocation_version,
        actual_amount=actual,
        estimated_amount=estimated,
        scope_line_id=order.scope_line_id,
        status="differences_open" if differences or unvalued else "calculated" if actual is not None else "incomplete_cost",
        snapshot={
            "input": data.model_dump(mode="json"),
            "order": serialize(order),
            "order_lines": [serialize(r) for r in order_lines],
            "documents": [serialize(r) for r in docs],
            "document_lines": [serialize(r) for r in lines],
            "actual_usage": [serialize(r) for r in usages],
            "valued_usage": valued,
            "unvalued_usage": unvalued,
            "partial_material_cost_unrounded": str(material_total),
            "labor_amount": str(labor) if labor is not None else None,
            "labor_status": "reported_with_evidence" if labor is not None else "not_provided",
            "cost_scope": "recorded_material_usage_and_reported_labor_only",
            "rounding": "final_half_up",
            "currency_places": PRECISION[order.currency],
        },
        reconciliation={
            "differences": differences,
            "matches": json_value(matches),
            "match_grain": "order_line_material_lot_tax_rate; original source lines retained; no invented invoice allocation",
            "invoice_matched": not differences and bool(lines),
            "tax_basis": order.tax_basis,
            "tax_exclusive_actual_cost": True,
            "payment": "not_performed",
        },
    )
    svc.audit(
        "cost.reconciled",
        row.id,
        {"differences": len(differences), "unvalued_usage": len(unvalued), "payments": False},
    )
    return serialize(row)
