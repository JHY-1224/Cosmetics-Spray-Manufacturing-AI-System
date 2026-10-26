"""Source inventory snapshots, internal proposed holds and actual source occupancy stay separate."""

from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import and_, or_
from . import models as m
from .enterprise import evidence, line_scope, lock
from .security import aware, current_time, fail
from .service import serialize

FACTORS = {
    "kg": Decimal(1),
    "g": Decimal("0.001"),
    "L": Decimal(1),
    "mL": Decimal("0.001"),
    "piece": Decimal(1),
}


def quantity(value, source_unit, target_unit, material):
    if source_unit not in FACTORS or target_unit not in FACTORS:
        fail(422, "material_unit", "单位未纳入批准数量合同")
    if (source_unit == "piece") != (target_unit == "piece"):
        fail(422, "material_piece_conversion", "件数与质量/体积不能推断换算")
    result = Decimal(value) * FACTORS[source_unit]
    source_mass = source_unit in {"kg", "g"}
    target_mass = target_unit in {"kg", "g"}
    if source_mass != target_mass:
        if not material.density_kg_l or not material.quality_requirements.get("density_basis"):
            fail(422, "material_density_missing", "质量与体积换算缺少材料密度及批准依据")
        result = result / material.density_kg_l if source_mass else result * material.density_kg_l
    return result / FACTORS[target_unit]


def material(svc, data):
    if data.line_id:
        line_scope(svc, data.line_id, "industrial_write")
    else:
        svc.actor.require("industrial_write", svc.settings.site_id)
        if svc.actor.lines:
            fail(403, "material_shared_scope", "产线受限账号不能维护工厂共享物料")
    if data.density_kg_l is not None and len(data.density_basis.strip()) < 3:
        fail(422, "material_density_basis", "密度必须有可核对的资料和适用条件")
    for id in data.prohibited_material_ids:
        svc.get(m.MaterialItem, id)
    row = svc.add(
        m.MaterialItem,
        code=data.code,
        name=data.name,
        unit=data.unit,
        density_kg_l=data.density_kg_l,
        quality_requirements={
            "density_basis": data.density_basis,
            "prohibited_material_ids": data.prohibited_material_ids,
        },
        scope_line_id=data.line_id,
    )
    svc.audit("material.created", row.id)
    return serialize(row)


def inventory(svc, data):
    line_scope(svc, data.line_id, "supply")
    evidence(svc, data.evidence_ids, data.line_id)
    item = svc.get(m.MaterialItem, data.material_id)
    if item.scope_line_id not in {"", data.line_id}:
        fail(422, "inventory_material_scope", "物料不属于当前产线或工厂共享目录")
    if item.unit != data.unit:
        fail(422, "inventory_unit", "库存单位须与明确物料主单位一致，导入换算另留依据")
    lock(svc, ["inventory", data.line_id, item.id, data.warehouse, data.lot])
    if len(set(data.included_usage_ids)) != len(data.included_usage_ids):
        fail(422, "inventory_included_usage", "源库存实际耗用纳入标识不能重复")
    for id in data.included_usage_ids:
        usage = svc.get(m.ActualMaterialUsage, id)
        source = svc.get(m.InventorySnapshot, usage.inventory_id)
        if (
            usage.material_id != item.id
            or usage.scope_line_id != data.line_id
            or (source.warehouse, source.lot, source.source_system) != (data.warehouse, data.lot, data.source_system)
            or aware(data.observed_at) < aware(usage.occurred_at)
        ):
            fail(422, "inventory_included_usage", "实际耗用纳入标记须绑定同材料、产线、源系统和批次，并有发生之后的源库存依据")
    for id in data.included_command_ids:
        from .enterprise_schemas import BusinessCommandInput

        command = svc.get(m.BusinessCommand, id)
        payload = BusinessCommandInput.model_validate(command.input).payload
        source = command.snapshot["prepared"]["source_identity"]
        if (
            command.status != "applied"
            or not command.action.startswith("inventory.")
            or command.scope_line_id != data.line_id
            or payload.material_id != item.id
            or source["warehouse"] != data.warehouse
            or source["lot"] != data.lot
            or source["system"] != data.source_system
            or aware(data.observed_at)
            < aware(__import__("datetime").datetime.fromisoformat(command.result["generated_at"]))
        ):
            fail(
                422, "inventory_included_command", "源库存纳入标记须绑定已确认同批次操作及其之后的真实源快照"
            )
    if data.quality_assessment_id:
        assessment = svc.get(m.QualityAssessment, data.quality_assessment_id)
        if assessment.scope_line_id != data.line_id or assessment.object_id != data.lot:
            fail(422, "inventory_quality_scope", "质量判定必须明确对应本库存批次")
        if data.quality_status == "released" and assessment.outcome != "pass":
            fail(422, "inventory_quality_conflict", "不通过或无效检测不能登记为检验释放库存")
    row = svc.add(m.InventorySnapshot, **data.model_dump(exclude={"line_id", "included_usage_ids"}), scope_line_id=data.line_id)
    svc.audit(
        "inventory.source_snapshot",
        row.id,
        {
            "authority": data.source_system,
            "source_version": data.source_version,
            "not_internal_stock_adjustment": True,
            "included_usage_ids": data.included_usage_ids,
        },
    )
    return {**serialize(row), "included_usage_ids": data.included_usage_ids}


