"""Shared governance, scope and evidence checks for industrial business domains."""

from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import select, text, update
from . import models as m
from . import enterprise_schemas as s
from .security import aware, current_time, digest, fail
from .service import serialize
from .scopes import applicable

GOVERNED = {
    "definition": m.DecisionDefinition,
    "formulation": m.MaterialFormulation,
    "policy": m.AutomationPolicy,
    "bom": m.BillOfMaterials,
}


def json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return aware(value).isoformat()
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def lock(svc, key):
    """Transaction-local serialization before read/modify, including SQLite tests."""
    if svc.db.bind.dialect.name == "postgresql":
        value = int(digest([svc.settings.tenant_id, svc.settings.site_id, key])[:15], 16)
        svc.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": value})
    else:
        svc.db.execute(update(m.User).where(m.User.id == svc.actor.id).values(active=m.User.active))


def line_scope(svc, line_id, permission="read", physical_fact=True):
    svc.actor.require(permission, svc.settings.site_id, line_id)
    if physical_fact and svc.settings.node_role != "site":
        fail(409, "site_fact_authority", "中心不得直接登记或覆写厂内实际业务事实")
    line = svc.get(m.Master, line_id)
    if line.kind != "line" or line.site_id != svc.settings.site_id:
        fail(422, "industrial_line", "须选择本站授权实际产线")
    return line


def evidence(svc, ids, line_id):
    if len(ids) != len(set(ids)):
        fail(422, "industrial_evidence", "证据引用不得重复")
    for id in ids:
        row = svc.get(m.Evidence, id)
        if row.scope_line_id not in {line_id, "unassigned:" + svc.actor.id}:
            fail(403, "industrial_evidence_scope", "证据必须属于本产线或当前贡献人的未绑定文件")
        if row.status != "available":
            fail(409, "industrial_evidence_unavailable", "原件未可靠保存或尚不可用")
        if row.scope_line_id.startswith("unassigned:"):
            row.scope_line_id = line_id
    return ids


def fits(svc, row, context, line_id):
    scope = row.scope
    as_knowledge = {**scope, "line_ids": [scope["line_id"]]}
    if not applicable(as_knowledge, svc.settings, scope["process"], line_id, context):
        return False
    return all(
        not scope.get(key) or context.get(target, "") in scope[key]
        for key, target in [("product_versions", "product_version"), ("equipment_ids", "equipment_id")]
    )


def effective(svc, cls, id, *, context=None, line_id=None, kind=None):
    row = svc.get(cls, id)
    now = current_time()
    if (
        row.status != "published"
        or not row.reviewer_id
        or not aware(row.valid_from) <= now < aware(row.valid_until)
    ):
        fail(409, "industrial_definition_inactive", "引用版本尚未独立发布、已停用或不在有效期内")
    if kind and getattr(row, "kind", None) != kind:
        fail(422, "industrial_definition_kind", "引用的定义类型不符")
    if line_id and (
        row.scope_line_id != line_id or context is not None and not fits(svc, row, context, line_id)
    ):
        fail(422, "industrial_applicability", "定义版本不适用于当前产线、产品版本、基材、工序或设备")
    return row


def governed_values(svc, data, cls, permission):
    line = line_scope(svc, data.scope.line_id, permission, physical_fact=False)
    if line.data.get("process") != data.scope.process:
        fail(422, "industrial_process", "工艺定义与产线工艺不一致")
    evidence(svc, data.evidence_ids, line.id)
    previous = svc.get(cls, data.previous_id) if data.previous_id else None
    if previous and (previous.code != data.code or previous.scope_line_id != line.id):
        fail(422, "industrial_revision", "修订须保持稳定编码及产线")
    revision = (
        svc.db.scalar(
            select(__import__("sqlalchemy").func.max(cls.revision)).where(
                cls.tenant_id == svc.settings.tenant_id,
                cls.site_id == svc.settings.site_id,
                cls.code == data.code,
            )
        )
        or 0
    )
    return dict(
        code=data.code,
        name=data.name,
        status="draft",
        author_id=svc.actor.id,
        previous_id=data.previous_id,
        revision=revision + 1,
        version=1,
        scope=data.scope.model_dump(mode="json"),
        scope_line_id=line.id,
        valid_from=data.valid_from,
        valid_until=data.valid_until,
        evidence_ids=data.evidence_ids,
    )


