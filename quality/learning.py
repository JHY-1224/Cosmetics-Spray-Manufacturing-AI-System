"""Human expert contributions, governed reuse and explicitly bounded learning levels."""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from fastapi import Depends, Request
from pydantic import ValidationError
from . import models as m
from . import learning_schemas as s
from .security import aware, current_time, digest, fail
from .service import serialize
from .scopes import applicable

KINDS = {"experience", "condition", "handoff", "material", "dataset", "rule", "replay", "observation"}
EDITABLE = {"draft", "rejected"}


def get_record(svc, id, kind=None, lock=False):
    row = svc.get(m.LearningRecord, id, lock)
    if kind and row.kind != kind:
        fail(422, "learning_kind", "对象类型不匹配")
    if row.kind == "dataset" and row.payload["split"] in {"locked_test", "validation"}:
        svc.actor.require("knowledge_review", row.site_id)
    return row


def touch(svc, row, action, detail=None):
    row.version += 1
    svc.audit(
        "learning." + action,
        row.id,
        {"kind": row.kind, "version": row.version, "revision": row.revision, **(detail or {})},
    )


def scope_check(svc, scope):
    line = svc.get(m.Master, scope.line_id, True)
    if line.kind != "line" or line.data.get("process") != scope.process:
        fail(422, "learning_scope", "请选择本站真实工艺匹配的产线")
    if svc.settings.node_role != "site":
        fail(409, "site_authority", "现场经验与条件由所属厂内节点维护，中心使用已授权知识分发")
    if scope.route_ids:
        for id in scope.route_ids:
            route = svc.get(m.Route, id)
            if route.process != scope.process:
                fail(422, "learning_scope", "路线与工艺不一致")
    if scope.step_ids:
        from .templates import effective_steps

        route_ids = scope.route_ids or ([line.data["route_id"]] if line.data.get("route_id") else [])
        steps = {step["id"] for id in route_ids for step in effective_steps(svc.get(m.Route, id).steps)}
        if not set(scope.step_ids).issubset(steps):
            fail(422, "learning_step", "适用调查工序须引用所声明或当前绑定工艺的真实工序实例")
    return line


def applicability(scope, site):
    return {
        "process": scope["process"],
        "factory_ids": [site],
        "line_ids": [scope["line_id"]],
        **{
            key: scope.get(key, [])
            for key in ["product_ids", "product_families", "substrate_ids", "shapes", "route_ids", "step_ids"]
        },
    }


def case_groups(svc, case):
    groups = {"case:" + case.id}
    context = case.context
    for key in ["production_batch_id", "material_batch_id", "furnace_run_id", "load_id", "work_order_id"]:
        if context.get(key):
            groups.add(key + ":" + context[key])
    for inspection in context.get("inspection_snapshots", []):
        result = inspection.get("result", {})
        for key in [
            "production_batch_id",
            "material_batch_id",
            "furnace_run_id",
            "load_id",
            "object_id",
            "inspection_event_id",
        ]:
            value = result.get(key) or inspection.get(key)
            if value:
                groups.add(key + ":" + str(value))
        for file in result.get("image_ids", []):
            groups.add("file:" + file)
        groups.add("inspection:" + inspection["id"])
    identities = [
        context,
        *[record.get("result", {}) for record in context.get("inspection_snapshots", [])],
        *[record.get("data", {}) for record in context.get("analysis_snapshot", {}).get("passages", [])],
    ]
    for identity in identities:
        groups.update("material_batch_id:" + id for id in identity.get("material_batch_ids", []))
        for key in [
            "production_batch_id",
            "material_batch_id",
            "load_id",
            "work_order_id",
            "equipment_run_id",
            "furnace_run_id",
        ]:
            if identity.get(key):
                groups.add(key + ":" + str(identity[key]))
        run = identity.get("furnace_run_id") or identity.get("equipment_run_id")
        if run:
            groups.update(["equipment_run_id:" + str(run), "furnace_run_id:" + str(run)])
    files = set(context.get("evidence_ids", []))
    for inspection in context.get("inspection_snapshots", []):
        files.update(inspection.get("result", {}).get("image_ids", []))
    for supplement in context.get("supplemental_evidence", []):
        files.update(supplement.get("evidence_ids", []))
    for cls, field in [(m.Receipt, "content"), (m.Retest, "result")]:
        for record in svc.db.scalars(svc.scoped(cls).where(cls.case_id == case.id)):
            files.update(getattr(record, field).get("evidence_ids", []))
    for record in svc.db.scalars(
        svc.scoped(m.LearningRecord).where(
            m.LearningRecord.kind == "observation", m.LearningRecord.case_id == case.id
        )
    ):
        files.update(record.payload.get("evidence_ids", []))
    return sorted({digest(case.site_id + ":" + group) for group in groups} | file_groups(svc, files))


def file_groups(svc, ids):
    result = set()
    for id in set(ids):
        file = svc.get(m.Evidence, id)
        result.update(
            [digest(file.site_id + ":file:" + id), digest(file.site_id + ":file-sha:" + file.sha256)]
        )
    return result


def provenance_guard(svc):
    from sqlalchemy import text, update

    if svc.db.bind.dialect.name == "postgresql":
        svc.db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {
                "key": int(
                    digest("provenance:" + svc.settings.tenant_id + ":" + svc.settings.node_id)[:15], 16
                )
            },
        )
    else:
        svc.db.execute(update(m.LearningTask).where(m.LearningTask.id == "").values(status="queued"))


def site_policy(svc, operation):
    """Trusted site-level boolean policy read, without widening the caller's RLS session."""
    from .db import Database
    from .service import Service
    from .security import Actor

    database = Database(svc.settings)
    try:
        with database.session() as db:
            policy = Service(db, svc.settings, Actor("provenance-policy", "admin", svc.settings.sites))
            return operation(policy)
    finally:
        database.engine.dispose()


def held_groups(svc, groups):
    provenance_guard(svc)
    return site_policy(
        svc,
        lambda policy: bool(
            policy.db.scalar(
                policy.scoped(m.StudyGroup)
                .where(
                    m.StudyGroup.group_key.in_(groups), m.StudyGroup.split.in_(["validation", "locked_test"])
                )
                .limit(1)
            )
        ),
    )


def require_not_holdout(svc, case):
    if held_groups(svc, case_groups(svc, case)):
        fail(409, "heldout_provenance", "本案或同源记录属于保留评估集，不得回流知识、规则开发或训练材料")


