"""Persistent D1/D2 digital jobs. Policy, identity, data and leases are checked again before commit."""

import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, or_, text
from . import models as m
from . import enterprise_schemas as s
from .db import Database
from .enterprise import effective, fits, governed_values, json_value, line_scope, lock
from .identity import validate_identity
from .security import Actor, ROLES, aware, current_time, digest, fail
from .service import Service, serialize

ACTION_PERMISSIONS = {
    "quality.assess": "inspect",
    "formulation.calculate": "industrial_write",
    "process.select": "industrial_write",
    **{
        key: "business_write"
        for key in [
            "retest.create",
            "inventory.reserve",
            "inventory.release",
            "purchase.draft",
            "purchase.submit",
        ]
    },
}
LOCAL = {
    "quality.assess": s.QualityObservation,
    "formulation.calculate": s.CalculationInput,
    "process.select": s.ProcessDecisionInput,
}


def can_read(actor, action):
    permissions = ROLES[actor.role]
    return bool(
        {
            ACTION_PERMISSIONS.get(action, "unsupported"),
            "business_review" if action not in LOCAL else "industrial_review",
        }
        & permissions
    )


def validate_policy(svc, row):
    record = serialize(row)
    data = s.PolicyInput.model_validate({key: record[key] for key in s.PolicyInput.model_fields})
    if set(data.actor_roles) - set(ROLES):
        fail(422, "policy_roles", "策略角色必须来自已注册角色，不接受模型自造授权")
    for id in data.actor_ids:
        user = svc.get(m.User, id)
        if (
            row.site_id not in user.allowed_sites
            or user.allowed_lines
            and row.scope_line_id not in user.allowed_lines
        ):
            fail(422, "policy_actor_scope", "策略账号没有本工厂／产线授权")
    if any(action.startswith("purchase.") for action in row.actions) and row.automation_level != "D2":
        fail(422, "purchase_independent_approval", "采购起草及提交要求逐次D2独立审核")


def create_policy(svc, data):
    values = governed_values(svc, data, m.AutomationPolicy, "policy_admin")
    row = svc.add(m.AutomationPolicy, **values, **data.model_dump(exclude=set(values) | {"scope"}))
    validate_policy(svc, row)
    svc.audit("digital.policy_drafted", row.id, {"equipment_control": "prohibited"})
    return serialize(row)


def offline(svc, requested):
    if requested:
        return True
    if not svc.settings.center_url:
        return False
    try:
        value = json.loads((svc.settings.data_dir / "sync-status.json").read_text())
        stamp = aware(datetime.fromisoformat(value["last_success"]))
        return (
            value.get("node_id") != svc.settings.node_id
            or not 0 <= (current_time() - stamp).total_seconds() <= 60
        )
    except (OSError, ValueError, KeyError, TypeError):
        return True