def definition(svc, data):
    values = governed_values(svc, data, m.DecisionDefinition, "industrial_write")
    validate_definition(svc, data.spec.model_dump(mode="json"), data.scope.line_id)
    row = svc.add(
        m.DecisionDefinition, **values, kind=data.spec.kind, payload=data.spec.model_dump(mode="json")
    )
    svc.audit("industrial.definition.created", row.id, {"kind": row.kind})
    return serialize(row)


def validate_definition(svc, spec, line_id):
    if spec["kind"] == "quality_standard":
        method = effective(svc, m.DecisionDefinition, spec["method_id"], line_id=line_id, kind="method")
        rule = s.StandardSpec.model_validate(spec)
        if method.payload["item"] != rule.item:
            fail(422, "quality_standard_item", "客户标准检测项目须与批准方法的物理含义一致")
        if method.payload["unit"] != rule.unit:
            fail(422, "quality_standard_unit", "标准单位须与批准方法一致；输入换算另由方法明示")
        numeric = method.payload["result_type"] == "numeric"
        if numeric == bool(rule.allowed_values):
            fail(422, "quality_standard_type", "方法结果类型与标准边界类型不一致")
    elif spec["kind"] == "quality_plan":
        if len(spec["standard_ids"]) != len(set(spec["standard_ids"])):
            fail(422, "quality_plan_duplicates", "计划不得重复计同一标准")
        for id in spec["standard_ids"]:
            effective(svc, m.DecisionDefinition, id, line_id=line_id, kind="quality_standard")
    elif spec["kind"] == "qualification":
        effective(svc, m.DecisionDefinition, spec["method_id"], line_id=line_id, kind="method")
    elif spec["kind"] == "process_mode":
        route = svc.get(m.Route, spec["route_id"])
        if route.status != "published" or not route.reviewer_id:
            fail(409, "mode_route", "模式须引用已独立批准路线")
        ids = {step["id"] for step in route.steps}
        if spec["from_step_id"] not in ids or spec["next_step_id"] not in ids:
            fail(422, "mode_steps", "批准模式的前后工序须属于同一路线")
        for id in spec["required_standard_ids"]:
            effective(svc, m.DecisionDefinition, id, line_id=line_id, kind="quality_standard")
        for id in spec["required_material_ids"]:
            if svc.get(m.MaterialItem, id).scope_line_id not in {"", line_id}:
                fail(422, "mode_material_scope", "模式要求的材料不适用于本产线")
        for id in spec["equipment_qualification_ids"]:
            effective(svc, m.DecisionDefinition, id, line_id=line_id, kind="qualification")
        for id in spec["sop_ids"]:
            sop = svc.get(m.Knowledge, id)
            if (
                sop.category != "sop"
                or sop.status != "published"
                or not sop.reviewer_id
                or aware(sop.valid_until) <= current_time()
            ):
                fail(409, "mode_sop", "模式须有当前有效已批准SOP")