def reject_heldout_sources(svc, source):
    groups = set()
    for key in ["case_ids", "file_ids"]:
        if key in source and (
            not isinstance(source[key], list) or any(not isinstance(id, str) for id in source[key])
        ):
            fail(422, "knowledge_source", "已知案件或原件来源必须是记录ID列表")
    for key in ["case_ids", "file_ids"]:
        if len(source.get(key, [])) > 200:
            fail(422, "knowledge_source", "已知来源记录引用最多200项，不能提交无界来源审计")
    for key in ["case_id", "file_id"]:
        if key in source and (not isinstance(source[key], str) or len(source[key]) > 120):
            fail(422, "knowledge_source", "单条来源引用须为有界记录ID")
    for id in [*source.get("case_ids", []), *([source["case_id"]] if source.get("case_id") else [])]:
        groups.update(case_groups(svc, svc.get(m.Case, id, True)))
    files = [*source.get("file_ids", []), *([source["file_id"]] if source.get("file_id") else [])]
    groups.update(file_groups(svc, files))
    if groups and held_groups(svc, groups):
        fail(409, "heldout_knowledge", "保留验证/测试资料不能进入知识或开发规则")
    return sorted(groups)


def source_used_for_learning(svc, groups):
    provenance_guard(svc)

    def used(policy):
        checked = 0
        for row in policy.db.scalars(
            policy.scoped(m.LearningRecord)
            .where(
                m.LearningRecord.kind.in_(["experience", "rule"]),
                m.LearningRecord.status.in_(["pending_apply", "effective", "retired", "rolled_back"]),
            )
            .execution_options(yield_per=100)
        ):
            checked += 1
            if groups & set(row.snapshot.get("groups", [])):
                return True
            if checked > 5000:
                fail(
                    409,
                    "provenance_budget",
                    "历史来源审计超出交互预算，须先完成独立来源审计，不能宣称保留集独立",
                )
        for row in policy.db.scalars(
            policy.scoped(m.Knowledge)
            .where(m.Knowledge.status.in_(["published", "retired"]))
            .execution_options(yield_per=100)
        ):
            checked += 1
            related = set(row.source.get("lineage_groups", []))
            for id in [
                *row.source.get("case_ids", []),
                *([row.source["case_id"]] if row.source.get("case_id") else []),
            ]:
                related.update(case_groups(policy, policy.get(m.Case, id)))
            related.update(
                file_groups(
                    policy,
                    [
                        *row.source.get("file_ids", []),
                        *([row.source["file_id"]] if row.source.get("file_id") else []),
                    ],
                )
            )
            if groups & related:
                return True
            if checked > 5000:
                fail(
                    409,
                    "provenance_budget",
                    "历史来源审计超出交互预算，须先完成独立来源审计，不能宣称保留集独立",
                )
        return False

    return site_policy(svc, used)


def definition_rows(svc, case):
    if "condition_dictionary" in case.context:
        return deepcopy(case.context["condition_dictionary"])
    return [
        dict(id=r.id, revision=r.revision, definition=deepcopy(r.payload))
        for r in svc.db.scalars(
            svc.scoped(m.LearningRecord).where(
                m.LearningRecord.kind == "condition",
                m.LearningRecord.status == "effective",
                m.LearningRecord.scope_line_id == case.line_id,
            )
        )
        if applicable(
            applicability(r.payload["scope"], case.site_id),
            svc.settings,
            case.process,
            case.line_id,
            case.context,
        )
    ]


def condition_snapshot(svc, case):
    definitions = definition_rows(svc, case)
    observations = list(
        svc.db.scalars(
            svc.scoped(m.LearningRecord)
            .where(m.LearningRecord.kind == "observation", m.LearningRecord.case_id == case.id)
            .order_by(m.LearningRecord.created_at, m.LearningRecord.id)
        )
    )
    latest = {r.payload["definition_id"]: r for r in observations}
    fields = []
    for definition in definitions:
        row = latest.get(definition["id"])
        payload = row.payload if row else None
        state = "uncollected"
        if payload:
            definition_payload = definition["definition"]
            age = (current_time() - datetime.fromisoformat(payload["occurred_at"])).total_seconds()
            state = (
                "missing"
                if payload["quality"] == "missing"
                else "valid"
                if payload["quality"] == definition_payload["quality_required"]
                else "invalid_quality"
            )
            if state == "valid" and not 0 <= age <= definition_payload["max_age_seconds"]:
                state = "stale"
        fields.append(
            {
                **definition,
                "observation": {
                    "id": row.id,
                    **payload,
                    "actor_id": row.author_id,
                    "recorded_at": aware(row.created_at).isoformat(),
                }
                if row
                else None,
                "state": state,
            }
        )
    return {
        "version": digest(definitions),
        "definitions": definitions,
        "fields": fields,
        "missing": [f["definition"]["name"] for f in fields if f["state"] != "valid"],
        "captured_at": current_time().isoformat(),
        "semantics": "明确录入的人工或可追溯来源事实；未采集不补零；定义版本冻结",
    }


def condition_measurements(snapshot):
    rows = []
    for field in snapshot["fields"]:
        observation = field["observation"]
        if not observation or observation["value"] is None:
            continue
        definition = field["definition"]
        rows.append(
            SimpleNamespace(
                id=observation["id"],
                equipment_id="condition:" + definition["stable_id"],
                metric=definition["stable_id"],
                occurred_at=aware(datetime.fromisoformat(observation["occurred_at"])),
                value=observation["value"],
                unit=observation["unit"],
                value_type="manual" if observation["source_kind"] == "manual" else definition["value_type"],
                quality="Good" if field["state"] == "valid" else "Uncertain",
                data={"condition_version": field["revision"]},
            )
        )
    return rows


def freeze_case(svc, case):
    return {
        "case": serialize(case),
        "condition_snapshot": condition_snapshot(svc, case),
        "plans": [serialize(r) for r in svc.db.scalars(svc.scoped(m.Plan).where(m.Plan.case_id == case.id))],
        "receipts": [
            serialize(r) for r in svc.db.scalars(svc.scoped(m.Receipt).where(m.Receipt.case_id == case.id))
        ],
        "retests": [
            serialize(r) for r in svc.db.scalars(svc.scoped(m.Retest).where(m.Retest.case_id == case.id))
        ],
        "captured_at": current_time().isoformat(),
        "groups": case_groups(svc, case),
    }


def freeze_experience(svc, case, data):
    snapshot = freeze_case(svc, case)
    materials = [serialize(get_record(svc, id, "material")) for id in data.material_ids]
    snapshot["materials"] = materials
    files = {r["file_id"] for r in materials}
    snapshot["groups"] = sorted(set(snapshot["groups"]) | file_groups(svc, files))
    return snapshot