def prepare(svc, data):
    svc.actor.require("digital_execute", svc.settings.site_id, data.line_id)
    svc.actor.require(ACTION_PERMISSIONS[data.action], svc.settings.site_id, data.line_id)
    line = line_scope(svc, data.line_id)
    try:
        payload = (LOCAL.get(data.action) or s.BusinessCommandInput).model_validate(data.payload)
    except ValidationError:
        fail(422, "task_payload", "任务载荷不符合该白名单动作的强类型合同")
    if payload.line_id != data.line_id:
        fail(422, "task_payload_scope", "任务载荷与授权产线不一致")
    context = payload.model_dump(mode="json")
    dependencies = {"line": serialize(line)}
    if hasattr(payload, "evidence_ids"):
        proofs = []
        for id in payload.evidence_ids:
            proof = svc.get(m.Evidence, id)
            if (
                proof.scope_line_id not in {data.line_id, "unassigned:" + svc.actor.id}
                or proof.status != "available"
            ):
                fail(403, "task_evidence_scope", "任务原件不属于本次产线／贡献人或尚不可用")
            proofs.append({"id": proof.id, "sha256": proof.sha256, "status": proof.status})
        dependencies["evidence"] = proofs
    if data.action == "quality.assess":
        from .quality_decisions import source_observation

        standard = effective(
            svc,
            m.DecisionDefinition,
            payload.standard_id,
            line_id=data.line_id,
            context=context,
            kind="quality_standard",
        )
        method = effective(
            svc, m.DecisionDefinition, payload.method_id, line_id=data.line_id, context=context, kind="method"
        )
        if standard.payload["method_id"] != method.id or data.object_id != payload.object_id:
            fail(422, "task_target", "任务检测对象或批准方法不一致")
        dependencies.update(
            standard=serialize(standard), method=serialize(method), source=source_observation(svc, payload)
        )
        for key in ["plan_id", "qualification_id"]:
            if getattr(payload, key):
                dependencies[key] = serialize(
                    effective(
                        svc,
                        m.DecisionDefinition,
                        getattr(payload, key),
                        line_id=data.line_id,
                        context=context,
                    )
                )
        if payload.cohort_id:
            dependencies["cohort"] = serialize(svc.get(m.ProductionCohort, payload.cohort_id))
        version, quantity, unit = str(standard.version), Decimal(payload.sample_count), payload.granularity
    elif data.action == "formulation.calculate":
        from .enterprise import detail
        from .material_inventory import available

        row = effective(
            svc, m.MaterialFormulation, payload.formulation_id, line_id=data.line_id, context=context
        )
        if data.object_id != row.id or row.version != payload.formulation_version:
            fail(409, "task_formulation_version", "任务配方标识或批准版本已变化")
        dependencies["formulation"] = detail(svc, "formulation", row)
        dependencies["inventory"] = [
            {
                "record": serialize(svc.get(m.InventorySnapshot, id)),
                "available": json_value(available(svc, svc.get(m.InventorySnapshot, id))),
            }
            for id in payload.inventory_ids
        ]
        dependencies["materials"] = [
            serialize(svc.get(m.MaterialItem, c["material_id"]))
            for c in dependencies["formulation"]["components"]
        ]
        version, quantity, unit = str(row.version), payload.quantity, payload.unit
    elif data.action == "process.select":
        from .process_decisions import sop_basis

        mode = svc.get(m.DecisionDefinition, payload.mode_id)
        context["step_id"] = mode.payload.get("next_step_id", "")
        row = effective(
            svc,
            m.DecisionDefinition,
            payload.mode_id,
            line_id=data.line_id,
            context=context,
            kind="process_mode",
        )
        if data.object_id != payload.object_id:
            fail(422, "task_target", "任务实际工艺对象不一致")
        dependencies.update(
            mode=serialize(row),
            route=serialize(svc.get(m.Route, payload.route_id)),
            assessments=[serialize(svc.get(m.QualityAssessment, id)) for id in payload.assessment_ids],
            sops=sop_basis(svc, line, row, context),
            materials=[serialize(svc.get(m.MaterialItem, id)) for id in sorted(set(payload.material_ids))],
            qualifications=[serialize(effective(svc, m.DecisionDefinition, id, line_id=data.line_id,
                                                context=context, kind="qualification"))
                            for id in sorted(set(payload.qualification_ids))],
        )
        version, quantity, unit = str(row.version), Decimal(1), "operation"
    else:
        from .business_gateway import prepare_payload

        dependencies.update(prepare_payload(svc, payload))
        metrics = dependencies["metrics"]
        if data.amount != Decimal(metrics["amount"]) or data.currency != metrics["currency"]:
            fail(422, "task_business_amount", "授权金额和币种必须与服务端按数量、报价及税基计算的结果一致")
        if data.action != payload.payload.action or data.object_id != payload.payload.target_id:
            fail(422, "task_business_identity", "业务动作和目标必须与类型合同一致")
        version, quantity, unit = (
            payload.payload.target_version,
            payload.payload.quantity,
            payload.payload.unit,
        )
        context = {**context, **dependencies.get("context", {})}
    if data.object_version != version or data.quantity != quantity:
        fail(409, "task_quantity_version", "任务数量或目标版本必须与确定性服务核对结果一致")
    context["process"] = line.data.get("process")
    return payload, context, json_value(dependencies), unit


