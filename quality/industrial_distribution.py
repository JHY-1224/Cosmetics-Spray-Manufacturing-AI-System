"""Typed signed configuration extensions; dependencies must already match at the target."""

from datetime import datetime
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import DateTime, Numeric
from . import models as m
from . import enterprise_schemas as s
from .enterprise import detail, json_value, line_scope, lock, validate_definition
from .security import aware, current_time, digest, fail
from .service import serialize

CLASSES = {
    "definition": m.DecisionDefinition,
    "formulation": m.MaterialFormulation,
    "policy": m.AutomationPolicy,
    "bom": m.BillOfMaterials,
}
DEPENDENCIES = {
    **CLASSES,
    "material": m.MaterialItem,
    "route": m.Route,
    "knowledge": m.Knowledge,
    "evidence": m.Evidence,
    "actor": m.User,
}


def state(svc, kind, row):
    if kind == "actor":
        return {
            key: getattr(row, key)
            for key in ["id", "role", "active", "allowed_sites", "allowed_lines", "auth_revision"]
        }
    return detail(svc, kind, row) if kind in CLASSES else serialize(row)


def references(kind, record, children):
    refs = [("evidence", id) for id in record["evidence_ids"]]
    if kind == "definition":
        p = record["payload"]
        if p["kind"] in {"quality_standard", "qualification"}:
            refs.append(("definition", p["method_id"]))
        if p["kind"] == "quality_plan":
            refs.extend(("definition", id) for id in p["standard_ids"])
        if p["kind"] == "process_mode":
            refs.append(("route", p["route_id"]))
            for key, dep in [
                ("sop_ids", "knowledge"),
                ("required_standard_ids", "definition"),
                ("required_material_ids", "material"),
                ("equipment_qualification_ids", "definition"),
            ]:
                refs.extend((dep, id) for id in p[key])
    elif kind == "formulation":
        for child in children:
            refs.append(("material", child["material_id"]))
            refs.extend(("material", id) for id in child["approved_substitute_ids"])
    elif kind == "bom":
        refs.append(("route", record["route_id"]))
        refs.extend(
            ("bom" if child["child_bom_id"] else "material", child["child_bom_id"] or child["material_id"])
            for child in children
        )
    elif kind == "policy":
        refs.extend(("actor", id) for id in record["actor_ids"])
    return sorted(set(refs))


def extension(svc, kind, row):
    full = detail(svc, kind, row)
    children = full.pop("components", [])
    return {
        "components": children,
        "dependencies": [
            {
                "kind": dep,
                "id": id,
                "sha256": digest(json_value(state(svc, dep, svc.get(DEPENDENCIES[dep], id)))),
            }
            for dep, id in references(kind, full, children)
        ],
    }


def validate_package(body):
    kind, record = body["kind"], body["record"]
    config = body.get("industrial_config")
    if not isinstance(config, dict) or set(config) != {"components", "dependencies"}:
        fail(422, "industrial_package_schema", "工业配置包须包含固定组分及依赖清单")
    children, deps = config["components"], config["dependencies"]
    if (
        not isinstance(children, list)
        or len(children) > 1000
        or not isinstance(deps, list)
        or len(deps) > 2000
    ):
        fail(422, "industrial_package_size", "配置组分或依赖数量超出可核对合同")
    try:
        values = {key: record[key] for key in s.GovernedInput.model_fields}
        if kind == "definition":
            s.DefinitionInput.model_validate({**values, "spec": record["payload"]})
            if record["kind"] != record["payload"]["kind"] or children:
                raise ValueError("definition kind")
        elif kind in {"formulation", "bom"}:
            schema = s.FormulationInput if kind == "formulation" else s.BomInput
            child_schema = s.ComponentInput if kind == "formulation" else s.BomLineInput
            child_cls = m.FormulationComponent if kind == "formulation" else m.BomComponent
            parent_key = "formulation_id" if kind == "formulation" else "bom_id"
            for child in children:
                if (
                    set(child) != set(child_cls.__table__.columns.keys())
                    or child[parent_key] != record["id"]
                    or any(child[k] != record[k] for k in ["tenant_id", "site_id", "scope_line_id"])
                ):
                    raise ValueError("child identity")
            schema.model_validate(
                {
                    **{k: record[k] for k in schema.model_fields if k not in {"components"}},
                    "components": [{k: child[k] for k in child_schema.model_fields} for child in children],
                }
            )
        else:
            s.PolicyInput.model_validate({k: record[k] for k in s.PolicyInput.model_fields})
            if children:
                raise ValueError("policy components")
        expected = references(kind, record, children)
        observed = []
        for dep in deps:
            if (
                not isinstance(dep, dict)
                or set(dep) != {"kind", "id", "sha256"}
                or dep["kind"] not in DEPENDENCIES
                or len(dep["sha256"]) != 64
            ):
                raise ValueError("dependency schema")
            observed.append((dep["kind"], dep["id"]))
        if sorted(observed) != expected:
            raise ValueError("dependency manifest incomplete")
    except (ValueError, KeyError, TypeError):
        fail(422, "industrial_package_contract", "工业配置或完整依赖清单不符合强类型合同")