def reconciled_usage_ids(svc, row):
    proof = svc.db.scalar(svc.scoped(m.Audit).where(m.Audit.target_id == row.id, m.Audit.action == "inventory.source_snapshot").order_by(m.Audit.created_at.desc()).limit(1))
    return proof.detail.get("included_usage_ids", []) if proof else []


def available(svc, row, at=None, include_proposals=True, exclude_plan_id=None):
    now = at or current_time()
    reasons = []
    if row.quality_status != "released":
        reasons.append("库存源质量状态未释放")
    if aware(row.expires_at) <= now:
        reasons.append("材料批次过期")
    if (now - aware(row.observed_at)).total_seconds() > row.fresh_seconds:
        reasons.append("库存快照过期")
    if aware(row.observed_at) > now:
        reasons.append("源库存观察晚于本次实际用量时点，不能作为当时可用依据")
    newer = svc.db.scalar(
        svc.scoped(m.InventorySnapshot).where(
            m.InventorySnapshot.material_id == row.material_id,
            m.InventorySnapshot.warehouse == row.warehouse,
            m.InventorySnapshot.lot == row.lot,
            m.InventorySnapshot.source_system == row.source_system,
            or_(
                m.InventorySnapshot.observed_at > row.observed_at,
                and_(
                    m.InventorySnapshot.observed_at == row.observed_at,
                    m.InventorySnapshot.created_at > row.created_at,
                ),
            ),
        )
    )
    if newer:
        reasons.append("已有更新源库存版本")
    if row.quality_assessment_id:
        from .quality_decisions import current_assessment

        try:
            if current_assessment(svc, row.quality_assessment_id).outcome != "pass":
                reasons.append("关联质量判定未通过")
        except HTTPException:
            reasons.append("关联判定或检测标准已更正/失效")
    pending_usage = svc.db.scalars(
        svc.scoped(m.ActualMaterialUsage)
        .join(m.InventorySnapshot, m.ActualMaterialUsage.inventory_id == m.InventorySnapshot.id)
        .where(
            m.InventorySnapshot.material_id == row.material_id,
            m.InventorySnapshot.warehouse == row.warehouse,
            m.InventorySnapshot.lot == row.lot,
            m.InventorySnapshot.source_system == row.source_system,
            m.ActualMaterialUsage.occurred_at <= now,
            m.ActualMaterialUsage.id.not_in(reconciled_usage_ids(svc, row)),
        ).order_by(m.ActualMaterialUsage.created_at, m.ActualMaterialUsage.id).limit(51)
    ).all()
    if pending_usage:
        reasons.append("已登记实际用量尚未获本源快照明确纳入，须先对账再承诺可用量")
    # Source quantity already includes held/reserved when declared net; never deduct them twice.
    base = (
        row.reported_available
        if row.quantity_basis == "net_available"
        else row.on_hand - row.held - row.reserved
    )
    reservations = svc.db.scalars(
        svc.scoped(m.InventoryReservation)
        .join(m.InventorySnapshot, m.InventoryReservation.inventory_id == m.InventorySnapshot.id)
        .where(
            m.InventorySnapshot.material_id == row.material_id,
            m.InventorySnapshot.warehouse == row.warehouse,
            m.InventorySnapshot.lot == row.lot,
            m.InventoryReservation.status.in_(["proposed", "confirmed", "unknown"]),
        )
    ).all()
    pending = Decimal(0)
    for reservation in reservations:
        if reservation.command_id and svc.get(m.BusinessCommand, reservation.command_id).error_code:
            reasons.append("关联源预占回执存在待核对错误或冲突")
        if reservation.status == "unknown":
            reasons.append("外部预占数量或效果未知，须先对账再计算可用量")
        elif reservation.status == "proposed" and include_proposals and (
            not exclude_plan_id or reservation.plan_id != exclude_plan_id
        ):
            pending += reservation.quantity
        elif reservation.status == "confirmed" and reservation.command_id not in row.included_command_ids:
            reasons.append("已确认预占尚未纳入本源快照，先核对库存版本而不重复扣减")
    return {
        "available": max(Decimal(0), base - pending) if not reasons else None,
        "reported_basis": row.quantity_basis,
        "source_available": base,
        "internal_proposed": pending,
        "reasons": list(dict.fromkeys(reasons)),
        "in_transit_not_counted_as_on_hand": row.in_transit,
        "source_version": row.source_version,
        "unreconciled_usage_ids": [r.id for r in pending_usage[:50]],
        "unreconciled_usage_truncated": len(pending_usage) > 50,
    }