def decision(svc, data, context, unit, *, exclude_task=""):
    lock(svc, ["digital-policy", data.line_id])
    now = current_time()
    candidates = []
    for row in svc.db.scalars(
        svc.scoped(m.AutomationPolicy).where(
            m.AutomationPolicy.scope_line_id == data.line_id, m.AutomationPolicy.status == "published"
        )
    ):
        if (
            row.reviewer_id
            and aware(row.valid_from) <= now < aware(row.valid_until)
            and svc.actor.role in row.actor_roles
            and (not row.actor_ids or svc.actor.id in row.actor_ids)
            and data.action in row.actions
            and (not row.object_ids or data.object_id in row.object_ids)
            and fits(svc, row, context, data.line_id)
        ):
            candidates.append(row)
    reasons = []
    policy = None
    if any(r.effect == "deny" for r in candidates):
        reasons.append("匹配显式拒绝策略；拒绝优先")
    elif not candidates:
        reasons.append("没有匹配的有效预授权，默认拒绝")
    else:
        priority = max(r.priority for r in candidates)
        choices = [r for r in candidates if r.priority == priority]
        if len(choices) != 1:
            reasons.append("相同优先级的预授权冲突，须独立澄清")
        else:
            policy = choices[0]
    if policy:
        if data.policy_id and data.policy_id != policy.id:
            reasons.append("请求指定策略不是当前唯一生效的最高优先级策略")
        if policy.scope.get("quantity_unit") != unit or data.quantity > policy.max_quantity:
            reasons.append("动作数量单位不符或超过单次批准上限")
        if data.currency != policy.currency or data.amount > policy.max_amount:
            reasons.append("币种不符或金额超过单次批准上限")
        if offline(svc, data.offline) and not policy.offline_allowed:
            reasons.append("当前无新鲜中心通信凭据，且策略不允许离线办理")
        recent = svc.scoped(m.DigitalTask).where(
            m.DigitalTask.policy_id == policy.id,
            m.DigitalTask.created_at >= now - timedelta(seconds=policy.window_seconds),
            m.DigitalTask.id != exclude_task,
        )
        count = svc.db.scalar(recent.with_only_columns(func.count())) or 0
        if count >= policy.max_frequency:
            reasons.append("本策略时间窗口内的发起次数已达上限")
        for kind in policy.required_evidence_kinds:
            cls = {
                "quality_assessment": m.QualityAssessment,
                "calculation": m.FormulationCalculation,
                "source_receipt": m.BusinessReceipt,
                "evidence": m.Evidence,
            }[kind]
            rows = svc.db.scalars(
                svc.scoped(cls).where(
                    cls.id.in_(data.evidence_ids),
                    cls.scope_line_id.in_(
                        [data.line_id, "unassigned:" + svc.actor.id] if kind == "evidence" else [data.line_id]
                    ),
                )
            ).all()
            if not rows:
                reasons.append("缺少明确类型的前置证据：" + kind)
            for row in rows:
                if kind == "quality_assessment":
                    from .quality_decisions import current_assessment

                    if current_assessment(svc, row.id).outcome != "pass":
                        reasons.append("前置质量证据未通过")
                elif kind == "calculation":
                    if row.status != "ready":
                        reasons.append("前置算量仍有库存阻断")
                    from .material_inventory import available

                    definition = effective(
                        svc, m.MaterialFormulation, row.formulation_id, line_id=data.line_id
                    )
                    if definition.version != row.formulation_version:
                        reasons.append("前置算量使用的配方版本已变化")
                    for original in row.snapshot.get("inventory", []):
                        if available(svc, svc.get(m.InventorySnapshot, original["id"]))["available"] is None:
                            reasons.append("前置算量的源库存已失效或存在未知外部占用")
                elif kind == "source_receipt":
                    svc.actor.require("business_read", row.site_id, row.scope_line_id)
                    command = svc.get(m.BusinessCommand, row.command_id)
                    latest = svc.db.scalar(
                        svc.scoped(m.BusinessReceipt)
                        .where(m.BusinessReceipt.command_id == command.id)
                        .order_by(m.BusinessReceipt.source_revision.desc())
                        .limit(1)
                    )
                    if (
                        row.status != "applied"
                        or command.status != "applied"
                        or command.error_code
                        or latest.id != row.id
                    ):
                        reasons.append("源回执不是原命令当前明确已应用的有效版本")
                elif kind == "evidence" and row.status != "available":
                    reasons.append("前置原件尚不可用")
    result = svc.add(
        m.PolicyDecision,
        policy_id=policy.id if policy else None,
        policy_version=policy.version if policy else None,
        actor_id=svc.actor.id,
        action=data.action,
        object_id=data.object_id,
        allowed=bool(policy and not reasons),
        input_hash=digest(data.model_dump(mode="json")),
        snapshot={
            "scope": {
                k: v
                for k, v in context.items()
                if k
                in {
                    "line_id",
                    "process",
                    "product_id",
                    "product_version",
                    "substrate_id",
                    "equipment_id",
                    "step_id",
                }
            },
            "quantity_unit": unit,
            "quantity": str(data.quantity),
            "amount": str(data.amount),
            "currency": data.currency,
            "policy_ids": [r.id for r in candidates],
            "offline": offline(svc, data.offline),
        },
        reasons=reasons,
        scope_line_id=data.line_id,
    )
    return result, policy