def review(svc, kind, id, data):
    cls = GOVERNED.get(kind)
    if not cls:
        fail(404, "industrial_resource", "未知治理资源类型")
    row = svc.get(cls, id)
    if kind == "policy":
        lock(svc, ["digital-policy", row.scope_line_id])
    lock(svc, [kind, row.scope_line_id, row.code])
    row = svc.get(cls, id, True)
    permission = "policy_review" if kind == "policy" else "industrial_review"
    svc.actor.require(
        "industrial_write" if data.action == "submit" else permission, row.site_id, row.scope_line_id
    )
    if row.version != data.version:
        fail(409, "version_conflict", "治理版本已变化，请重新核对")
    if data.action == "submit":
        if row.author_id != svc.actor.id or row.status != "draft":
            fail(409, "industrial_submit", "仅作者可提交自己的草稿")
        row.status = "pending_review"
    elif data.action == "publish":
        if row.status != "pending_review" or row.author_id == svc.actor.id:
            fail(409, "industrial_independent_review", "须由另一审核人发布待审版本")
        if aware(row.valid_until) <= current_time():
            fail(409, "industrial_definition_expired", "过期定义不能发布")
        if kind == "definition":
            validate_definition(svc, row.payload, row.scope_line_id)
        elif kind == "formulation":
            from .formulations import validate_formulation

            validate_formulation(svc, row)
        elif kind == "bom":
            from .supply import validate_bom

            validate_bom(svc, row)
        elif kind == "policy":
            from .digital_tasks import validate_policy

            validate_policy(svc, row)
        others = svc.db.scalars(
            svc.scoped(cls)
            .where(
                cls.code == row.code,
                cls.id != row.id,
                cls.scope_line_id == row.scope_line_id,
                cls.status == "published",
            )
            .with_for_update()
        ).all()
        for other in others:
            if other.id != row.previous_id:
                fail(409, "industrial_parallel_version", "已有有效版本，须明确引用其修订链")
            other.status = "retired"
            other.version += 1
        row.status = "published"
        row.reviewer_id = svc.actor.id
    elif data.action == "reject":
        if row.status != "pending_review" or row.author_id == svc.actor.id:
            fail(409, "industrial_review_state", "只能独立驳回待审版本")
        row.status = "draft"
    elif data.action == "retire":
        if row.status != "published":
            fail(409, "industrial_retire_state", "仅有效版本可停用")
        row.status = "retired"
    elif data.action == "rollback":
        if row.status != "published" or not row.previous_id:
            fail(409, "industrial_rollback_state", "只能回退当前已发布且有前版的版本")
        old = svc.get(cls, row.previous_id, True)
        if (
            not old.reviewer_id
            or old.status not in {"retired", "published"}
            or aware(old.valid_from) > current_time()
            or aware(old.valid_until) <= current_time()
            or old.scope_line_id != row.scope_line_id
        ):
            fail(409, "industrial_rollback_target", "前版未批准、过期或范围不同")
        if kind == "definition":
            validate_definition(svc, old.payload, old.scope_line_id)
        elif kind == "formulation":
            from .formulations import validate_formulation

            validate_formulation(svc, old)
        elif kind == "bom":
            from .supply import validate_bom

            validate_bom(svc, old)
        elif kind == "policy":
            from .digital_tasks import validate_policy

            validate_policy(svc, old)
        old.status = "published"
        old.version += 1
        row.status = "rolled_back"
    row.version += 1
    if data.action in {"publish", "reject"}:
        svc.add(
            m.DomainApproval,
            resource_kind=kind,
            resource_id=row.id,
            resource_version=row.version,
            actor_id=svc.actor.id,
            approved=data.action == "publish",
            fingerprint=digest(detail(svc, kind, row)),
            reason=data.reason,
            expires_at=min(aware(row.valid_until), current_time() + timedelta(days=1)),
            scope_line_id=row.scope_line_id,
        )
    svc.audit(
        "industrial." + kind + "." + data.action, row.id, {"version": row.version, "reason": data.reason}
    )
    return detail(svc, kind, row)


def detail(svc, kind, row):
    result = serialize(row)
    if kind == "formulation":
        result["components"] = [
            serialize(c)
            for c in svc.db.scalars(
                svc.scoped(m.FormulationComponent).where(m.FormulationComponent.formulation_id == row.id)
            )
        ]
    if kind == "bom":
        result["components"] = [
            serialize(c)
            for c in svc.db.scalars(svc.scoped(m.BomComponent).where(m.BomComponent.bom_id == row.id))
        ]
    return result
