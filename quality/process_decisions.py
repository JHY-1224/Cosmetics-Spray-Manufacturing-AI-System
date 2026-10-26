"""Choose only approved process modes. A candidate never authorizes an actual passage or equipment action."""

from decimal import Decimal
from . import models as m
from .enterprise import effective, json_value, line_scope
from .quality_decisions import current_assessment
from .security import fail
from .service import serialize
from .templates import effective_steps


def sop_basis(svc, line, mode, context):
    """Freeze every applicable SOP, including next-step constraints outside the explicit references."""
    from .scopes import applicable
    from .security import aware, current_time

    context = {**context, "step_id": mode.payload["next_step_id"]}
    records = {}
    for id in mode.payload["sop_ids"]:
        sop = svc.get(m.Knowledge, id)
        svc.applicable_knowledge(sop, line.data["process"], context["product_id"], line.id, context)
        if sop.status != "published" or not sop.reviewer_id or sop.category != "sop":
            fail(409, "process_sop_inactive", "模式引用的SOP未独立发布")
        records[sop.id] = sop
    for sop in svc.db.scalars(svc.scoped(m.Knowledge).where(
        m.Knowledge.category == "sop", m.Knowledge.status == "published",
    )):
        if sop.reviewer_id and aware(sop.valid_until) > current_time() and applicable(
            sop.scope, svc.settings, line.data["process"], line.id, context,
        ):
            records[sop.id] = sop
    return [serialize(records[id]) for id in sorted(records)]


def select_mode(svc, data):
    line = line_scope(svc, data.line_id, "industrial_write")
    context = data.model_dump(mode="json")
    route = svc.get(m.Route, data.route_id)
    mode = svc.get(m.DecisionDefinition, data.mode_id)
    effective(svc, m.DecisionDefinition, mode.id, line_id=data.line_id,
              context={**context, "step_id": mode.payload.get("next_step_id", "")}, kind="process_mode")
    if (
        route.status != "published"
        or not route.reviewer_id
        or route.process != line.data.get("process")
        or line.data.get("route_id") != route.id
        or mode.payload["route_id"] != route.id
        or mode.payload["from_step_id"] != data.from_step_id
    ):
        fail(409, "process_mode_binding", "模式、工艺、已批准路线及当前实际产线绑定必须一致")
    reasons = []
    steps = effective_steps(route.steps)
    ids = [r["id"] for r in steps]
    spec = mode.payload
    next_id = spec["next_step_id"]
    if data.from_step_id not in ids or next_id not in ids:
        fail(422, "process_mode_steps", "模式工序不是本路线的有效工序实例")
    position = ids.index(data.from_step_id)
    ordinary = ids[position + 1 : position + 2]
    branches = steps[position].get("allowed_next_step_ids", ordinary)
    rework = route.disposition.get("approved") and next_id == route.disposition.get("return_step_id")
    if next_id not in branches and not rework:
        reasons.append("下一工序不在批准分支或返工路径内")
    if (
        data.cycle > spec.get("max_cycles", 1)
        or rework
        and data.cycle > route.disposition.get("max_cycles", 0)
    ):
        reasons.append("超过模式或路线批准的循环次数")
    assessments = [current_assessment(svc, id) for id in data.assessment_ids]
    if any(r.scope_line_id != data.line_id or r.object_id != data.object_id for r in assessments):
        fail(422, "process_quality_identity", "前置质量结果须明确属于同一实际对象与产线")
    for id in spec.get("required_standard_ids", []):
        effective(
            svc, m.DecisionDefinition, id, line_id=data.line_id, context=context, kind="quality_standard"
        )
        candidates = [r for r in assessments if r.definition_id == id]
        if len(candidates) != 1 or candidates[0].outcome != "pass":
            reasons.append("缺少该对象当前有效且通过的前置质量标准：" + id)
    if len(set(data.material_ids)) != len(data.material_ids):
        fail(422, "process_material_duplicate", "材料引用不能重复")
    materials = [svc.get(m.MaterialItem, id) for id in data.material_ids]
    if any(r.scope_line_id not in {"", data.line_id} for r in materials):
        fail(422, "process_material_scope", "材料不属于当前产线")
    if set(spec.get("required_material_ids", [])) - set(data.material_ids):
        reasons.append("未明确提供所有模式所需材料")
    actual = set(data.material_ids)
    if any(set(r.quality_requirements.get("prohibited_material_ids", [])) & actual for r in materials):
        reasons.append("实际所选材料存在禁配组合")
    qualifications = []
    for id in spec.get("equipment_qualification_ids", []):
        if id not in data.qualification_ids:
            reasons.append("缺少模式要求的设备资质：" + id)
            continue
        qualification = effective(
            svc, m.DecisionDefinition, id, line_id=data.line_id, context=context, kind="qualification"
        )
        qualifications.append(serialize(qualification))
        if qualification.payload["equipment_id"] != data.equipment_id:
            reasons.append("设备身份与批准资质不匹配")
    sops = sop_basis(svc, line, mode, context)
    windows = {}
    for sop in sops:
        for name, window in sop["rules"].get("parameters", {}).items():
            windows.setdefault(name, []).append(window)
    for name in sorted(set(windows) - set(spec.get("parameters", {}))):
        reasons.append("模式未明确提供适用SOP要求的参数：" + name)
    for name, value in spec.get("parameters", {}).items():
        rules = windows.get(name, [])
        unit = spec.get("parameter_units", {}).get(name)
        if not rules or not unit or any(w.get("unit") != unit for w in rules):
            reasons.append("参数缺少批准共同单位或窗口：" + name)
            continue
        low = max(Decimal(str(w["min"])) for w in rules)
        high = min(Decimal(str(w["max"])) for w in rules)
        if low > high or not low <= Decimal(str(value)) <= high:
            reasons.append("参数不满足所有适用SOP共同窗口：" + name)
    row = svc.add(
        m.ProcessDecision,
        object_id=data.object_id,
        route_id=route.id,
        mode_id=mode.id,
        from_step_id=data.from_step_id,
        next_step_id=next_id,
        status="blocked" if reasons else "candidate",
        author_id=svc.actor.id,
        snapshot=json_value(
            {
                "input": context,
                "route": serialize(route),
                "mode": serialize(mode),
                "quality_assessments": [serialize(r) for r in assessments],
                "materials": [serialize(r) for r in materials],
                "qualifications": qualifications,
                "sops": sops,
                "all_applicable_sop_windows": windows,
                "authorization": "not_granted",
                "actual_passage": "not_recorded",
                "equipment_control": "prohibited",
            }
        ),
        reasons=reasons,
        scope_line_id=data.line_id,
    )
    svc.audit("process.mode_assessed", row.id, {"status": row.status, "actual_equipment_action": False})
    return serialize(row)