def enqueue(svc, data, *, preview=False, batch_id=""):
    payload, context, dependencies, unit = prepare(svc, data)
    result, policy = decision(svc, data, context, unit)
    if preview or not result.allowed:
        return {
            "status": "eligible" if result.allowed else "blocked",
            "policy_decision": serialize(result),
            "automation_level": policy.automation_level if policy else None,
            "task_created": False,
        }
    count = (
        svc.db.scalar(
            svc.scoped(m.DigitalTask)
            .where(m.DigitalTask.status.in_(["queued", "running", "awaiting_approval"]))
            .with_only_columns(func.count())
        )
        or 0
    )
    if count >= svc.settings.digital_queue_limit:
        fail(429, "digital_queue_full", "数字任务队列已达受控容量")
    user = svc.get(m.User, svc.actor.id)
    row = svc.add(
        m.DigitalTask,
        kind=data.action,
        actor_id=svc.actor.id,
        policy_id=policy.id,
        policy_decision_id=result.id,
        responsibility="system:policy:" + policy.id,
        authorization_hash=svc.command_authorization(),
        status="awaiting_approval" if policy.automation_level == "D2" else "queued",
        next_at=current_time(),
        input=data.model_dump(mode="json"),
        batch_id=batch_id,
        scope_line_id=data.line_id,
        target_snapshot={
            "dependencies": dependencies,
            "data_hash": digest(dependencies),
            "policy_version": policy.version,
            "auth_revision": user.auth_revision,
            "generation": 1,
            "context": context,
            "unit": unit,
        },
    )
    svc.audit(
        "digital.task_enqueued",
        row.id,
        {"system_responsibility": row.responsibility, "level": policy.automation_level},
    )
    return {
        "status": row.status,
        "task": safe(row),
        "task_created": True,
        "policy_decision": serialize(result),
    }


def safe(row):
    return {
        key: val for key, val in serialize(row).items() if key not in {"authorization_hash", "lease_token"}
    }


def fingerprint(task):
    return digest(
        [
            task.kind,
            task.input,
            task.target_snapshot["data_hash"],
            task.policy_id,
            task.target_snapshot["policy_version"],
            task.target_snapshot["generation"],
            task.authorization_hash,
        ]
    )


def approve(svc, id, data):
    row = svc.get(m.DigitalTask, id, True)
    svc.actor.require(
        "industrial_review" if row.kind in LOCAL else "business_review", row.site_id, row.scope_line_id
    )
    if row.actor_id == svc.actor.id or row.status != "awaiting_approval" or row.version != data.version:
        fail(409, "digital_independent_review", "须由另一审核人办理当前待审任务版本")
    policy = effective(svc, m.AutomationPolicy, row.policy_id, line_id=row.scope_line_id)
    if (
        not current_time()
        < data.expires_at
        <= min(aware(policy.valid_until), current_time() + timedelta(days=1))
    ):
        fail(422, "digital_approval_expiry", "逐次审批期限须在24小时及策略有效期内")
    svc.add(
        m.DomainApproval,
        resource_kind="digital_task",
        resource_id=row.id,
        resource_version=row.target_snapshot["generation"],
        actor_id=svc.actor.id,
        approved=data.approved,
        fingerprint=fingerprint(row),
        reason=data.reason,
        expires_at=data.expires_at,
        scope_line_id=row.scope_line_id,
    )
    row.status = "queued" if data.approved else "rejected"
    row.version += 1
    svc.audit("digital.task_reviewed", row.id, {"approved": data.approved, "reason": data.reason})
    return safe(row)


def actor_for(db, settings, task, lock_user=False):
    user = db.get(m.User, task.actor_id, with_for_update=lock_user)
    if not user or user.tenant_id != settings.tenant_id:
        fail(403, "digital_identity", "任务账号不属于本企业")
    validate_identity(user, settings)
    actor = Actor(user.id, user.role, user.allowed_sites, user.username, user.allowed_lines)
    svc = Service(db, settings, actor, "digital-task:" + task.id)
    if (
        svc.command_authorization() != task.authorization_hash
        or user.auth_revision != task.target_snapshot["auth_revision"]
    ):
        fail(403, "digital_authorization_changed", "账号权限、范围或授权版本已变化，不得按旧授权提交")
    actor.require("digital_execute", task.site_id, task.scope_line_id)
    return svc


