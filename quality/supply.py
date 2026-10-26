"""Version-bound BOM explosion and material plans; proposals never masquerade as source occupancy."""

from collections import defaultdict
from decimal import Decimal, localcontext, ROUND_CEILING
from . import models as m
from .enterprise import detail, effective, evidence, governed_values, json_value, line_scope, lock
from .material_inventory import available, quantity
from .security import aware, current_time, digest, fail
from .service import serialize


def bom_components(svc, row):
    return svc.db.scalars(
        svc.scoped(m.BomComponent).where(m.BomComponent.bom_id == row.id).order_by(m.BomComponent.id)
    ).all()


def validate_bom(svc, row, ancestors=(), budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if budget[0] > 1000:
        fail(422, "bom_expansion_budget", "BOM展开超过1000节点，须拆分为可核对计划")
    if row.id in ancestors or len(ancestors) >= 32:
        fail(422, "bom_cycle", "BOM存在循环或超过32层的不可核对层级")
    route = svc.get(m.Route, row.route_id)
    if route.status != "published" or not route.reviewer_id or route.process != row.scope["process"]:
        fail(409, "bom_route", "BOM必须绑定适用工艺的已批准路线版本")
    parts = bom_components(svc, row)
    if not parts:
        fail(422, "bom_components", "BOM必须有明确材料或子BOM用量")
    seen = set()
    for part in parts:
        budget[0] += 1
        if budget[0] > 1000:
            fail(422, "bom_expansion_budget", "BOM展开超过1000节点")
        key = part.material_id or part.child_bom_id
        if key in seen:
            fail(422, "bom_duplicate", "同层组分不能重复计入")
        seen.add(key)
        if part.child_bom_id:
            child = effective(svc, m.BillOfMaterials, part.child_bom_id, line_id=row.scope_line_id)
            if part.unit != child.output_unit:
                fail(422, "bom_child_unit", "子BOM用量单位须明确匹配其产出单位")
            validate_bom(svc, child, (*ancestors, row.id), budget)
        else:
            material = svc.get(m.MaterialItem, part.material_id)
            if material.scope_line_id not in {"", row.scope_line_id}:
                fail(422, "bom_material_scope", "BOM材料不适用于当前产线")
            quantity(part.quantity, part.unit, material.unit, material)


def create(svc, data):
    values = governed_values(svc, data, m.BillOfMaterials, "industrial_write")
    row = svc.add(
        m.BillOfMaterials,
        **values,
        **data.model_dump(
            include={"product_id", "product_version", "output_quantity", "output_unit", "route_id"}
        ),
    )
    for part in data.components:
        svc.add(m.BomComponent, bom_id=row.id, scope_line_id=row.scope_line_id, **part.model_dump())
    validate_bom(svc, row)
    svc.audit("supply.bom_created", row.id)
    return detail(svc, "bom", row)


def order_detail(svc, row):
    return {
        **serialize(row),
        "source_evidence": [
            r.detail
            for r in svc.db.scalars(
                svc.scoped(m.Audit)
                .where(m.Audit.target_id == row.id, m.Audit.action == "supply.order_source")
                .order_by(m.Audit.created_at)
            )
        ],
        "lines": [
            serialize(r)
            for r in svc.db.scalars(
                svc.scoped(m.BusinessOrderLine)
                .where(m.BusinessOrderLine.order_id == row.id)
                .order_by(m.BusinessOrderLine.external_line)
            )
        ],
    }


def validate_order_lines(svc, data):
    if len({r.external_line for r in data.lines}) != len(data.lines):
        fail(422, "order_line_duplicate", "订单外部行标识不能重复")
    for part in data.lines:
        bom = effective(
            svc,
            m.BillOfMaterials,
            part.bom_id,
            line_id=data.line_id,
            context={"product_id": part.product_id, "product_version": part.product_version},
        )
        validate_bom(svc, bom)
        if (bom.product_id, bom.product_version, bom.output_unit) != (
            part.product_id,
            part.product_version,
            part.unit,
        ):
            fail(422, "order_bom_identity", "订单产品、版本和产出单位须匹配指定BOM")


def order(svc, data, id=None):
    line_scope(svc, data.line_id, "supply")
    from .enterprise import evidence

    evidence(svc, data.evidence_ids, data.line_id)
    validate_order_lines(svc, data)
    if id:
        lock(svc, ["supply-order", id])
        row = svc.get(m.BusinessOrder, id, True)
        if row.scope_line_id != data.line_id or row.version != data.version:
            fail(409, "order_version", "订单范围或版本已变化")
        if (row.external_id, row.source_system) != (
            data.external_id,
            data.source_system,
        ) or row.source_version == data.source_version:
            fail(422, "order_revision", "修订须保持源订单身份，并提供新的源版本")
        old = {
            r.external_line: r
            for r in svc.db.scalars(
                svc.scoped(m.BusinessOrderLine).where(m.BusinessOrderLine.order_id == row.id)
            )
        }
        if set(old) != {r.external_line for r in data.lines}:
            fail(422, "order_revision_lines", "当前修订保留已关联行身份；拆单、删行须走源系统明确变更流程")
        before = order_detail(svc, row)
        before.pop("source_evidence", None)
        for key, val in data.model_dump(exclude={"lines", "line_id", "version", "evidence_ids"}).items():
            setattr(row, key, val)
        row.version += 1
        for part in data.lines:
            target = old[part.external_line]
            for key, val in part.model_dump().items():
                setattr(target, key, val)
            target.version += 1
        for plan in svc.db.scalars(svc.scoped(m.SupplyPlan).where(m.SupplyPlan.order_id == id)):
            plan.status = "stale"
            plan.version += 1
        svc.audit("supply.order_revised", row.id, {"before": before, "source_version": row.source_version})
    else:
        row = svc.add(
            m.BusinessOrder,
            scope_line_id=data.line_id,
            **data.model_dump(exclude={"line_id", "lines", "evidence_ids"}),
        )
        for part in data.lines:
            svc.add(m.BusinessOrderLine, order_id=row.id, scope_line_id=data.line_id, **part.model_dump())
        svc.audit("supply.order_recorded", row.id)
    svc.audit(
        "supply.order_source",
        row.id,
        {
            "order_version": row.version,
            "source_system": row.source_system,
            "source_version": row.source_version,
            "evidence_ids": data.evidence_ids,
        },
    )
    return order_detail(svc, row)


def explode(svc, bom, output, context, path=(), budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if budget[0] > 1000:
        fail(422, "bom_expansion_budget", "BOM用量展开超过1000节点")
    effective(svc, m.BillOfMaterials, bom.id, line_id=bom.scope_line_id, context=context)
    if not path:
        validate_bom(svc, bom)
    if bom.id in path:
        fail(422, "bom_cycle", "BOM循环")
    result = []
    scale = output / bom.output_quantity
    for part in bom_components(svc, bom):
        budget[0] += 1
        if budget[0] > 1000:
            fail(422, "bom_expansion_budget", "BOM用量展开超过1000节点")
        # Loss is yield loss, so gross input = net input/(1-loss), rather than 1+loss.
        gross = part.quantity * part.conversion_factor * scale / (1 - part.loss_fraction)
        step = {
            "bom_id": bom.id,
            "bom_version": bom.version,
            "component": serialize(part),
            "output": str(output),
            "scale": str(scale),
            "gross_input": str(gross),
            "formula": "component_quantity × approved_conversion × output/bom_output ÷ (1-loss)",
        }
        if part.child_bom_id:
            child = svc.get(m.BillOfMaterials, part.child_bom_id)
            for item in explode(svc, child, gross, context, (*path, bom.id), budget):
                item["path"].insert(0, step)
                result.append(item)
        else:
            material = svc.get(m.MaterialItem, part.material_id)
            result.append(
                {
                    "material": material,
                    "quantity": quantity(gross, part.unit, material.unit, material),
                    "path": [step],
                }
            )
    return result


def current_plan(svc, plan, *, write_lock=True):
    if write_lock:
        lock(svc, ["supply-order", plan.order_id])
    order = svc.get(m.BusinessOrder, plan.order_id)
    if write_lock:
        svc.db.refresh(order)
    if plan.status != "calculated" or order.version != plan.snapshot["order"]["version"]:
        fail(409, "plan_stale", "订单已变化或计划失效，请重新展开需求")
    for row in plan.snapshot["boms"]:
        bom = effective(svc, m.BillOfMaterials, row["id"], line_id=plan.scope_line_id)
        if bom.version != row["version"]:
            fail(409, "plan_bom_stale", "BOM版本或生效状态已变化")
        validate_bom(svc, bom)
    routes = plan.snapshot.get("routes")
    if not routes:
        fail(409, "plan_route_snapshot_missing", "历史计划缺少路线版本快照，请重新核算后办理")
    for previous in routes:
        route = svc.get(m.Route, previous["id"])
        if digest(serialize(route)) != digest(previous) or route.status != "published":
            fail(409, "plan_route_stale", "批准路线版本或状态已变化，请重新核算")
    for previous in plan.snapshot["inventory"]:
        stock = svc.get(m.InventorySnapshot, previous["id"])
        # A plan's own proposed holds consume its allocation, not a competing plan's stock.
        state = json_value(available(svc, stock, exclude_plan_id=plan.id))
        if state != plan.snapshot["inventory_states"][stock.id]:
            fail(409, "plan_inventory_stale", "库存、质量、耗用或其他预占已变化，请重新核对需求与缺口")
    material_ids = svc.scoped(m.MaterialDemand).where(m.MaterialDemand.plan_id == plan.id).with_only_columns(m.MaterialDemand.material_id)
    if svc.db.scalar(svc.scoped(m.InventorySnapshot).where(
        m.InventorySnapshot.scope_line_id == plan.scope_line_id,
        m.InventorySnapshot.material_id.in_(material_ids),
        m.InventorySnapshot.created_at > plan.created_at,
    ).limit(1)):
        fail(409, "plan_inventory_stale", "计划生成后有新的材料库存依据，请重新核算缺口")
    return order


def plan_validation(svc, row):
    from fastapi import HTTPException

    try:
        current_plan(svc, row, write_lock=False)
    except HTTPException as exc:
        if exc.status_code in {403, 404}:
            return {"current": False, "code": "plan_dependency_unavailable", "reason": "计划依据不可访问，请按当前授权重新核对"}
        return {"current": False, "code": exc.detail["code"], "reason": exc.detail["message"]}
    return {"current": True, "code": "", "reason": "已按当前订单、路线、BOM、库存与预占重新核对"}


def plan(svc, data):
    order = svc.get(m.BusinessOrder, data.order_id)
    line_scope(svc, order.scope_line_id, "supply")
    lock(svc, ["supply-order", order.id])
    svc.db.refresh(order)
    if order.version != data.order_version:
        fail(409, "order_version", "订单版本已变化")
    prior_plans = svc.scoped(m.SupplyPlan).where(m.SupplyPlan.order_id == order.id).with_only_columns(m.SupplyPlan.id)
    if svc.db.scalar(svc.scoped(m.InventoryReservation).where(
        m.InventoryReservation.plan_id.in_(prior_plans),
        m.InventoryReservation.status.in_(["proposed", "confirmed", "unknown"]),
    ).limit(1)):
        fail(409, "order_allocation_pending", "本订单旧计划仍有内部或源预占；先取消未发送建议或核对源释放，避免将已分配量再次作为采购缺口")
    stocks = [svc.get(m.InventorySnapshot, id) for id in data.inventory_ids]
    if len({r.id for r in stocks}) != len(stocks) or any(
        r.scope_line_id != order.scope_line_id for r in stocks
    ):
        fail(422, "plan_inventory_scope", "库存不得重复或跨产线引用")
    slots = [(r.warehouse, r.lot, r.material_id) for r in stocks]
    if len(set(slots)) != len(slots):
        fail(422, "plan_inventory_versions", "同一物料批次只能选一个源库存版本")
    stock_states = {r.id: available(svc, r) for r in stocks}
    pools = defaultdict(Decimal)
    inventory_unknown = []
    for r in stocks:
        state = stock_states[r.id]
        if state["available"] is None:
            inventory_unknown.append({"id": r.id, "reasons": state["reasons"]})
        else:
            pools[r.material_id] += state["available"]
    snapshot_order = order_detail(svc, order)
    demands, boms, routes = [], {}, {}
    with localcontext() as ctx:
        ctx.prec = 128
        for part in snapshot_order["lines"]:
            bom = svc.get(m.BillOfMaterials, part["bom_id"])
            context = {"product_id": part["product_id"], "product_version": part["product_version"]}
            totals = defaultdict(Decimal)
            paths = defaultdict(list)
            for item in explode(svc, bom, Decimal(part["quantity"]), context):
                totals[item["material"].id] += item["quantity"]
                paths[item["material"].id].append(item["path"])
                for step in item["path"]:
                    boms[step["bom_id"]] = {"id": step["bom_id"], "version": step["bom_version"]}
                    route = svc.get(m.Route, svc.get(m.BillOfMaterials, step["bom_id"]).route_id)
                    routes[route.id] = serialize(route)
            for material_id, required in totals.items():
                if abs(required) >= Decimal("10000000000000000000"):
                    fail(422, "demand_capacity", "展开用量超出明确数量精度，不保存截断需求")
                rounded = required.quantize(Decimal("0.000000001"), rounding=ROUND_CEILING)
                allocated = min(rounded, pools[material_id])
                pools[material_id] -= allocated
                material = svc.get(m.MaterialItem, material_id)
                demands.append(
                    {
                        "order_line_id": part["id"],
                        "material_id": material_id,
                        "required_quantity": rounded,
                        "available_quantity": allocated,
                        "shortage": rounded - allocated,
                        "unit": material.unit,
                        "calculation": json_value(
                            {
                                "paths": paths[material_id],
                                "unrounded": required,
                                "rounding": "ceil_to_1e-9",
                                "rounding_delta": rounded - required,
                                "source_stock": "latest released snapshots only",
                                "reserved": False,
                            }
                        ),
                    }
                )
    missing = [
        key
        for key in [
            "capacity_basis",
            "capacity_per_day",
            "calendar_available_days",
            "changeover_days",
            "arrival_at",
        ]
        if getattr(data, key) is None or getattr(data, key) == ""
    ]
    units = {p["unit"] for p in snapshot_order["lines"]}
    shortage = any(d["shortage"] > 0 for d in demands)
    delivery = {
        "conclusion": "unknown",
        "missing_facts": missing,
        "inventory_unknown": inventory_unknown,
        "material_shortage": shortage,
        "promise": False,
    }
    if not missing and len(units) == 1 and not inventory_unknown:
        output = sum((Decimal(p["quantity"]) for p in snapshot_order["lines"]), Decimal(0))
        days = output / data.capacity_per_day + data.changeover_days
        delivery.update(
            required_days=str(days),
            basis=data.capacity_basis,
            conclusion="risk"
            if shortage or days > data.calendar_available_days or data.arrival_at > aware(order.due_at)
            else "candidate_feasible",
            note="仅按已提供产能、日历、切换与到料依据计算，仍非源系统交期承诺",
        )
    row = svc.add(
        m.SupplyPlan,
        order_id=order.id,
        author_id=svc.actor.id,
        status="calculated",
        scope_line_id=order.scope_line_id,
        snapshot=json_value(
            {
                "input": data.model_dump(mode="json"),
                "order": snapshot_order,
                "boms": list(boms.values()),
                "routes": list(routes.values()),
                "inventory": [serialize(r) for r in stocks],
                "inventory_states": stock_states,
                "hash": digest([snapshot_order, list(boms.values()), data.model_dump(mode="json")]),
            }
        ),
        delivery_assessment=delivery,
    )
    for demand in demands:
        svc.add(m.MaterialDemand, plan_id=row.id, scope_line_id=row.scope_line_id, **demand)
    svc.audit(
        "supply.plan_calculated", row.id, {"physical_reservation": False, "delivery": delivery["conclusion"]}
    )
    return {**serialize(row), "demands": json_value(demands)}


def usage(svc, data):
    line_scope(svc, data.line_id, "supply")
    if svc.db.scalar(svc.scoped(m.ActualMaterialUsage).where(m.ActualMaterialUsage.source_id == data.source_id)):
        fail(409, "usage_duplicate", "此实际用量源记录已登记，请核对原始事实")
    evidence(svc, data.evidence_ids, data.line_id)
    stock = svc.get(m.InventorySnapshot, data.inventory_id)
    lock(svc, ["inventory", data.line_id, stock.material_id, stock.warehouse, stock.lot])
    order_line = svc.get(m.BusinessOrderLine, data.order_line_id)
    material = svc.get(m.MaterialItem, data.material_id)
    if stock.material_id != material.id or {stock.scope_line_id, order_line.scope_line_id} != {data.line_id}:
        fail(422, "usage_identity", "实际领料的库存、材料和订单行须属于同一产线")
    if data.occurred_at > current_time():
        fail(422, "usage_future", "实际发生不能来自未来")
    quantity(data.quantity, data.unit, material.unit, material)
    state = available(svc, stock, at=data.occurred_at)
    if (
        state["available"] is None
        or quantity(data.quantity, data.unit, stock.unit, material) > state["available"]
    ) and len(data.deviation_reason.strip()) < 3:
        fail(422, "usage_deviation", "异常实际用料须保留原因，不能当作有效库存计划")
    row = svc.add(
        m.ActualMaterialUsage,
        scope_line_id=data.line_id,
        author_id=svc.actor.id,
        **data.model_dump(exclude={"line_id"}),
    )
    svc.audit(
        "supply.actual_usage_recorded", row.id, {"purpose": data.purpose, "source_stock_deducted": False}
    )
    return serialize(row)