def dependency_errors(svc, body):
    errors = []
    for dep in body["industrial_config"]["dependencies"]:
        try:
            row = svc.get(DEPENDENCIES[dep["kind"]], dep["id"])
            if digest(json_value(state(svc, dep["kind"], row))) != dep["sha256"]:
                errors.append({"kind": dep["kind"], "id": dep["id"], "code": "dependency_changed"})
        except HTTPException:
            errors.append({"kind": dep["kind"], "id": dep["id"], "code": "dependency_unavailable"})
    return errors


def values(cls, record):
    result = dict(record)
    for col in cls.__table__.columns:
        if result.get(col.name) is not None:
            if isinstance(col.type, DateTime):
                result[col.name] = datetime.fromisoformat(result[col.name])
            elif isinstance(col.type, Numeric):
                result[col.name] = Decimal(str(result[col.name]))
    return result


def validate_local(svc, kind, row):
    line = line_scope(
        svc,
        row.scope_line_id,
        "industrial_review" if kind != "policy" else "policy_review",
        physical_fact=False,
    )
    if (
        row.scope["line_id"] != line.id
        or row.scope["process"] != line.data.get("process")
        or not aware(row.valid_from) <= current_time() < aware(row.valid_until)
    ):
        fail(409, "industrial_package_applicability", "分发版本未生效、已过期或不适用于目标实际产线工艺")
    if kind == "definition":
        validate_definition(svc, row.payload, row.scope_line_id)
    elif kind == "formulation":
        from .formulations import validate_formulation

        validate_formulation(svc, row)
    elif kind == "bom":
        from .supply import validate_bom

        validate_bom(svc, row)
    else:
        from .digital_tasks import validate_policy

        validate_policy(svc, row)


def install(svc, body, local):
    kind, record = body["kind"], body["record"]
    if dependency_errors(svc, body):
        fail(409, "industrial_dependencies", "依赖缺失或版本变化，不能应用工业配置")
    cls = CLASSES[kind]
    lock(svc, ["digital-policy" if kind == "policy" else "industrial-version", record["scope_line_id"]])
    others = svc.db.scalars(
        svc.scoped(cls)
        .where(
            cls.code == record["code"],
            cls.id != record["id"],
            cls.scope_line_id == record["scope_line_id"],
            cls.status == "published",
        )
        .with_for_update()
    ).all()
    for other in others:
        if other.id != record["previous_id"] and body["delivery"]["action"] != "rollback":
            fail(409, "industrial_parallel_version", "分发版本未明确接续当前本地有效版本，禁止并行覆盖")
        if body["delivery"]["action"] == "rollback" and other.previous_id != record["id"]:
            fail(409, "industrial_rollback_lineage", "回退目标不在当前生效版本的明确修订链上")
        other.status = "retired"
        other.version += 1
    if local is None:
        cls = CLASSES[kind]
        v = values(cls, record)
        v.pop("tenant_id")
        site = v.pop("site_id")
        if v["previous_id"] and svc.db.get(cls, v["previous_id"]) is None:
            fail(409, "industrial_lineage_missing", "本地缺少明确修订前版，须先核对并导入依赖")
        local = svc.add(cls, site=site, **v)
        if kind in {"formulation", "bom"}:
            cls = m.FormulationComponent if kind == "formulation" else m.BomComponent
            for child in body["industrial_config"]["components"]:
                v = values(cls, child)
                v.pop("tenant_id")
                svc.add(cls, site=v.pop("site_id"), **v)
        svc.db.flush()
    validate_local(svc, kind, local)
    return local