def revalidate(svc, task):
    data = s.DigitalTaskInput.model_validate(task.input)
    payload, context, dependencies, unit = prepare(svc, data)
    if digest(dependencies) != task.target_snapshot["data_hash"]:
        fail(409, "digital_target_stale", "目标、库存、方法、标准或证据版本已变化，须重新核对")
    check, policy = decision(svc, data, context, unit, exclude_task=task.id)
    if (
        not check.allowed
        or policy.id != task.policy_id
        or policy.version != task.target_snapshot["policy_version"]
    ):
        fail(409, "digital_policy_changed", "执行前预授权已不满足当前条件")
    if policy.automation_level == "D2":
        approval = svc.db.scalar(
            svc.scoped(m.DomainApproval)
            .where(
                m.DomainApproval.resource_kind == "digital_task",
                m.DomainApproval.resource_id == task.id,
                m.DomainApproval.resource_version == task.target_snapshot["generation"],
                m.DomainApproval.approved.is_(True),
                m.DomainApproval.fingerprint == fingerprint(task),
                m.DomainApproval.expires_at > current_time(),
            )
            .order_by(m.DomainApproval.created_at.desc())
            .limit(1)
        )
        if not approval or approval.actor_id == task.actor_id:
            fail(409, "digital_approval_stale", "缺少绑定此任务、版本、策略和数据的有效独立审批")
        reviewer = svc.db.get(m.User, approval.actor_id)
        if not reviewer:
            fail(409, "digital_reviewer_revoked", "审核人已失效")
        validate_identity(reviewer, svc.settings)
        Actor(
            reviewer.id, reviewer.role, reviewer.allowed_sites, reviewer.username, reviewer.allowed_lines
        ).require(
            "industrial_review" if task.kind in LOCAL else "business_review", task.site_id, task.scope_line_id
        )
    return payload


def control(svc, id, data):
    row = svc.get(m.DigitalTask, id, True)
    svc.actor.require("digital_execute", row.site_id, row.scope_line_id)
    if row.actor_id != svc.actor.id and svc.actor.role != "admin":
        fail(403, "digital_task_owner", "仅发起人或管理员可控制任务")
    if row.version != data.version:
        fail(409, "version_conflict", "任务版本已变化")
    allowed = {
        "pause": {"queued", "running"},
        "resume": {"paused"},
        "cancel": {"queued", "running", "paused", "awaiting_approval", "failed"},
        "retry": {"failed", "rejected"},
    }
    if row.status not in allowed[data.action]:
        fail(409, "digital_task_state", "当前任务状态不支持该动作")
    if data.action in {"retry", "resume"}:
        payload = s.DigitalTaskInput.model_validate(row.input)
        _, context, deps, unit = prepare(svc, payload)
        if digest(deps) != row.target_snapshot["data_hash"]:
            fail(409, "digital_target_stale", "原输入依赖已变化，请重新发起，不复用旧批准")
        check, policy = decision(svc, payload, context, unit, exclude_task=row.id)
        if (
            not check.allowed
            or policy.id != row.policy_id
            or policy.version != row.target_snapshot["policy_version"]
        ):
            fail(409, "digital_policy_changed", "重试／恢复须重新满足当前策略")
    if data.action == "retry":
        row.target_snapshot = {
            **row.target_snapshot,
            "generation": row.target_snapshot["generation"] + 1,
            "auth_revision": svc.get(m.User, row.actor_id).auth_revision,
        }
        row.authorization_hash = svc.command_authorization()
        row.attempts = 0
        row.error_code = ""
        row.status = "awaiting_approval" if policy.automation_level == "D2" else "queued"
    else:
        row.status = {"pause": "paused", "resume": "queued", "cancel": "cancelled"}[data.action]
    row.lease_token = ""
    row.lease_until = None
    row.version += 1
    row.next_at = current_time()
    svc.audit("digital.task_" + data.action, row.id, {"reason": data.reason, "prior_results_preserved": True})
    return safe(row)