def check_references(svc, case, ids):
    allowed = {case.id, *case.context.get("evidence_ids", []), *case.context.get("inspection_ids", [])}
    for cls in [
        m.Evidence,
        m.Inspection,
        m.RawRecord,
        m.LabResult,
        m.Measurement,
        m.LearningRecord,
        m.Plan,
        m.Retest,
        m.Receipt,
    ]:
        for id in ids:
            row = svc.db.scalar(svc.scoped(cls).where(cls.id == id))
            if row is not None and (
                row.scope_line_id in {"", case.line_id} or row.scope_line_id == "unassigned:" + svc.actor.id
            ):
                allowed.add(id)
    if set(ids) - allowed:
        fail(422, "expert_reference", "意见、标签依据包含无权或其他产线对象")


def experience_validation(svc, data):
    case = svc.get(m.Case, data.case_id, True)
    svc.version(case, data.case_version)
    if any(label.formed_at > current_time() for label in data.labels):
        fail(422, "label_time", "标签形成时点不能来自未来")
    if any(f.get("resolved") is not True for f in case.review_flags):
        fail(409, "correction_review", "案件更正尚未核对，不能发布其经验")
    if svc.settings.mode == "production" and (
        case.context.get("provenance") in {"demo", "reference"}
        or any(
            i.get("provenance") in {"demo", "reference"} for i in case.context.get("inspection_snapshots", [])
        )
    ):
        fail(422, "synthetic_experience", "生产经验不能将演示/参考事实改标为真实案例")
    refs = [id for opinion in data.opinions for id in [*opinion.support_ids, *opinion.counter_ids]] + [
        id for label in data.labels for id in label.basis_ids
    ]
    check_references(svc, case, refs)
    for id in data.material_ids:
        material = get_record(svc, id, "material")
        if material.case_id != case.id or material.status != "checked":
            fail(409, "material_unchecked", "资料须属于本案且先核对提取结果")
        reject_heldout_sources(svc, {"file_ids": [material.file_id]})
    if any(op.origin == "model_draft" for op in data.opinions) and any(
        label.allow_training or label.allow_evaluation for label in data.labels
    ):
        fail(422, "generated_label", "模型草稿不能直接生成训练/评估真实标签")
    if (
        data.conclusion == "retest_supported"
        or any(label.grade == "retest_supported" for label in data.labels)
    ) and not svc.db.scalar(
        svc.scoped(m.Retest).where(m.Retest.case_id == case.id, m.Retest.verdict == "passed")
    ):
        fail(409, "retest_support", "缺少达标复测，不能标注有复测支持")
    return case


def validate_rule(svc, data):
    scope_check(svc, data.scope)
    from .agent import validate_rules

    rules = data.rule.model_dump(exclude_none=True)
    validate_rules(rules)
    if (
        not rules.get("dimension")
        or not rules.get("defects")
        or not rules.get("cause")
        or not rules.get("next_checks")
    ):
        fail(422, "rule_schema", "候选规则必须有维度、缺陷、候选原因和人工核查步骤")
    definitions = [get_record(svc, id, "condition") for id in data.definition_ids]
    if any(r.status != "effective" or r.scope_line_id != data.scope.line_id for r in definitions):
        fail(422, "rule_dictionary", "规则只能引用适用产线已生效的条件版本")
    by_metric = {r.payload["stable_id"]: r.payload for r in definitions}
    for definition in definitions:
        for key in ["product_ids", "product_families", "substrate_ids", "shapes", "step_ids", "route_ids"]:
            required = set(definition.payload["scope"].get(key, []))
            proposed = set(getattr(data.scope, key))
            if required and (not proposed or not proposed.issubset(required)):
                fail(422, "rule_dictionary_scope", "规则适用范围不能超出条件字典的产品、材料、形状或工序范围")
    for condition in [*rules.get("conditions", []), *rules.get("counter_conditions", [])]:
        definition = by_metric.get(condition["metric"])
        if not definition or definition["unit"] != condition["unit"]:
            fail(422, "rule_unit", "规则指标及单位须与获批条件版本一致")
        value = condition["value"]
        number = isinstance(value, (float, int)) and not isinstance(value, bool)
        valid = {
            "number": number,
            "boolean": isinstance(value, bool),
            "enum": isinstance(value, str) and value in definition["allowed_values"],
            "text": isinstance(value, str),
        }[definition["data_type"]]
        if not valid:
            fail(422, "rule_value_type", "规则取值必须符合条件字典类型")
        if definition["value_type"] != condition["value_type"]:
            fail(422, "rule_nature", "规则不能混用设定/实测/人工性质")
    if set(rules.get("required_metrics", [])) - set(by_metric):
        fail(422, "rule_dictionary", "规则必要指标必须声明获批条件版本")
    for id in data.experience_ids:
        experience = get_record(svc, id, "experience")
        if experience.status != "effective" or experience.scope_line_id != data.scope.line_id:
            fail(409, "rule_experience", "候选规则经验依据尚未生效或范围不符")
        require_not_holdout(svc, svc.get(m.Case, experience.case_id))
    if rules.get("parameters"):
        sops = [svc.get(m.Knowledge, id) for id in data.sop_ids]
        for name, window in rules["parameters"].items():
            if not any(
                k.status == "published"
                and aware(k.valid_until) > current_time()
                and applicable(k.scope, svc.settings, data.scope.process, data.scope.line_id)
                and (bound := k.rules.get("parameters", {}).get(name))
                and bound["unit"] == window["unit"]
                and bound["min"] <= window["min"] <= window["max"] <= bound["max"]
                for k in sops
            ):
                fail(422, "rule_parameter_bounds", "参数建议必须位于适用的批准SOP窗口内")
    return rules


def conflicts(svc, row):
    if row.kind != "rule":
        return []
    rule = row.payload["rule"]
    ids = []
    parent_ids = {parent.id for parent in ancestors(svc, row)}
    for other in svc.db.scalars(
        svc.scoped(m.LearningRecord).where(
            m.LearningRecord.kind == "rule",
            m.LearningRecord.status == "effective",
            m.LearningRecord.scope_line_id == row.scope_line_id,
        )
    ):
        if other.id in parent_ids:
            continue
        existing = other.payload["rule"]
        if set(existing.get("defects", [])) & set(rule.get("defects", [])) and existing.get(
            "dimension"
        ) == rule.get("dimension"):
            ids.append(
                {
                    "id": other.id,
                    "title": other.title,
                    "reason": "相同缺陷/维度已有生效候选，须核对条件重叠及不同专家意见",
                }
            )
    for legacy in svc.db.scalars(
        svc.scoped(m.Knowledge).where(m.Knowledge.category == "root_cause", m.Knowledge.status == "published")
    ):
        if legacy.source.get("learning_record_id") or aware(legacy.valid_until) <= current_time():
            continue
        scope = legacy.scope
        if scope.get("process") not in {"all", row.payload["scope"]["process"]}:
            continue
        if scope.get("line_ids") and row.scope_line_id not in scope["line_ids"]:
            continue
        if scope.get("factory_ids") and row.site_id not in scope["factory_ids"]:
            continue
        if set(rule.get("defects", [])) & set(legacy.rules.get("defects", [])) and legacy.rules.get(
            "dimension"
        ) == rule.get("dimension"):
            ids.append(
                {
                    "id": legacy.id,
                    "title": legacy.title,
                    "kind": "legacy_rule",
                    "reason": "已有旧版批准规则使用同一缺陷/维度；须核对单位、条件重叠和不同意见，不能旁路覆盖",
                }
            )
    return ids


