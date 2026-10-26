"""Approved material formulas use exact Decimal arithmetic; a calculation is never a real addition."""

from datetime import timedelta
from decimal import Decimal, localcontext, ROUND_HALF_UP
from . import models as m
from .enterprise import effective, evidence, governed_values, json_value, line_scope, detail, lock
from .material_inventory import available, quantity
from .security import current_time, digest, fail
from .service import serialize


def components(svc, row):
    return svc.db.scalars(
        svc.scoped(m.FormulationComponent)
        .where(m.FormulationComponent.formulation_id == row.id)
        .order_by(m.FormulationComponent.id)
    ).all()


def validate_formulation(svc, row):
    parts = components(svc, row)
    if not parts or not any(c.role == "main" for c in parts):
        fail(422, "formulation_components", "批准配方须含主剂及明确组分")
    ids = {c.material_id for c in parts}
    for c in parts:
        item = svc.get(m.MaterialItem, c.material_id)
        if item.scope_line_id not in {"", row.scope_line_id}:
            fail(422, "formulation_scope", "材料不适用于本产线")
        if row.ratio_basis == "volume" and not item.density_kg_l:
            fail(422, "material_density_missing", "体积份换算称量质量须有每个材料密度")
        for id in c.approved_substitute_ids:
            sub = svc.get(m.MaterialItem, id)
            if sub.scope_line_id not in {"", row.scope_line_id}:
                fail(422, "formulation_substitute_scope", "替代料不适用于本产线")
        prohibited = set(item.quality_requirements.get("prohibited_material_ids", []))
        if prohibited & ids:
            fail(422, "formulation_prohibited_pair", "材料主数据存在禁配组合")
    if any(a in ids and b in ids for a, b in row.prohibited_pairs):
        fail(422, "formulation_prohibited_pair", "批准配方存在明确禁配组合")


def create(svc, data):
    values = governed_values(svc, data, m.MaterialFormulation, "industrial_write")
    row = svc.add(
        m.MaterialFormulation,
        **values,
        **data.model_dump(
            exclude=set(values)
            | {"scope", "components", "previous_id", "evidence_ids", "valid_from", "valid_until"}
        ),
    )
    for c in data.components:
        svc.get(m.MaterialItem, c.material_id)
        svc.add(
            m.FormulationComponent, formulation_id=row.id, **c.model_dump(), scope_line_id=row.scope_line_id
        )
    validate_formulation(svc, row)
    svc.audit(
        "formulation.created", row.id, {"kind": "material_mixture", "separate_from_process_recipe": True}
    )
    return detail(svc, "formulation", row)