def claim(settings, database):
    with database.session() as db:
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        else:
            db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": int(digest(settings.tenant_id + ":digital:" + settings.node_id)[:15], 16)},
            )
        svc = Service(db, settings, Actor("digital-dispatcher", "admin", settings.sites))
        now = current_time()
        running = svc.scoped(m.DigitalTask).where(
            m.DigitalTask.status == "running", m.DigitalTask.lease_until > now
        )
        if (db.scalar(running.with_only_columns(func.count())) or 0) >= settings.digital_concurrency:
            return None
        row = db.scalar(
            svc.scoped(m.DigitalTask)
            .where(
                or_(
                    (m.DigitalTask.status == "queued") & (m.DigitalTask.next_at <= now),
                    (m.DigitalTask.status == "running") & (m.DigitalTask.lease_until <= now),
                )
            )
            .order_by(m.DigitalTask.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        if row.attempts >= 3:
            row.status = "failed"
            row.error_code = "digital_lease_exhausted"
            row.lease_token = ""
            row.lease_until = None
            return None
        row.status = "running"
        row.attempts += 1
        row.version += 1
        row.lease_token = m.uid()
        row.lease_until = now + timedelta(seconds=settings.digital_timeout + 15)
        return row.id, row.lease_token


def run_claimed(settings, id, lease):
    database = Database(settings)
    try:
        with database.session() as db:
            task = db.get(m.DigitalTask, id)
            if not task or task.status != "running" or task.lease_token != lease:
                return
            svc = actor_for(db, settings, task)
            payload = revalidate(svc, task)
            if task.kind == "quality.assess":
                from .quality_decisions import assess

                result = assess(svc, payload)
            elif task.kind == "formulation.calculate":
                from .formulations import calculate

                result = calculate(svc, payload)
            elif task.kind == "process.select":
                from .process_decisions import select_mode

                result = select_mode(svc, payload)
            else:
                from .business_gateway import create

                result = create(svc, payload, task=task)
            # Cancellation can fence a compute before the final write; stale children cannot commit.
            db.flush()
            db.expire_all()
            task = db.get(m.DigitalTask, id, with_for_update=True)
            if (
                task.status != "running"
                or task.lease_token != lease
                or aware(task.lease_until) <= current_time()
            ):
                fail(409, "digital_lease_lost", "任务已取消、暂停或租约失效，结果不提交")
            fresh = actor_for(db, settings, task, lock_user=True)
            revalidate(fresh, task)
            from .industrial_tools import record_execution

            invocation = record_execution(svc, task, result)
            task.status = "succeeded"
            task.result = {
                "record": result,
                "system_responsibility": task.responsibility,
                "tool_invocation_id": invocation.id,
            }
            task.lease_token = ""
            task.lease_until = None
            task.version += 1
            svc.audit(
                "digital.task_succeeded",
                task.id,
                {"kind": task.kind, "system_responsibility": task.responsibility},
            )
    finally:
        database.engine.dispose()


def mark_failed(settings, database, id, lease, error):
    with database.session() as db:
        row = db.get(m.DigitalTask, id, with_for_update=True)
        if row and row.status == "running" and row.lease_token == lease:
            row.status = "failed"
            row.error_code = error
            row.lease_token = ""
            row.lease_until = None
            row.version += 1
            Service(db, settings, Actor("digital-worker", "admin", settings.sites)).audit(
                "digital.task_failed", id, {"code": error}
            )


def run_once(settings, database, stopping=None):
    from .learning_tasks import launch_claimed

    item = claim(settings, database)
    if item:
        launch_claimed(
            settings,
            database,
            item,
            stopping,
            runner="digital",
            timeout=settings.digital_timeout,
            fail_task=mark_failed,
        )
    return bool(item)


def serve(settings, stopping):
    from .learning_tasks import launch_claimed

    database = Database(settings)
    try:
        futures = []
        with ThreadPoolExecutor(
            max_workers=settings.digital_concurrency, thread_name_prefix="digital-child"
        ) as pool:
            while not stopping.is_set():
                futures = [f for f in futures if not f.done()]
                if len(futures) < settings.digital_concurrency:
                    try:
                        item = claim(settings, database)
                        if item:
                            futures.append(
                                pool.submit(
                                    launch_claimed,
                                    settings,
                                    database,
                                    item,
                                    stopping,
                                    runner="digital",
                                    timeout=settings.digital_timeout,
                                    fail_task=mark_failed,
                                )
                            )
                    except Exception:
                        stopping.wait(1)
                stopping.wait(0.25)
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    from .config import Settings

    settings = Settings()
    try:
        run_claimed(settings, sys.argv[1], sys.argv[2])
    except Exception as exc:
        code = exc.detail.get("code", "task_failed") if isinstance(exc, HTTPException) else type(exc).__name__
        mark_failed(settings, Database(settings), sys.argv[1], sys.argv[2], code)
        raise SystemExit(1) from None