def ensure_capacity(svc):
    from sqlalchemy import func, text, update

    if svc.db.bind.dialect.name == "postgresql":
        svc.db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": int(digest(svc.settings.tenant_id + ":" + svc.settings.node_id)[:15], 16)},
        )
    else:
        # A write lock serializes the capacity check across lines in the same SQLite database.
        svc.db.execute(update(m.LearningTask).where(m.LearningTask.id == "").values(status="queued"))
    # Admission is a trusted site-wide aggregate; it returns no other line's task rows.
    # Its independent read sees the previous lock holder's committed task. Business
    # queries retain the caller's original RLS line context and are never widened.
    from .db import Database
    from sqlalchemy import select

    database = Database(svc.settings)
    try:
        with database.session() as db:
            active = (
                db.scalar(
                    select(func.count())
                    .select_from(m.LearningTask)
                    .where(
                        m.LearningTask.tenant_id == svc.settings.tenant_id,
                        m.LearningTask.site_id == svc.settings.site_id,
                        m.LearningTask.status.in_(["queued", "running"]),
                    )
                )
                or 0
            )
    finally:
        database.engine.dispose()
    if active >= svc.settings.learning_queue_limit:
        fail(429, "learning_backpressure", "本厂学习任务积压已达上限，原始采集与人工流程继续，请稍后重试")


def enqueue(svc, row, kind, data=None):
    ensure_capacity(svc)
    task = svc.add(
        m.LearningTask,
        scope_line_id=row.scope_line_id,
        record_id=row.id,
        kind=kind,
        actor_id=svc.actor.id,
        authorization_hash=svc.command_authorization(),
        data={
            "record_version": row.version,
            "auth_revision": svc.get(m.User, svc.actor.id).auth_revision,
            **(data or {}),
        },
    )
    svc.audit("learning.task_queued", task.id, {"kind": kind, "record_id": row.id})
    return task


def ancestors(svc, row):
    result, seen = [], {row.id}
    previous_id = row.previous_id
    while previous_id:
        if previous_id in seen or len(seen) > 1000:
            fail(409, "learning_lineage", "版本链异常或过长，须先审核，不能跳过历史分支")
        previous = get_record(svc, previous_id, row.kind)
        if previous.scope_line_id != row.scope_line_id or previous.case_id != row.case_id:
            fail(409, "learning_lineage", "版本链跨越原生产范围或案件")
        result.append(previous)
        seen.add(previous_id)
        previous_id = previous.previous_id
    return result


def root_id(svc, row):
    chain = ancestors(svc, row)
    return chain[-1].id if chain else row.id


def deactivate_record(row):
    row.activation = {
        **row.activation,
        "keyword_status": "inactive" if row.knowledge_id else "not_applicable",
        "dictionary_status": "inactive" if row.kind == "condition" else "not_applicable",
    }


def new_record(
    svc,
    kind,
    title,
    line_id,
    payload,
    case_id=None,
    file_id=None,
    previous=None,
    snapshot=None,
    status="draft",
):
    row = svc.add(
        m.LearningRecord,
        kind=kind,
        title=title,
        scope_line_id=line_id,
        payload=payload,
        case_id=case_id,
        file_id=file_id,
        author_id=svc.actor.id,
        previous_id=previous.id if previous else None,
        revision=previous.revision + 1 if previous else 1,
        snapshot=snapshot or {},
        status=status,
    )
    if kind in {"experience", "rule", "condition", "handoff"}:
        row.snapshot = {**row.snapshot, "root_id": root_id(svc, row)}
    svc.audit("learning.created", row.id, {"kind": kind, "case_id": case_id, "revision": row.revision})
    return row


def validate_handoff(svc, data):
    scope_check(svc, data.scope)
    for stage in data.stages:
        svc.evidence([*stage.sample_ids, *stage.acceptance_evidence_ids], data.scope.line_id)
        if stage.state in {"field_connected", "field_accepted"} and svc.settings.mode != "production":
            fail(422, "handoff_field_evidence", "开发夹具不能登记现场连通/验收，须有真实生产配置及验收证据")


def ready_for_publication(svc, row):
    if row.kind == "handoff":
        validate_handoff(svc, s.HandoffInput.model_validate(row.payload))
    if row.kind == "experience":
        data = s.ExperienceInput.model_validate(row.payload)
        if not data.opinions:
            fail(422, "expert_opinion", "经验提交需至少一条可核对的专家意见")
        case = experience_validation(svc, data)
        require_not_holdout(svc, case)
    if row.kind == "condition":
        data = s.ConditionInput.model_validate(row.payload)
        scope_check(svc, data.scope)
        if data.source_id:
            source = svc.get(m.Adapter, data.source_id)
            if source.scope_line_id != row.scope_line_id:
                fail(422, "condition_source", "条件来源必须绑定相同产线")
        other = svc.db.scalar(
            svc.scoped(m.LearningRecord).where(
                m.LearningRecord.kind == "condition",
                m.LearningRecord.status == "effective",
                m.LearningRecord.scope_line_id == row.scope_line_id,
                m.LearningRecord.payload["stable_id"].as_string() == data.stable_id,
            )
        )
        if other and other.id not in {r.id for r in ancestors(svc, row)}:
            fail(409, "condition_branch", "已有同稳定ID版本，必须从该版本创建修订")
    if row.kind == "rule":
        validate_rule(svc, s.RuleProposal.model_validate(row.payload))
        report = row.activation.get("replay")
        if not report or report["rule_hash"] != digest(row.payload) or not report["functional_gate_passed"]:
            fail(409, "rule_replay_required", "先用正常/异常/失败三类已核对开发案例回放并通过功能门槛")
        dataset = get_record(svc, report["dataset_id"], "dataset")
        if svc.settings.mode == "production" and dataset.payload["split"] != "development":
            fail(
                409,
                "production_rule_dataset",
                "生产候选规则须使用经独立核对的真实开发集，功能夹具回放不能用于生产发布",
            )
        if report["dataset_hash"] != digest(dataset.payload):
            fail(409, "dataset_changed", "评估数据版本已变化，请重新回放")