def reserve(svc, data):
    svc.actor.require("supply")
    row = svc.get(m.InventorySnapshot, data.inventory_id)
    line_scope(svc, row.scope_line_id, "supply")
    if data.plan_id:
        plan = svc.get(m.SupplyPlan, data.plan_id)
        lock(svc, ["supply-order", plan.order_id])
        lock(svc, ["plan-reservation", data.plan_id, row.material_id])
    lock(svc, ["inventory", row.scope_line_id, row.material_id, row.warehouse, row.lot])
    state = available(svc, row)
    if state["available"] is None or data.quantity > state["available"]:
        fail(409, "inventory_insufficient", "可用库存不足或需先核对源状态")
    if data.plan_id:
        plan = svc.get(m.SupplyPlan, data.plan_id)
        if plan.scope_line_id != row.scope_line_id or plan.status != "calculated":
            fail(409, "reservation_plan", "需求计划已失效或不适用")
        from .supply import current_plan

        current_plan(svc, plan)
        if row.id not in {stock["id"] for stock in plan.snapshot["inventory"]}:
            fail(409, "reservation_plan_inventory", "预占须使用本计划已核对的源库存，新增库存先重新核算")
        demands = svc.db.scalars(svc.scoped(m.MaterialDemand).where(
            m.MaterialDemand.plan_id == plan.id, m.MaterialDemand.material_id == row.material_id,
        )).all()
        limit = sum((part.available_quantity for part in demands), Decimal(0))
        holds = svc.db.scalars(svc.scoped(m.InventoryReservation)
            .join(m.InventorySnapshot, m.InventoryReservation.inventory_id == m.InventorySnapshot.id)
            .where(m.InventoryReservation.plan_id == plan.id,
                   m.InventorySnapshot.material_id == row.material_id,
                   m.InventoryReservation.status.in_(["proposed", "confirmed", "unknown"]))).all()
        if data.quantity + sum((hold.quantity for hold in holds), Decimal(0)) > limit:
            fail(409, "reservation_plan_quantity", "同计划材料累计预占不能超过本次明确分配的可用需求量")
    hold = svc.add(
        m.InventoryReservation,
        plan_id=data.plan_id,
        inventory_id=row.id,
        quantity=data.quantity,
        status="proposed",
        scope_line_id=row.scope_line_id,
    )
    svc.audit("inventory.reservation_proposed", hold.id, {"source_applied": False})
    return serialize(hold)


def cancel_reservation(svc, id, data):
    row = svc.get(m.InventoryReservation, id, True)
    line_scope(svc, row.scope_line_id, "supply")
    if row.version != data.version or data.action != "cancel" or row.status != "proposed":
        fail(409, "reservation_cancel_state", "只能按当前版本取消尚未被源确认的内部建议")
    pending = svc.db.scalars(
        svc.scoped(m.BusinessCommand).where(
            m.BusinessCommand.action == "inventory.reserve",
            m.BusinessCommand.status.in_(
                {"awaiting_task", "queued", "sending", "accepted", "unknown", "partial", "applied"}
            ),
        )
    ).all()
    if any(r.input.get("payload", {}).get("reference_id") == id for r in pending):
        fail(409, "reservation_command_pending", "预占存在已授权或在途命令，先办理原命令，不能本地抹掉源效果")
    row.status = "cancelled"
    row.version += 1
    svc.audit("inventory.proposal_cancelled", row.id, {"reason": data.reason, "source_adjustment": False})
    return serialize(row)
