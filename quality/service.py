from datetime import UTC, timedelta
from decimal import Decimal, localcontext

from sqlalchemy import select, func, or_, cast, Numeric

from . import models as m
from .security import aware, current_time, digest, fail


def serialize(row):
    return {
        c.name: (
            aware(getattr(row, c.name)).astimezone(UTC).isoformat()
            if hasattr(getattr(row, c.name), "isoformat")
            else str(getattr(row, c.name))
            if isinstance(getattr(row, c.name), Decimal)
            else getattr(row, c.name)
        )
        for c in row.__table__.columns
        if c.name not in {"password_hash", "token_hash"}
    }


def critical_context_hash(context):
    return digest({k: v for k, v in context.items() if k != "supplemental_evidence"})


def command_response(value):
    """One-time credentials must never become durable command-result plaintext."""
    if isinstance(value, dict):
        return {
            key: (
                None
                if key.lower() in {"token", "sync_token", "password", "api_key", "secret_key"}
                else command_response(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [command_response(item) for item in value]
    return value


class Service:
    def __init__(self, db, settings, actor, request_id=""):
        self.db, self.settings, self.actor, self.request_id = db, settings, actor, request_id

    def command_authorization(self):
        return digest(
            {
                "tenant": self.settings.tenant_id,
                "node": self.settings.node_id,
                "actor": self.actor.id,
                "role": self.actor.role,
                "sites": sorted(self.actor.sites),
                "lines": sorted(self.actor.lines or []),
            }
        )

    def cached_command_result(self, row):
        if not self.can_read_command_result(row):
            fail(
                403,
                "command_result_restricted",
                "原命令已提交，但授权已变或旧缓存未绑定范围；请按原对象核对，勿重复新建操作",
            )
        return command_response(row.response)

    def can_read_command_result(self, row):
        return bool(row.authorization_hash and row.authorization_hash == self.command_authorization())

    def scoped(self, cls, site=None):
        q = select(cls).where(
            cls.tenant_id == self.settings.tenant_id,
            cls.site_id.in_(["", *(self.actor.sites if site is None else [site])]),
        )
        if self.actor.lines:
            q = q.where(cls.scope_line_id.in_(["", "unassigned:" + self.actor.id, *self.actor.lines]))

        def ids_filter(query, key, allowed):
            if self.db.bind.dialect.name == "sqlite":
                values = func.json_each(cls.scope, "$." + key).table_valued("value")
                length = func.json_array_length(func.json_extract(cls.scope, "$." + key))
                return query.where(
                    or_(
                        length.is_(None),
                        length == 0,
                        select(1).select_from(values).where(values.c.value.in_(allowed)).exists(),
                    )
                )
            from sqlalchemy.dialects.postgresql import JSONB, array

            ids = cast(cls.scope[key], JSONB)
            return query.where(
                or_(func.coalesce(func.jsonb_array_length(ids), 0) == 0, ids.has_any(array(allowed)))
            )

        if cls == m.Knowledge:
            q = ids_filter(q, "factory_ids", self.actor.sites)
            if self.actor.lines:
                q = ids_filter(q, "line_ids", self.actor.lines)
        if cls == m.KnowledgeVector:
            q = q.where(cls.knowledge_id.in_(self.scoped(m.Knowledge).with_only_columns(m.Knowledge.id)))
        return q

    def get(self, cls, id, lock=False):
        q = self.scoped(cls).where(cls.id == id)
        if lock:
            q = q.with_for_update()
        row = self.db.scalar(q)
        if row is None:
            fail(404, "not_found", "记录不存在或无权查看")
        return row

    def add(self, cls, site=None, **data):
        for column in cls.__table__.columns:
            value = data.get(column.name)
            if (
                isinstance(column.type, Numeric)
                and isinstance(value, Decimal)
                and column.type.precision
                and column.type.scale is not None
            ):
                precision, scale = column.type.precision, column.type.scale
                with localcontext() as context:
                    context.prec = precision + scale + 5
                    if (
                        not value.is_finite()
                        or abs(value) >= Decimal(10) ** (precision - scale)
                        or value != value.quantize(Decimal(1).scaleb(-scale))
                    ):
                        fail(422, "numeric_storage_precision", "数量或金额超出明确存储精度：" + column.name)
        if cls == m.User and data.get("auth_origin", "local") == "local":
            from .identity import local_lease

            data.setdefault("lease_expires_at", local_lease(self.settings))
        line = data.get("line_id", "")
        if cls in (m.Plan, m.Approval, m.Receipt, m.Retest, m.Run) and data.get("case_id"):
            line = self.get(m.Case, data["case_id"]).line_id
        if cls == m.Master:
            if data.get("kind") == "line":
                data.setdefault("id", m.uid())
                line = data["id"]
                if self.actor.lines:
                    fail(403, "line_grant", "新增产线需工厂范围管理员/工程师")
            else:
                line = data.get("data", {}).get("line_id", "")
                if data.get("parent_id"):
                    line = line or self.get(m.Master, data["parent_id"]).scope_line_id
        if cls == m.Evidence:
            line = data.pop("bound_line_id", "") or "unassigned:" + self.actor.id
        if cls == m.Knowledge:
            line_ids = data.get("scope", {}).get("line_ids", [])
            factories = data.get("scope", {}).get("factory_ids", [])
            if set(factories) - set(self.actor.sites):
                fail(403, "knowledge_scope", "不能向未授权工厂写入知识")
            if self.actor.lines and (not line_ids or set(line_ids) - set(self.actor.lines)):
                fail(403, "knowledge_scope", "产线受限账号只能创建其授权产线知识")
            line = line_ids[0] if len(line_ids) == 1 else ""
        if line and not line.startswith("unassigned:"):
            self.actor.require("read", line=line)
        data.setdefault("scope_line_id", line)
        row = cls(
            tenant_id=self.settings.tenant_id, site_id=self.settings.site_id if site is None else site, **data
        )
        self.db.add(row)
        self.db.flush()
        return row

    def audit(self, action, target, detail=None):
        case = self.db.get(m.Case, target)
        related = case
        if related is None:
            # New domain objects are already loaded by their command. Avoid querying
            # absent 0012 tables while a genuine historical database is being upgraded.
            related = next(
                (
                    r
                    for r in self.db.identity_map.values()
                    if isinstance(r, tuple(m.ENTERPRISE_MODELS)) and r.id == target
                ),
                None,
            )
        if related is None:
            for cls in (
                m.Evidence,
                m.Plan,
                m.Knowledge,
                m.Master,
                m.Adapter,
                m.Run,
                m.RawRecord,
                m.Disposition,
                m.Distribution,
                m.LearningRecord,
                m.LearningTask,
                m.StudyGroup,
            ):
                related = self.db.get(cls, target)
                if related is not None:
                    break
        self.add(
            m.Audit,
            scope_line_id=related.scope_line_id if related else "",
            actor_id=self.actor.id,
            action=action,
            target_id=target,
            request_id=self.request_id,
            detail=detail or {},
        )

    def event(self, kind, payload):
        # The outbox row is committed atomically with the business change.
        case = self.db.get(m.Case, payload.get("id", "")) if kind == "case.changed" else None
        run = self.db.get(m.Run, payload.get("run_id", "")) if payload.get("run_id") else None
        self.add(
            m.Outbox,
            scope_line_id=case.line_id if case else run.scope_line_id if run else payload.get("line_id", ""),
            kind=kind,
            payload=payload,
        )

    def evidence(self, ids, line_id=""):
        for id in ids:
            row = self.get(m.Evidence, id)
            if row.status != "available":
                fail(422, "evidence_unavailable", "证据尚未可用")
            if line_id:
                self.actor.require("read", line=line_id)
                if row.scope_line_id.startswith("unassigned:"):
                    row.scope_line_id = line_id
                elif row.scope_line_id and row.scope_line_id != line_id:
                    fail(422, "evidence_line", "证据属于其他产线，不能改变归属")
        return ids

    def version(self, case, expected, states=None):
        self.actor.require("read", case.site_id)
        if case.version != expected:
            fail(409, "version_conflict", "记录已变化，请刷新后重新提交")
        if states and case.status not in states:
            fail(409, "invalid_transition", f"当前状态 {case.status} 不允许该操作")

    def advance(self, case, status, action):
        case.status, case.version = status, case.version + 1
        self.audit(action, case.id, {"version": case.version, "status": status})
        evidence_ids = set(case.context.get("evidence_ids", []))
        evidence_ids.update(case.outcome.get("release_evidence_ids", []))
        for supplement in case.context.get("supplemental_evidence", []):
            evidence_ids.update(supplement.get("evidence_ids", []))
        for inspection in case.context.get("inspection_snapshots", []):
            evidence_ids.update(inspection.get("result", {}).get("image_ids", []))
        for cls, field in [(m.Receipt, "content"), (m.Retest, "result")]:
            for record in self.db.scalars(self.scoped(cls).where(cls.case_id == case.id)).all():
                evidence_ids.update(getattr(record, field).get("evidence_ids", []))
        evidence_refs = [
            {"id": e.id, "filename": e.filename, "sha256": e.sha256, "media_type": e.media_type}
            for key in sorted(evidence_ids)
            for e in [self.get(m.Evidence, key)]
        ]
        self.event(
            "case.changed",
            {
                "id": case.id,
                "version": case.version,
                "status": status,
                "title": case.title,
                "process": case.process,
                "line_id": case.line_id,
                "line_name": self.get(m.Master, case.line_id).name,
                "review_flags": case.review_flags,
                "provenance": case.context.get("provenance"),
                "evidence_refs": evidence_refs,
                "context": case.context,
                "diagnosis": case.diagnosis,
                "outcome": case.outcome,
                "records": {
                    name: [
                        serialize(r)
                        for r in self.db.scalars(self.scoped(cls).where(cls.case_id == case.id)).all()
                    ]
                    for name, cls in [
                        ("plans", m.Plan),
                        ("approvals", m.Approval),
                        ("receipts", m.Receipt),
                        ("retests", m.Retest),
                    ]
                },
            },
        )

    def create_master(self, data):
        self.actor.require("master", self.settings.site_id)
        if data.parent_id:
            self.get(m.Master, data.parent_id)
        if data.kind == "load":
            for key, kind in [("hanger_id", "hanger"), ("production_batch_id", "production_batch")]:
                ref = self.get(m.Master, data.data.get(key, ""))
                if ref.kind != kind:
                    fail(422, "load_reference", "挂次必须关联挂具与生产批次")
        if data.kind == "line" and data.data.get("anomaly_threshold"):
            from .anomalies import AnomalyPolicy
            from pydantic import ValidationError

            try:
                AnomalyPolicy.model_validate(data.data["anomaly_threshold"])
            except ValidationError:
                fail(422, "anomaly_policy", "异常触发配置无效，请核对阈值、窗口和样本量")
        if data.kind == "recipe":
            fail(422, "recipe_governance", "配方请通过专用配方版本接口创建，不能通过基础台账绕过审核")
        row = self.add(m.Master, **data.model_dump())
        self.audit("master.created", row.id, {"kind": data.kind})
        return serialize(row)

    def record_lab(self, data):
        self.actor.require("inspect", self.settings.site_id)
        line = self.get(m.Master, data.line_id)
        if line.kind != "line":
            fail(422, "line_invalid", "检验结果必须绑定实际产线")
        self.evidence(data.evidence_ids, data.line_id)
        if data.occurred_at.tzinfo is None or data.occurred_at > current_time() + timedelta(minutes=5):
            fail(422, "test_time", "试验时间必须带时区且不能超前")
        row = self.add(
            m.LabResult,
            **data.model_dump(exclude={"granularity", "instrument_id", "line_id"}),
            scope_line_id=data.line_id,
            operator_id=self.actor.id,
            data={
                "granularity": data.granularity,
                "instrument_id": data.instrument_id,
                "provenance": "demo" if self.settings.mode == "demo" else "manual",
            },
        )
        self.audit("lab.recorded", row.id)
        return serialize(row)

    def create_case(self, data):
        self.actor.require("case", self.settings.site_id)
        line = self.get(m.Master, data.line_id)
        if line.kind != "line":
            fail(422, "line_invalid", "必须指定产线")
        if line.data.get("process") and line.data["process"] != data.process:
            fail(422, "process_mismatch", "案件工艺与实际产线不一致")
        self.evidence(data.evidence_ids, data.line_id)
        if self.settings.mode == "production" and any(
            self.get(m.Evidence, id).source in ("demo", "reference") for id in data.evidence_ids
        ):
            fail(422, "synthetic_evidence", "生产案件不能引用演示/参考证据")
        inspections = [self.get(m.Inspection, id) for id in data.inspection_ids]
        if any(i.line_id != data.line_id or i.result.get("process") != data.process for i in inspections):
            fail(422, "context_mismatch", "检测结果与案件产线或工艺不一致")
        if self.settings.mode == "production" and any(
            i.provenance in ("demo", "reference") for i in inspections
        ):
            fail(422, "synthetic_data", "生产案件不能引用模拟检测结果")
        route = self.get(m.Route, data.route_id) if data.route_id else None
        if route and (
            route.status != "published"
            or route.process != data.process
            or line.data.get("route_id") != route.id
        ):
            fail(422, "route_unbound", "工艺必须已发布并绑定该产线")
        if data.step_id:
            from .templates import effective_steps

            if not route or data.step_id not in {step["id"] for step in effective_steps(route.steps)}:
                fail(422, "case_step", "调查工序必须属于所选的已发布绑定工艺版本，不能按名称猜测")
        recipe_id = data.recipe_id or line.data.get("recipe_id", "")
        recipe = self.get(m.Master, recipe_id) if recipe_id else None
        if recipe and (
            recipe.kind != "recipe"
            or recipe.data.get("status") != "published"
            or not recipe.data.get("reviewer_id")
            or recipe.data.get("line_id") != line.id
            or line.data.get("recipe_id") != recipe.id
        ):
            fail(422, "recipe_invalid", "配方必须为已发布版本")
        provenance = "demo" if self.settings.mode == "demo" else "manual"
        if inspections:
            values = {i.provenance for i in inspections}
            provenance = next(iter(values)) if len(values) == 1 else "mixed"
        context = {
            **data.model_dump(),
            "inspection_snapshots": [serialize(i) for i in inspections],
            "route_snapshot": serialize(route) if route else None,
            "recipe_snapshot": serialize(recipe) if recipe else None,
            "recipe_id": recipe.id if recipe else "",
            "provenance": provenance,
            "snapshot_version": 1,
            "captured_at": current_time().isoformat(),
            "association": "verified"
            if inspections and all(i.association != "unresolved" for i in inspections)
            else "needs_check",
        }
        case = self.add(
            m.Case,
            title=data.title,
            line_id=data.line_id,
            process=data.process,
            author_id=self.actor.id,
            context=context,
        )
        from .learning import definition_rows

        case.context = {**case.context, "condition_dictionary": definition_rows(self, case)}
        self.advance(case, "accepted", "case.created")
        return serialize(case)

    def valid_actions(self, actions, process, product_id="", line_id="", context=None):
        for action in actions:
            if action.type != "sop_action" and action.parameters:
                fail(422, "unapproved_parameters", "仅已发布 SOP 动作可带工艺参数")
            if action.type == "sop_action":
                sop = self.get(m.Knowledge, action.sop_id)
                self.applicable_knowledge(sop, process, product_id, line_id, context)
                if sop.category != "sop" or sop.status != "published":
                    fail(422, "sop_unapproved", "必须引用已发布 SOP")
                for metric, value in action.parameters.items():
                    window = sop.rules.get("parameters", {}).get(metric)
                    if not window or not window.get("unit") or not window["min"] <= value <= window["max"]:
                        fail(422, "parameter_out_of_bounds", "参数缺少批准范围或越界")
                    windows = [window]
                    for other in self.db.scalars(
                        self.scoped(m.Knowledge).where(
                            m.Knowledge.category == "sop", m.Knowledge.status == "published"
                        )
                    ).all():
                        if (
                            other.scope.get("process") not in (process, "all")
                            or aware(other.valid_until) < current_time()
                        ):
                            continue
                        if other.scope.get("product_ids") and product_id not in other.scope["product_ids"]:
                            continue
                        from .scopes import applicable

                        if not applicable(
                            other.scope,
                            self.settings,
                            process,
                            line_id,
                            context or {"product_id": product_id},
                        ):
                            continue
                        if metric in other.rules.get("parameters", {}):
                            windows.append(other.rules["parameters"][metric])
                    low, high = max(w["min"] for w in windows), min(w["max"] for w in windows)
                    if len({w["unit"] for w in windows}) != 1 or low > high:
                        fail(409, "sop_conflict", "有效SOP的关键约束冲突，必须转工艺专家处理")
                    if not low <= value <= high:
                        fail(422, "sop_intersection", "参数不满足所有适用且有效SOP的共同约束")

    def require_reviewed_corrections(self, case):
        if any(not flag.get("resolved") for flag in case.review_flags):
            fail(
                409,
                "correction_review_required",
                "引用的检测事实已有更正；核对并通过关键补证处理，已结案须先受控重开",
            )

    def applicable_knowledge(self, entry, process, product_id="", line_id="", context=None):
        from .scopes import applicable

        if not applicable(
            entry.scope, self.settings, process, line_id, context or {"product_id": product_id}
        ):
            fail(422, "knowledge_inapplicable", "缺少适用范围依据或该知识不适用当前对象")
        if aware(entry.valid_until) < current_time() or entry.scope.get("process") not in (process, "all"):
            fail(422, "knowledge_inapplicable", "知识已失效或不适用该工艺")
        products = entry.scope.get("product_ids", [])
        if products and product_id not in products:
            fail(422, "knowledge_inapplicable", "知识不适用该产品")

    def plan_assistance(self, case, run_id):
        if not run_id:
            return {}
        from .model_assistance import assistance
        source = assistance(self, case)
        if source['run_id'] != run_id or not source['draft_available']:
            fail(409, 'model_draft_stale', '模型草稿来源不是本案当前有效运行，请刷新并重新核对')
        return {'assistance': {'run_id': run_id, 'model': source['model'],
                              'prompt_version': source['prompt_version'],
                              'candidate_actions_hash': digest(source['actions']),
                              'reviewed_by': self.actor.id, 'approval_granted': False}}

    def save_plan(self, id, data):
        self.actor.require("plan", self.settings.site_id)
        case = self.get(m.Case, id, True)
        self.require_reviewed_corrections(case)
        self.version(
            case,
            data.version,
            {
                "diagnosed",
                "needs_evidence",
                "plan_draft",
                "awaiting_approval",
                "rejected",
                "approved",
                "retest_failed",
                "manual_review",
                "partially_executed",
                "execution_failed",
            },
        )
        self.valid_actions(
            data.actions, case.process, case.context.get("product_id", ""), case.line_id, case.context
        )
        content = {"actions": self.identified_actions(data.actions, case), "retest": data.retest.model_dump()}
        content.update(self.plan_assistance(case, data.source_run_id))
        old = self.db.scalars(self.scoped(m.Plan).where(m.Plan.case_id == id)).all()
        for p in old:
            if p.status not in ("executed", "superseded"):
                p.status = "superseded"
        plan = self.add(
            m.Plan,
            case_id=id,
            revision=max([p.revision for p in old], default=0) + 1,
            author_id=self.actor.id,
            status="awaiting_approval",
            content=content,
            fingerprint=digest(content),
            context_hash=critical_context_hash(case.context),
            expires_at=current_time() + timedelta(hours=data.expires_in_hours),
        )
        self.advance(case, "awaiting_approval", "plan.submitted")
        return {"case": serialize(case), "plan": serialize(plan)}

    def active_plan(self, case, id, status):
        self.require_reviewed_corrections(case)
        plan = self.get(m.Plan, id, True)
        if plan.case_id != case.id or plan.status != status:
            fail(409, "plan_invalid", "方案已失效或状态不匹配")
        if aware(plan.expires_at) < current_time() or plan.context_hash != critical_context_hash(
            case.context
        ):
            fail(409, "approval_expired", "方案过期或证据已变化，需要重新审核")
        if digest(plan.content) != plan.fingerprint:
            fail(409, "plan_tampered", "方案完整性校验失败")
        if case.context.get("route_id"):
            route = self.get(m.Route, case.context["route_id"])
            line = self.get(m.Master, case.line_id)
            if route.status != "published" or line.data.get("route_id") != route.id:
                fail(409, "route_changed", "当前有效工艺已变化，需要重新核验和审核方案")
        if case.context.get("recipe_id"):
            recipe = self.get(m.Master, case.context["recipe_id"])
            from .recipes import validate_recipe

            validate_recipe(self, recipe)
            if (
                recipe.data.get("status") != "published"
                or self.get(m.Master, case.line_id).data.get("recipe_id") != recipe.id
            ):
                fail(409, "recipe_changed", "有效配方已变化，须重新核验方案和审批")
        from .schemas import Action

        self.valid_actions(
            [Action.model_validate(a) for a in plan.content["actions"]],
            case.process,
            case.context.get("product_id", ""),
            case.line_id,
            case.context,
        )
        return plan

    def approve(self, id, data):
        self.actor.require("approve", self.settings.site_id)
        case = self.get(m.Case, id, True)
        self.version(case, data.version, {"awaiting_approval"})
        plan = self.active_plan(case, data.plan_id, "awaiting_approval")
        if plan.author_id == self.actor.id:
            fail(403, "separation_of_duties", "方案提交人与审批人必须不同")
        self.add(
            m.Approval,
            case_id=id,
            plan_id=plan.id,
            actor_id=self.actor.id,
            approved=data.approved,
            reason=data.reason,
            fingerprint=plan.fingerprint,
        )
        plan.status = "approved" if data.approved else "rejected"
        self.advance(case, plan.status, "plan.approved" if data.approved else "plan.rejected")
        return serialize(case)

    def execute(self, id, data):
        self.actor.require("execute", self.settings.site_id)
        case = self.get(m.Case, id, True)
        self.version(case, data.version, {"approved"})
        plan = self.active_plan(case, data.plan_id, "approved")
        approval = self.db.scalar(
            self.scoped(m.Approval).where(m.Approval.plan_id == plan.id, m.Approval.approved.is_(True))
        )
        if not approval or approval.fingerprint != plan.fingerprint:
            fail(409, "approval_missing", "缺少有效审批")
        if (
            data.executed_at.tzinfo is None
            or data.executed_at > current_time() + timedelta(minutes=2)
            or data.executed_at < aware(approval.created_at)
        ):
            fail(422, "execution_time", "执行时间必须在审批后且不能在未来")
        completion = data.completion or ("completed" if data.success else "failed")
        if (completion == "completed") != data.success:
            fail(422, "execution_completion", "完成状态与success声明不一致；部分执行不能进入复测")
        if any(i < 0 or i >= len(plan.content["actions"]) for i in data.completed_action_indexes):
            fail(422, "execution_actions", "已完成任务项超出批准方案范围")
        actions = plan.content["actions"]
        ids = [a.get("action_id") or digest([plan.id, i])[:32] for i, a in enumerate(actions)]
        if len(set(data.completed_action_ids)) != len(data.completed_action_ids) or set(
            data.completed_action_ids
        ) - set(ids):
            fail(422, "execution_action_identity", "任务完成标识重复或不属于本批准方案")
        if (
            data.completed_action_ids
            and data.completed_action_indexes
            and set(data.completed_action_ids) != {ids[i] for i in data.completed_action_indexes}
        ):
            fail(422, "execution_action_identity", "稳定任务标识与原索引口径不一致")
        if data.action_results:
            result_ids = [r.action_id for r in data.action_results]
            if len(set(result_ids)) != len(result_ids) or set(result_ids) != set(ids):
                fail(422, "execution_action_results", "逐项回执须完整覆盖本方案任务标识，不能漏项或重复")
            finished = {r.action_id for r in data.action_results if r.status == "completed"}
            if data.completed_action_ids and finished != set(data.completed_action_ids):
                fail(422, "execution_action_results", "逐项实际结果与完成标识冲突")
            if completion == "completed" and len(finished) != len(ids):
                fail(422, "execution_action_results", "仍有未完成任务，不能声明全部完成")
            for item in data.action_results:
                self.evidence(item.evidence_ids, case.line_id)
        self.evidence(data.evidence_ids, case.line_id)
        self.add(
            m.Receipt,
            case_id=id,
            plan_id=plan.id,
            actor_id=self.actor.id,
            content=data.model_dump(mode="json"),
            effective_at=data.executed_at,
        )
        plan.status = (
            "executed"
            if completion == "completed"
            else "partially_executed"
            if completion == "partial"
            else "execution_failed"
        )
        self.advance(
            case,
            "awaiting_retest"
            if completion == "completed"
            else "partially_executed"
            if completion == "partial"
            else "execution_failed",
            "execution.recorded",
        )
        return serialize(case)

    def save_draft(self, id, data):
        self.actor.require("plan", self.settings.site_id)
        case = self.get(m.Case, id, True)
        self.version(case, data.version)
        if case.status in {"closed", "cancelled", "awaiting_retest", "inconclusive", "retest_passed"}:
            fail(409, "draft_state", "当前案件不能新增整改草稿")
        from fastapi import HTTPException

        validation = {"status": "valid"}
        try:
            self.valid_actions(
                data.actions, case.process, case.context.get("product_id", ""), case.line_id, case.context
            )
        except HTTPException as exc:
            validation = {"status": "needs_expert", "code": exc.detail.get("code", "validation")}
        content = {
            "actions": self.identified_actions(data.actions, case),
            "retest": data.retest.model_dump(),
            "draft_validation": validation,
        }
        content.update(self.plan_assistance(case, data.source_run_id))
        old = self.db.scalars(self.scoped(m.Plan).where(m.Plan.case_id == id)).all()
        row = self.add(
            m.Plan,
            case_id=id,
            revision=max((p.revision for p in old), default=0) + 1,
            author_id=self.actor.id,
            status="draft",
            content=content,
            fingerprint=digest(content),
            context_hash=critical_context_hash(case.context),
            expires_at=current_time() + timedelta(hours=data.expires_in_hours),
        )
        self.advance(case, case.status, "plan.draft_saved")
        return {"case": serialize(case), "plan": serialize(row)}

    def identified_actions(self, actions, case):
        rows = []
        for action in actions:
            value = action.model_dump()
            value["action_id"] = value["action_id"] or m.uid()
            if value["owner_id"]:
                user = self.get(m.User, value["owner_id"])
                if (
                    not user.active
                    or case.site_id not in user.allowed_sites
                    or user.allowed_lines
                    and case.line_id not in user.allowed_lines
                ):
                    fail(422, "plan_owner_scope", "任务责任人必须为当前工厂和产线的有效授权人员")
                if value["owner_role"] and user.role != value["owner_role"]:
                    fail(422, "plan_owner_role", "责任人岗位与任务指定岗位不一致")
            rows.append(value)
        if len({a["action_id"] for a in rows}) != len(rows):
            fail(422, "plan_action_identity", "方案内任务稳定标识不能重复")
        return rows

    def submit_draft(self, id, plan_id, expected):
        self.actor.require("plan", self.settings.site_id)
        case = self.get(m.Case, id, True)
        self.require_reviewed_corrections(case)
        self.version(
            case,
            expected,
            {
                "accepted",
                "diagnosed",
                "needs_evidence",
                "plan_draft",
                "awaiting_approval",
                "rejected",
                "approved",
                "retest_failed",
                "manual_review",
                "execution_failed",
                "partially_executed",
            },
        )
        row = self.get(m.Plan, plan_id, True)
        if row.case_id != case.id or row.status != "draft" or row.author_id != self.actor.id:
            fail(409, "draft_state", "只能提交本人仍有效的本案草稿")
        if row.context_hash != critical_context_hash(case.context) or aware(row.expires_at) <= current_time():
            fail(409, "draft_expired", "草稿上下文或有效期已变，请重新核验")
        from .schemas import Action

        self.valid_actions(
            [Action.model_validate(v) for v in row.content["actions"]],
            case.process,
            case.context.get("product_id", ""),
            case.line_id,
            case.context,
        )
        for old in self.db.scalars(self.scoped(m.Plan).where(m.Plan.case_id == id)).all():
            if old.id != row.id and old.status in {"awaiting_approval", "approved"}:
                old.status = "superseded"
        row.content = {k: v for k, v in row.content.items() if k != "draft_validation"}
        row.fingerprint, row.status = digest(row.content), "awaiting_approval"
        self.advance(case, "awaiting_approval", "plan.draft_submitted")
        return {"case": serialize(case), "plan": serialize(row)}

    def retest(self, id, data):
        self.actor.require("retest", self.settings.site_id)
        case = self.get(m.Case, id, True)
        self.require_reviewed_corrections(case)
        self.version(case, data.version, {"awaiting_retest", "inconclusive"})
        plan = self.get(m.Plan, data.plan_id)
        receipt = self.db.scalar(
            self.scoped(m.Receipt).where(m.Receipt.plan_id == plan.id, m.Receipt.case_id == id)
        )
        if not receipt or plan.case_id != id or plan.status != "executed":
            fail(409, "receipt_missing", "须先登记本方案的有效执行回执")
        self.evidence(data.evidence_ids, case.line_id)
        p = plan.content["retest"]
        if (
            not data.started_at.tzinfo
            or not data.finished_at.tzinfo
            or data.finished_at < data.started_at
            or data.finished_at > current_time() + timedelta(minutes=2)
            or data.defective_count > data.sample_count
        ):
            fail(422, "retest_invalid", "复测时间或数量无效")
        sufficient = (
            data.sample_count >= p["min_samples"]
            and data.sample_unit == p["sample_unit"]
            and data.started_at >= aware(receipt.effective_at) + timedelta(minutes=p["stabilization_minutes"])
            and data.finished_at - data.started_at >= timedelta(minutes=p["observation_minutes"])
            and data.conditions_met
            and data.other_constraints_met
        )
        rate = data.defective_count / data.sample_count if data.sample_count else None
        verdict = (
            "inconclusive" if not sufficient else ("passed" if rate <= p["max_defect_rate"] else "failed")
        )
        result = {
            **data.model_dump(mode="json"),
            "defect_rate": rate,
            "denominator": data.sample_count,
            "sufficient": sufficient,
        }
        self.add(
            m.Retest, case_id=id, plan_id=plan.id, actor_id=self.actor.id, result=result, verdict=verdict
        )
        case.outcome = {
            "rectification_effect": verdict,
            "root_cause_confirmation": "unverified",
            "quality_disposition": "pending",
        }
        self.advance(
            case,
            {"passed": "retest_passed", "failed": "retest_failed", "inconclusive": "inconclusive"}[verdict],
            "retest.recorded",
        )
        return serialize(case)

    def close(self, id, data):
        self.actor.require("close", self.settings.site_id)
        case = self.get(m.Case, id, True)
        self.require_reviewed_corrections(case)
        self.version(case, data.version, {"retest_passed"})
        if data.quality_disposition in ("rework", "strip"):
            approved = self.db.scalar(
                self.scoped(m.Disposition).where(
                    m.Disposition.case_id == id,
                    m.Disposition.action == data.quality_disposition,
                    m.Disposition.status == "approved",
                )
            )
            if not approved:
                fail(409, "disposition_pending", "返工/退镀尚无批准记录，请保持待处置")
        case.outcome = {**case.outcome, **data.model_dump(exclude={"version"}), "released": False}
        self.advance(case, "closed", "case.closed")
        self.add(
            m.Knowledge,
            category="case",
            title=case.title,
            content=data.reason,
            author_id=self.actor.id,
            status="draft",
            scope={"process": case.process, "line_ids": [case.line_id], "factory_ids": [case.site_id]},
            source={
                "case_id": id,
                "context_hash": digest(case.context),
                "provenance": case.context["provenance"],
            },
            rules={},
            valid_until=current_time() + timedelta(days=365),
        )
        return serialize(case)