def review(svc, row, data):
    svc.version(row, data.version)
    if row.kind not in {"condition", "experience", "rule", "handoff"}:
        fail(422, "learning_review_kind", "此记录使用单独的核对或数据集锁定流程")
    if data.action == "submit":
        svc.actor.require("knowledge", row.site_id)
        if row.author_id != svc.actor.id or row.status not in EDITABLE:
            fail(409, "learning_state", "只有本人草稿可提交复核")
        ready_for_publication(svc, row)
        row.status = "pending_review"
    else:
        svc.actor.require("knowledge_review", row.site_id)
        if row.author_id == svc.actor.id:
            fail(403, "separation_of_duties", "贡献者不能审核自己的经验或条件版本")
        if data.action in {"publish", "reject"} and row.status != "pending_review":
            fail(409, "learning_state", "只有待复核版本可审核")
        if data.action in {"retire", "rollback"} and row.status != "effective":
            fail(409, "learning_state", "只有生效版本可废止/回退")
        if data.action == "publish":
            ready_for_publication(svc, row)
            if row.kind == "experience" and row.payload.get("labels") and not data.confirm_labels:
                fail(422, "label_review_required", "必须逐项核对标签类型、来源、证据等级和准入理由")
            existing = conflicts(svc, row)
            if existing and not data.acknowledge_conflicts:
                fail(409, "rule_conflict", "存在重叠候选规则，请核对分歧并明确共存依据")
            row.reviewer_id = svc.actor.id
            row.status = "pending_apply"
            row.activation = {
                **row.activation,
                "review_reason": data.reason,
                "labels_checked": data.confirm_labels,
                "conflicts": existing,
                "independent_effect_evaluation": "not_performed",
            }
            touch(svc, row, "approved_waiting_apply", {"reason": data.reason})
            enqueue(svc, row, "activate")
            return serialize(row)
        if data.action == "reject":
            row.status = "rejected"
        else:
            knowledge = svc.get(m.Knowledge, row.knowledge_id, True) if row.knowledge_id else None
            if knowledge:
                knowledge.status = "retired"
            deactivate_record(row)
            row.status = "retired" if data.action == "retire" else "rolled_back"
            if data.action == "rollback":
                previous = next((r for r in ancestors(svc, row) if r.activation.get("applied_at")), None)
                if not previous or not previous.reviewer_id or previous.status != "retired":
                    fail(409, "learning_rollback", "没有经审核且已停用的前置版本可回退")
                previous.status = "effective"
                previous.activation = {
                    **previous.activation,
                    "keyword_status": "active" if previous.knowledge_id else "not_applicable",
                    "dictionary_status": "active" if previous.kind == "condition" else "not_applicable",
                }
                touch(svc, previous, "rollback_restored", {"reason": data.reason})
                if previous.knowledge_id:
                    svc.get(m.Knowledge, previous.knowledge_id, True).status = "published"
        row.reviewer_id = svc.actor.id
    touch(svc, row, data.action, {"reason": data.reason})
    return serialize(row)


def create_dataset(svc, data):
    scope_check(svc, data.scope)
    if data.split in {"locked_test", "validation"}:
        svc.actor.require("knowledge_review")
    else:
        svc.actor.require("knowledge")
    snapshots = []
    groups = set()
    for item in data.items:
        if (
            item.decision_time > current_time()
            or item.label_time > current_time()
            or item.label_time < item.decision_time
        ):
            fail(422, "dataset_time", "决定时点须不晚于标签形成时点，两者不能来自未来")
        case = svc.get(m.Case, item.case_id, True)
        svc.version(case, item.case_version)
        if case.line_id != data.scope.line_id or not applicable(
            applicability(data.scope.model_dump(), case.site_id),
            svc.settings,
            case.process,
            case.line_id,
            case.context,
        ):
            fail(422, "dataset_scope", "数据集中的案件必须属于所选产线及适用产品/工艺范围")
        if svc.settings.mode == "production" and data.split != "functional":
            inspections = case.context.get("inspection_snapshots", [])
            if case.context.get("provenance") in {"demo", "reference"} or any(
                i.get("provenance") in {"demo", "reference"} for i in inspections
            ):
                fail(422, "dataset_provenance", "生产开发/验证数据不能来自演示或参考输入")
            checked = [i for i in inspections if i.get("status") in {"OK", "NG"}]
            if item.sample_role == "normal" and (not checked or any(i["status"] != "OK" for i in checked)):
                fail(422, "dataset_normal_evidence", "正常对照必须有已保留的明确OK检验事实")
            if item.sample_role == "abnormal" and not any(i["status"] == "NG" for i in checked):
                fail(422, "dataset_abnormal_evidence", "异常案例必须有已保留的NG检验事实")
            if item.sample_role == "failed" and not svc.db.scalar(
                svc.scoped(m.Retest).where(m.Retest.case_id == case.id, m.Retest.verdict == "failed")
            ):
                fail(422, "dataset_failure_evidence", "失败案例必须有已保留的未达标复测事实")
        case_group = set(case_groups(svc, case))
        groups.update(case_group)
        snapshot = freeze_case(svc, case)
        measurements = case.context.get("analysis_snapshot", {}).get("measurements", [])
        values = []
        for measurement in measurements:
            if measurement.get("equipment_id", "").startswith("condition:"):
                continue  # Condition observations below have their own recorded/available timestamps.
            available = measurement.get("created_at") or measurement["occurred_at"]
            if (
                aware(datetime.fromisoformat(available)) <= item.decision_time
                and aware(datetime.fromisoformat(measurement["occurred_at"])) <= item.decision_time
            ):
                values.append(measurement)
        for field in snapshot["condition_snapshot"]["fields"]:
            observation = field["observation"]
            if (
                observation
                and observation["value"] is not None
                and datetime.fromisoformat(observation["recorded_at"]) <= item.decision_time
                and datetime.fromisoformat(observation["available_at"]) <= item.decision_time
                and datetime.fromisoformat(observation["occurred_at"]) <= item.decision_time
            ):
                definition = field["definition"]
                values.append(
                    {
                        "id": observation["id"],
                        "equipment_id": "condition:" + definition["stable_id"],
                        "metric": definition["stable_id"],
                        "occurred_at": observation["occurred_at"],
                        "value": observation["value"],
                        "unit": observation["unit"],
                        "quality": "Good"
                        if observation["quality"] in {"Good", "manual_verified"}
                        else observation["quality"],
                        "value_type": "manual"
                        if observation["source_kind"] == "manual"
                        else definition["value_type"],
                    }
                )
        # No outcome, expert answer, label, notes or post-decision inspection is a predictor feature.
        snapshots.append(
            {
                "item": item.model_dump(mode="json"),
                "case": {
                    "id": case.id,
                    "version": case.version,
                    "process": case.process,
                    "line_id": case.line_id,
                    "context": {
                        key: case.context.get(key, "")
                        for key in [
                            "product_id",
                            "product_family",
                            "substrate_id",
                            "shape",
                            "step_id",
                            "route_id",
                        ]
                    },
                },
                "feature_measurements": values,
                "defect_input": case.context["defect"] if data.task == "post_inspection_diagnosis" else None,
                "feature_available_by": item.decision_time.isoformat(),
                "groups": case_group and sorted(case_group),
                "source_hash": digest(snapshot),
                "dictionary_version": snapshot["condition_snapshot"]["version"],
            }
        )
    if data.split in {"locked_test", "validation"} and source_used_for_learning(svc, groups):
        fail(409, "heldout_used", "同源案例、批次、图像内容或原件已有学习/知识引用，不能事后登记成独立保留集")
    existing = svc.db.scalar(svc.scoped(m.StudyGroup).where(m.StudyGroup.group_key.in_(groups)))
    if existing:
        fail(
            409,
            "dataset_group_overlap",
            "本案/同源批次、炉次、物品、事件或图像已分组；不能重复分配到另一数据集",
        )
    row = new_record(
        svc,
        "dataset",
        data.title,
        data.scope.line_id,
        data.model_dump(mode="json"),
        snapshot={
            "items": snapshots,
            "groups": sorted(groups),
            "feature_schema": "conditions-v1",
            "label_review": "pending",
        },
    )
    for key in sorted(groups):
        svc.add(
            m.StudyGroup, scope_line_id=row.scope_line_id, dataset_id=row.id, group_key=key, split=data.split
        )
    return serialize(row)