def calculate(svc, data):
    line = line_scope(svc, data.line_id, "industrial_write")
    context = data.model_dump(mode="json")
    row = effective(svc, m.MaterialFormulation, data.formulation_id, line_id=data.line_id, context=context)
    if row.version != data.formulation_version or row.scope["process"] != line.data.get("process"):
        fail(409, "formulation_version", "批准配方版本已变或与产线工艺不符")
    validate_formulation(svc, row)
    parts = components(svc, row)
    items = {}
    if set(data.substitutions) - {c.material_id for c in parts}:
        fail(422, "formulation_substitute", "只可替代本配方已有组分")
    for c in parts:
        id = data.substitutions.get(c.material_id, c.material_id)
        if id != c.material_id and id not in c.approved_substitute_ids:
            fail(422, "formulation_unapproved_substitute", "替代料不在本配方已批准清单内")
        items[c.id] = svc.get(m.MaterialItem, id)
    actual_ids = {item.id for item in items.values()}
    if len(actual_ids) != len(parts):
        fail(422, "formulation_duplicate_substitute", "替代后材料不能重复计量")
    for item in items.values():
        if set(item.quality_requirements.get("prohibited_material_ids", [])) & actual_ids:
            fail(422, "formulation_substitute_prohibited", "替代后存在禁配组合")
    if any(a in actual_ids and b in actual_ids for a, b in row.prohibited_pairs):
        fail(422, "formulation_substitute_prohibited", "替代后存在配方禁配组合")
    with localcontext() as decimal_context:
        decimal_context.prec = 128
        weights = {c.id: c.parts for c in parts}
        dilution_formula = "各组分按批准份数"
        if row.dilution_basis != "parts":
            diluents = [c for c in parts if c.role == "diluent"]
            if len(diluents) != 1:
                fail(422, "formulation_dilution", "百分比稀释须有唯一稀释剂")
            main = sum((weights[c.id] for c in parts if c.role == "main"), Decimal(0))
            hardener = sum((weights[c.id] for c in parts if c.role == "hardener"), Decimal(0))
            non_diluent = sum((weights[c.id] for c in parts if c.role != "diluent"), Decimal(0))
            basis = {
                "main": main,
                "main_hardener": main + hardener,
                "final_total": non_diluent / (1 - row.dilution_fraction),
            }[row.dilution_basis]
            weights[diluents[0].id] = basis * row.dilution_fraction
            dilution_formula = (
                f"{row.dilution_basis}基准 × {row.dilution_fraction}（份数性质={row.ratio_basis}）"
            )
        kg_weights = {}
        for c in parts:
            item = items[c.id]
            kg_weights[c.id] = (
                weights[c.id] if row.ratio_basis == "mass" else quantity(weights[c.id], "L", "kg", item)
            )
        relevant = [c for c in parts if data.basis == "total" or c.role == "main"]
        divisor = sum(
            (
                kg_weights[c.id]
                if data.unit in {"kg", "g"}
                else quantity(kg_weights[c.id], "kg", "L", items[c.id])
                for c in relevant
            ),
            Decimal(0),
        )
        if divisor <= 0:
            fail(422, "formulation_zero_basis", "核算基准数量不能为零")
        target = data.quantity * (Decimal("0.001") if data.unit in {"g", "mL"} else Decimal(1))
        scale = target / divisor
        stocks = [svc.get(m.InventorySnapshot, id) for id in data.inventory_ids]
        identities = [(r.material_id, r.warehouse, r.lot) for r in stocks]
        if len(set(identities)) != len(identities):
            fail(422, "calculation_duplicate_stock", "同一物料仓库批次只能选择一个权威源版本，不能重复计量")
        if any(stock.scope_line_id != data.line_id for stock in stocks):
            fail(403, "calculation_inventory_scope", "库存须属本次产线")
        results = []
        blocked = []
        for c in parts:
            item = items[c.id]
            theoretical = kg_weights[c.id] * scale
            planned = (theoretical / row.resolution_kg).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            ) * row.resolution_kg
            available_kg = Decimal(0)
            stock_states = []
            for stock in stocks:
                if stock.material_id != item.id:
                    continue
                state = available(svc, stock)
                stock_states.append({"id": stock.id, **json_value(state)})
                if state["available"] is not None:
                    available_kg += quantity(state["available"], stock.unit, "kg", item)
            if planned > 0 and (not stocks or available_kg < planned):
                blocked.append(item.code + "：缺少足量且当前有效的释放库存")
            if theoretical > 0 and planned == 0:
                blocked.append(item.code + "：称量分辨率将正值舍入为零，需重新批准核算条件")
            results.append(
                {
                    "component_id": c.id,
                    "approved_material_id": c.material_id,
                    "material_id": item.id,
                    "material": item.name,
                    "role": c.role,
                    "unit": "kg",
                    "ratio_basis": row.ratio_basis,
                    "effective_parts": str(weights[c.id]),
                    "kg_weight": str(kg_weights[c.id]),
                    "scale": str(scale),
                    "theoretical": str(theoretical),
                    "planned": str(planned),
                    "rounding_delta": str(planned - theoretical),
                    "resolution_kg": str(row.resolution_kg),
                    "tolerance_fraction": str(row.tolerance_fraction),
                    "inventory_available_kg": str(available_kg) if stocks else None,
                    "inventory": stock_states,
                    "density_kg_l": str(item.density_kg_l) if item.density_kg_l else None,
                    "density_basis": item.quality_requirements.get("density_basis"),
                    "formula": "effective_parts × density_if_volume × scale → resolution rounding",
                }
            )
        result = {
            "components": results,
            "dilution_formula": dilution_formula,
            "target": str(data.quantity),
            "target_unit": data.unit,
            "basis": data.basis,
            "theoretical_total_kg": str(sum((Decimal(r["theoretical"]) for r in results), Decimal(0))),
            "planned_total_kg": str(sum((Decimal(r["planned"]) for r in results), Decimal(0))),
            "inventory_feasible": not blocked,
            "blocking_reasons": blocked,
            "physical_addition": "not_performed",
            "authorization_scope": row.scope,
            "field_check_required": True,
            "reserved_stock": False,
        }
    record = svc.add(
        m.FormulationCalculation,
        formulation_id=row.id,
        formulation_version=row.version,
        author_id=svc.actor.id,
        basis=data.basis,
        target_quantity=data.quantity,
        target_unit=data.unit,
        status="ready" if not blocked else "blocked_inventory",
        snapshot=json_value(
            {
                "definition": detail(svc, "formulation", row),
                "input": context,
                "materials": [serialize(i) for i in items.values()],
                "inventory": [serialize(i) for i in stocks],
                "hash": digest([row.id, row.version, context]),
            }
        ),
        result=result,
        scope_line_id=data.line_id,
    )
    svc.audit(
        "formulation.calculated", record.id, {"physical_addition": False, "inventory_feasible": not blocked}
    )
    return serialize(record)