def record_conditions(svc, case, data):
    svc.actor.require("inspect", case.site_id, case.line_id)
    svc.version(case, data.version)
    if case.status in {"closed", "cancelled"}:
        fail(409, "immutable_case", "已签署案件保留原条件，先受控重开或新建调查")
    dictionary = definition_rows(svc, case)
    by_id = {d["id"]: d for d in dictionary}
    critical = False
    ids = []
    for value in data.values:
        definition = get_record(svc, value.definition_id, "condition")
        config = definition.payload
        if (
            definition.revision != value.definition_version
            or definition.scope_line_id != case.line_id
            or config["scope"]["process"] != case.process
        ):
            fail(422, "condition_version", "条件版本、产线或工艺不一致")
        if definition.status != "effective" and definition.id not in by_id:
            fail(409, "condition_inactive", "此条件版本未生效且未绑定原案件")
        if not applicable(
            applicability(config["scope"], case.site_id),
            svc.settings,
            case.process,
            case.line_id,
            case.context,
        ):
            fail(422, "condition_applicability", "条件产品族、基材、形状、工序或路线不适用")
        if value.unit != config["unit"]:
            fail(422, "condition_unit", "输入单位与冻结定义不一致，不自动换算猜测")
        number = isinstance(value.value, (float, int)) and not isinstance(value.value, bool)
        valid = {
            "number": number,
            "boolean": isinstance(value.value, bool),
            "enum": isinstance(value.value, str) and value.value in config["allowed_values"],
            "text": isinstance(value.value, str) and len(value.value) <= 5000,
        }[config["data_type"]]
        if value.quality == "missing" and value.value is None:
            valid = True
        if not valid:
            fail(422, "condition_type", "条件类型或允许取值不符合字典")
        if number and (
            config["minimum"] is not None
            and value.value < config["minimum"]
            or config["maximum"] is not None
            and value.value > config["maximum"]
        ):
            fail(422, "condition_range", "超出字典允许值，须核对来源，不能改成正常值")
        if value.occurred_at > current_time() + timedelta(
            minutes=5
        ) or value.available_at > current_time() + timedelta(minutes=5):
            fail(422, "condition_time", "发生/可用时间不能来自未来")
        svc.evidence(value.evidence_ids, case.line_id)
        if value.source_kind == "source_record":
            raw = svc.get(m.RawRecord, value.raw_record_id)
            matching = svc.db.scalars(
                svc.scoped(m.Measurement).where(
                    m.Measurement.id.in_(raw.normalized_ids),
                    m.Measurement.metric == config["stable_id"],
                    m.Measurement.scope_line_id == case.line_id,
                )
            ).all()
            case_identity = {
                key: case.context.get(key)
                for key in [
                    "production_batch_id",
                    "material_batch_id",
                    "furnace_run_id",
                    "load_id",
                    "work_order_id",
                ]
                if case.context.get(key)
            }
            objects = {
                r["object_id"] for r in case.context.get("inspection_snapshots", []) if r.get("object_id")
            }

            def matches(measurement):
                identity = measurement.data.get("identity", {})
                linked = identity.get("object_id") in objects or any(
                    identity.get(k) == v for k, v in case_identity.items()
                )
                return (
                    linked
                    and measurement.value == value.value
                    and measurement.unit == value.unit
                    and measurement.quality == value.quality
                    and measurement.value_type == config["value_type"]
                    and aware(measurement.occurred_at) == value.occurred_at
                )

            if (
                raw.scope_line_id != case.line_id
                or raw.source_id != config["source_id"]
                or value.available_at < aware(raw.received_at)
                or not any(matches(r) for r in matching)
            ):
                fail(
                    422,
                    "condition_raw_reference",
                    "来源条件须匹配原始记录、性质、时点和明确批次/实际对象关联；无身份时先保留缺证，不能按临近时间猜测",
                )
        if definition.id not in by_id:
            if case.status in {"awaiting_retest", "inconclusive", "retest_passed"}:
                fail(409, "executed_conditions", "已执行事实不追改条件定义；通过新调查保留原决定")
            dictionary = [d for d in dictionary if d["definition"]["stable_id"] != config["stable_id"]]
            dictionary.append(
                {"id": definition.id, "revision": definition.revision, "definition": deepcopy(config)}
            )
            critical = True
        critical = critical or config["critical"]
        row = new_record(
            svc,
            "observation",
            config["name"],
            case.line_id,
            value.model_dump(mode="json"),
            case_id=case.id,
            snapshot={"definition": deepcopy(config), "revision": definition.revision},
            status="recorded",
        )
        ids.append(row.id)
    if critical:
        if case.status in {"awaiting_retest", "inconclusive", "retest_passed"}:
            fail(409, "executed_conditions", "关键条件更正需保留执行事实并另开调查")
        case.context = {
            **case.context,
            "condition_dictionary": dictionary,
            "condition_record_ids": [*case.context.get("condition_record_ids", []), *ids],
            "snapshot_version": case.context.get("snapshot_version", 1) + 1,
        }
        for plan in svc.db.scalars(
            svc.scoped(m.Plan).where(
                m.Plan.case_id == case.id, m.Plan.status.in_(["approved", "awaiting_approval"])
            )
        ):
            plan.status = "superseded"
        svc.advance(case, "needs_evidence", "condition.critical_recorded")
    return {
        "record_ids": ids,
        "case": serialize(case),
        "conditions": condition_snapshot(svc, case),
        "approval_recheck": critical,
    }


def public_task(row):
    return {k: v for k, v in serialize(row).items() if k not in {"authorization_hash", "lease_token"}}


def register_learning(app, service, command):
    @app.get("/api/learning/status")
    def status(svc=Depends(service)):
        return {
            "knowledge": "governed_versions_with_keyword_reuse",
            "rules": "governed_development_replay",
            "model_training": "not_enabled_not_validated",
            "automatic_asr": "not_enabled",
            "automatic_image_judgement": "not_enabled",
            "video_understanding": "not_enabled",
            "publication": "审核后排队；生效仅指本站关键词检索/规则可用，节点分发回执另记",
            "independent_effect_evaluation": "not_performed",
            "limitation": "开发夹具和回放不能证明客户现场诊断效果或新案件泛化",
        }

    @app.get("/api/learning/{id}")
    def detail(id: str, svc=Depends(service)):
        row = get_record(svc, id)
        return {
            **serialize(row),
            "conflicts": conflicts(svc, row),
            "current_authoritative_case_version": svc.get(m.Case, row.case_id).version
            if row.case_id
            else None,
            "knowledge": serialize(svc.get(m.Knowledge, row.knowledge_id)) if row.knowledge_id else None,
        }

    @app.get("/api/cases/{id}/conditions")
    def case_conditions(id: str, svc=Depends(service)):
        return condition_snapshot(svc, svc.get(m.Case, id))

    @app.post("/api/cases/{id}/conditions")
    def add_conditions(id: str, data: s.ConditionBatch, request: Request, svc=Depends(service)):
        return command(
            svc,
            request,
            data.model_dump(mode="json"),
            lambda: record_conditions(svc, svc.get(m.Case, id, True), data),
        )

    @app.post("/api/learning/handoffs")
    def handoff(data: s.HandoffInput, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge")

        def apply():
            validate_handoff(svc, data)
            return serialize(
                new_record(svc, "handoff", data.title, data.scope.line_id, data.model_dump(mode="json"))
            )

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.post("/api/learning/conditions")
    def condition(data: s.ConditionInput, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge")

        def apply():
            scope_check(svc, data.scope)
            return serialize(
                new_record(svc, "condition", data.name, data.scope.line_id, data.model_dump(mode="json"))
            )

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.post("/api/learning/experiences")
    def experience(data: s.ExperienceInput, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge")

        def apply():
            case = experience_validation(svc, data)
            previous = get_record(svc, data.previous_id, "experience", True) if data.previous_id else None
            if previous and previous.case_id != case.id:
                fail(422, "experience_revision_scope", "修订不得改换原案件")
            return serialize(
                new_record(
                    svc,
                    "experience",
                    data.title,
                    case.line_id,
                    data.model_dump(mode="json"),
                    case_id=case.id,
                    previous=previous,
                    snapshot=freeze_experience(svc, case, data),
                )
            )

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.post("/api/learning/materials")
    def material(data: s.MaterialInput, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge")

        def apply():
            case = svc.get(m.Case, data.case_id)
            svc.evidence([data.file_id], case.line_id)
            row = new_record(
                svc,
                "material",
                data.title,
                case.line_id,
                {"parser_version": "evidence-extract-v1"},
                case_id=case.id,
                file_id=data.file_id,
                status="queued",
            )
            task = enqueue(svc, row, "extract")
            return {"record": serialize(row), "task": public_task(task)}

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.post("/api/learning/materials/{id}/check")
    def check_material(id: str, data: s.MaterialCheck, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge")

        def apply():
            row = get_record(svc, id, "material", True)
            svc.version(row, data.version)
            if row.status not in {"needs_check", "partial", "parse_failed"}:
                fail(409, "material_state", "只有待核对/部分或失败的提取可人工核对；已核对内容保留版本")
            if row.author_id != svc.actor.id:
                fail(403, "material_author", "由录入专家核对原件后形成修订，独立审核另行记录")
            row.payload = {
                **row.payload,
                **data.model_dump(mode="json"),
                "checked_by": svc.actor.id,
                "checked_at": current_time().isoformat(),
            }
            row.status = "checked"
            touch(svc, row, "material_checked", {"reason": data.reason})
            return serialize(row)

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.post("/api/learning/datasets")
    def dataset(data: s.DatasetInput, request: Request, svc=Depends(service)):
        return command(svc, request, data.model_dump(mode="json"), lambda: create_dataset(svc, data))

    @app.post("/api/learning/rules")
    def rule(data: s.RuleProposal, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge")

        def apply():
            validate_rule(svc, data)
            previous = get_record(svc, data.previous_id, "rule", True) if data.previous_id else None
            if previous and previous.scope_line_id != data.scope.line_id:
                fail(422, "rule_revision_scope", "规则修订不可变更原产线")
            return serialize(
                new_record(
                    svc,
                    "rule",
                    data.title,
                    data.scope.line_id,
                    data.model_dump(mode="json"),
                    previous=previous,
                )
            )

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.post("/api/learning/{id}/review")
    def review_record(id: str, data: s.LearningDecision, request: Request, svc=Depends(service)):
        def apply():
            row = get_record(svc, id, lock=True)
            if row.kind == "dataset":
                svc.version(row, data.version)
                svc.actor.require("knowledge_review")
                if data.action == "reject" and row.status == "draft" and row.author_id != svc.actor.id:
                    row.status = "rejected"
                    row.reviewer_id = svc.actor.id
                    touch(svc, row, "dataset_rejected", {"reason": data.reason})
                    return serialize(row)
                if (
                    data.action != "publish"
                    or row.status != "draft"
                    or row.author_id == svc.actor.id
                    or not data.confirm_labels
                ):
                    fail(409, "dataset_review", "数据集须另一审核人核对标签/时点后封存，不能自审或反复改写")
                row.status = "effective"
                row.reviewer_id = svc.actor.id
                row.snapshot = {
                    **row.snapshot,
                    "label_review": "independently_checked",
                    "review_reason": data.reason,
                }
                touch(svc, row, "dataset_sealed")
                return serialize(row)
            return review(svc, row, data)

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.post("/api/learning/{id}/revision")
    def revision(id: str, data: s.RevisionInput, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge")

        def apply():
            row = get_record(svc, id, lock=True)
            svc.version(row, data.version)
            if row.kind not in {"experience", "condition", "rule", "handoff"} or row.status not in {
                "effective",
                "retired",
                "rejected",
                "apply_failed",
            }:
                fail(409, "learning_revision", "请从已审核版本/被驳回稿创建独立修订，原版本保留")
            payload = deepcopy(row.payload)
            snapshot = deepcopy(row.snapshot)
            if row.case_id:
                case = svc.get(m.Case, row.case_id)
                payload["case_version"] = case.version
                snapshot = freeze_case(svc, case)
            result = new_record(
                svc,
                row.kind,
                row.title + " 修订",
                row.scope_line_id,
                payload,
                case_id=row.case_id,
                previous=row,
                snapshot=snapshot,
            )
            svc.audit("learning.revision", result.id, {"previous_id": id, "reason": data.reason})
            return serialize(result)

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.post("/api/learning/{id}/save")
    def save(id: str, data: s.DraftSave, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge")
        data = data.model_dump(mode="json")

        def apply():
            row = get_record(svc, id, lock=True)
            if row.status not in EDITABLE or row.author_id != svc.actor.id:
                fail(409, "learning_edit", "只能编辑本人草稿；生效版本必须创建修订")
            if set(data) != {"version", "payload"}:
                fail(422, "learning_edit_schema", "仅允许版本与对应强类型载荷")
            svc.version(row, data["version"])
            schema = {
                "experience": s.ExperienceInput,
                "condition": s.ConditionInput,
                "rule": s.RuleProposal,
                "handoff": s.HandoffInput,
            }.get(row.kind)
            if schema is None:
                fail(422, "learning_edit_schema", "此记录不可编辑")
            try:
                parsed = schema.model_validate(data["payload"])
            except ValidationError:
                fail(422, "learning_edit_schema", "字段/类型/范围不符合合同")
            if row.kind == "experience":
                case = experience_validation(svc, parsed)
                if case.id != row.case_id:
                    fail(422, "learning_scope", "不能将草稿改到另一案件")
                row.snapshot = freeze_experience(svc, case, parsed)
            else:
                if parsed.scope.line_id != row.scope_line_id:
                    fail(422, "learning_scope", "不能改绑原生产范围")
                scope_check(svc, parsed.scope)
                if row.kind == "rule":
                    validate_rule(svc, parsed)
                if row.kind == "handoff":
                    validate_handoff(svc, parsed)
            row.payload = parsed.model_dump(mode="json")
            if row.kind in {"experience", "rule"}:
                row.payload = {**row.payload, "previous_id": row.previous_id}
            if row.kind in {"experience", "condition", "rule", "handoff"}:
                row.snapshot = {**row.snapshot, "root_id": root_id(svc, row)}
            if (
                row.kind == "condition"
                and row.previous_id
                and row.payload["stable_id"] != get_record(svc, row.previous_id).payload["stable_id"]
            ):
                fail(422, "condition_stable_id", "修订不能改换稳定字段标识；新字段应另建定义")
            row.title = getattr(parsed, "title", getattr(parsed, "name", row.title))
            row.activation = {}
            touch(svc, row, "draft_saved")
            return serialize(row)

        return command(svc, request, data, apply)

    @app.post("/api/learning/{id}/replay")
    def replay(id: str, data: s.ReplayInput, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge")

        def apply():
            row = get_record(svc, id, "rule", True)
            svc.version(row, data.version)
            if row.status not in EDITABLE:
                fail(409, "rule_replay_state", "候选规则须处于可编辑草稿")
            dataset = get_record(svc, data.dataset_id, "dataset")
            if (
                dataset.status != "effective"
                or dataset.payload["split"] not in {"functional", "development"}
                or dataset.payload["task"] != "post_inspection_diagnosis"
            ):
                fail(409, "replay_dataset", "仅使用已核对的功能/开发案例，保留评估集禁止参与开发回放")
            if dataset.scope_line_id != row.scope_line_id:
                fail(422, "dataset_scope", "回放数据与规则产线不一致")
            return public_task(
                enqueue(
                    svc,
                    row,
                    "replay",
                    {
                        "dataset_id": dataset.id,
                        "dataset_hash": digest(dataset.payload),
                        "rule_hash": digest(row.payload),
                    },
                )
            )

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.get("/api/learning-tasks/{id}")
    def task_detail(id: str, svc=Depends(service)):
        task = svc.get(m.LearningTask, id)
        get_record(svc, task.record_id)
        return public_task(task)

    @app.post("/api/learning-tasks/{id}/action")
    def task_action(id: str, data: s.TaskAction, request: Request, svc=Depends(service)):
        def apply():
            task = svc.get(m.LearningTask, id, True)
            row = get_record(svc, task.record_id, lock=True)
            if task.actor_id != svc.actor.id:
                svc.actor.require("admin")
            if data.action == "cancel":
                if task.status not in {"queued", "running"}:
                    fail(409, "task_state", "仅能取消排队/运行任务")
                task.status = "cancelled"
                task.lease_token = ""
                if task.kind == "activate" and row.status == "pending_apply":
                    row.status = "apply_failed"
                    touch(svc, row, "activation_cancelled")
                elif task.kind == "extract" and row.status == "queued":
                    row.status = "parse_failed"
                    touch(svc, row, "extraction_cancelled")
            else:
                if task.status not in {"failed", "cancelled"} or task.attempts >= 3:
                    fail(409, "task_retry", "任务状态或次数不允许重试，保留失败证据后创建受控新版本")
                svc.actor.require("knowledge_review" if task.kind == "activate" else "knowledge")
                ensure_capacity(svc)
                if task.kind == "activate":
                    if row.status != "apply_failed":
                        fail(409, "learning_state", "版本已改变，禁止重放旧生效任务")
                    ready_for_publication(svc, row)
                    row.status = "pending_apply"
                    touch(svc, row, "activation_retried")
                if task.kind == "extract":
                    row.status = "queued"
                    touch(svc, row, "extraction_retried")
                task.actor_id = svc.actor.id
                task.authorization_hash = svc.command_authorization()
                task.data = {
                    **task.data,
                    "record_version": row.version,
                    "auth_revision": svc.get(m.User, svc.actor.id).auth_revision,
                }
                task.status = "queued"
                task.next_at = current_time()
                task.error_code = ""
                task.lease_token = ""
            svc.audit("learning.task_" + data.action, task.id, {"reason": data.reason})
            return public_task(task)

        return command(svc, request, data.model_dump(mode="json"), apply)