def mix(svc, data):
    line_scope(svc, data.line_id, "execute")
    if svc.db.scalar(svc.scoped(m.MixingBatch).where(m.MixingBatch.external_id == data.external_id)):
        fail(409, "mixing_duplicate", "此实际混配批次已登记，请核对原回执，不得重复登记耗用")
    if not data.actual_confirmation or data.mixed_at > current_time():
        fail(422, "mixing_actual_confirmation", "须明确实际发生并提供逐项称量，不能以核算完成代替实际加料")
    calculation = svc.get(m.FormulationCalculation, data.calculation_id)
    if calculation.scope_line_id != data.line_id:
        fail(403, "mixing_scope", "核算不属于本次产线")
    formulation = effective(
        svc,
        m.MaterialFormulation,
        calculation.formulation_id,
        line_id=data.line_id,
        context=calculation.snapshot["input"],
    )
    if formulation.version != calculation.formulation_version:
        fail(409, "mixing_stale_calculation", "批准配方已变化，请重新核算后登记对应实际执行依据")
    expected = {r["material_id"]: r for r in calculation.result["components"] if Decimal(r["planned"]) > 0}
    if len(data.weighings) != len(expected) or {w.material_id for w in data.weighings} != set(expected):
        fail(422, "mixing_incomplete", "实际称量须逐项完整对应批准核算材料，不得遗漏或重复")
    stocks = [svc.get(m.InventorySnapshot, weighing.inventory_id) for weighing in data.weighings]
    for key in sorted({(stock.scope_line_id, stock.material_id, stock.warehouse, stock.lot) for stock in stocks}):
        lock(svc, ["inventory", *key])
    checked = []
    deviations = []
    for weighing in data.weighings:
        item = svc.get(m.MaterialItem, weighing.material_id)
        stock = svc.get(m.InventorySnapshot, weighing.inventory_id)
        if stock.material_id != item.id or stock.scope_line_id != data.line_id:
            fail(422, "mixing_inventory", "实际批次、材料和产线不匹配")
        evidence(svc, weighing.evidence_ids, data.line_id)
        actual = quantity(weighing.quantity, weighing.unit, "kg", item)
        planned = Decimal(expected[item.id]["planned"])
        state = available(svc, stock, at=data.mixed_at, include_proposals=False)
        outside = abs(actual - planned) > planned * formulation.tolerance_fraction
        warnings = list(state["reasons"])
        if state["available"] is not None and quantity(state["available"], stock.unit, "kg", item) < actual:
            warnings.append("称量超出本快照可核对库存")
        if outside:
            warnings.append("实际称量偏差超过批准容差")
        if warnings and len(weighing.deviation_reason.strip()) < 3:
            fail(422, "mixing_deviation_reason", "实际偏差或材料异常必须留原因与证据")
        deviations.extend(warnings)
        checked.append((weighing, stock, actual, warnings))
    record = svc.add(
        m.MixingBatch,
        calculation_id=calculation.id,
        external_id=data.external_id,
        author_id=svc.actor.id,
        mixed_at=data.mixed_at,
        expires_at=data.mixed_at + timedelta(minutes=formulation.pot_life_minutes),
        status="deviation" if deviations else "recorded",
        snapshot={
            "calculation": serialize(calculation),
            "definition_version": formulation.version,
            "deviations": deviations,
            "actual_confirmation": True,
            "actual_weighings": data.model_dump(mode="json")["weighings"],
            "actual_conversions": [
                {
                    "material_id": weighing.material_id,
                    "exact_kg": str(actual),
                    "original_quantity": str(weighing.quantity),
                    "original_unit": weighing.unit,
                }
                for weighing, stock, actual, warnings in checked
            ],
        },
        scope_line_id=data.line_id,
    )
    if data.order_line_id:
        order_line = svc.get(m.BusinessOrderLine, data.order_line_id)
        if order_line.scope_line_id != data.line_id:
            fail(403, "mixing_order_scope", "订单行不属于当前产线")
    for weighing, stock, actual, warnings in checked:
        svc.add(
            m.ActualMaterialUsage,
            material_id=weighing.material_id,
            inventory_id=stock.id,
            mixing_batch_id=record.id,
            order_line_id=data.order_line_id,
            source_id=record.id + ":" + weighing.material_id,
            quantity=weighing.quantity,
            unit=weighing.unit,
            purpose="production",
            occurred_at=data.mixed_at,
            author_id=svc.actor.id,
            evidence_ids=weighing.evidence_ids,
            deviation_reason=weighing.deviation_reason,
            scope_line_id=data.line_id,
        )
    svc.audit(
        "material.actual_mixing_recorded",
        record.id,
        {"status": record.status, "source_stock_deducted": False},
    )
    return serialize(record)
