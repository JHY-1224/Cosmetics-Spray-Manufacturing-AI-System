"""Business command ledger: durable intent precedes HTTP; uncertain writes are queried by the original key."""

import hmac
import json
import os
import re
from datetime import timedelta
from decimal import Decimal, localcontext
from urllib.parse import urlparse
from fastapi import HTTPException
from sqlalchemy import func, or_, text
from . import models as m
from . import enterprise_schemas as s
from .business_protocol import EFFECTS, SourceIdentity, BusinessRequest, SignedBusinessReceipt
from .db import Database
from .enterprise import json_value, line_scope, lock
from .http_control import client as controlled_client
from .security import Actor, aware, current_time, digest, endpoint, fail, sign
from .service import Service, serialize

PENDING = {"proposed", "awaiting_task", "queued", "sending", "accepted", "unknown", "partial"}


def safe(row):
    return {k: v for k, v in serialize(row).items() if k not in {"lease_token"}}


def credential(row):
    if not re.fullmatch(r"QUALITY_BUSINESS_SECRET_[A-Z0-9_]+", row.credential_reference):
        fail(422, "business_secret_reference", "业务凭据只能引用专用服务端环境变量，不能提交明文密钥")
    value = os.environ.get(row.credential_reference, "")
    if len(value) < 16:
        fail(409, "business_secret_missing", "业务连接尚未配置有效的服务端凭据引用")
    return value


def connector(svc, data, id=None, version=None):
    line_scope(svc, data.line_id, "admin")
    endpoint(data.endpoint, svc.settings)
    if not re.fullmatch(r"QUALITY_BUSINESS_SECRET_[A-Z0-9_]+", data.credential_reference):
        fail(422, "business_secret_reference", "业务凭据须引用QUALITY_BUSINESS_SECRET_专用环境变量")
    if set(data.actions) - set(EFFECTS):
        fail(422, "business_action_contract", "业务连接不能承接计算或设备动作")
    if data.enabled and (not data.supports_idempotency or not data.supports_lookup):
        fail(
            409,
            "business_protocol_required",
            "启用写入连接须明确支持强幂等、按原键查询及business-actions-v1合同",
        )
    if data.enabled and svc.settings.mode == "production" and urlparse(data.endpoint).scheme != "https":
        fail(422, "business_tls_required", "生产业务连接必须使用HTTPS及受信证书")
    if id:
        row = svc.get(m.BusinessConnector, id, True)
        if row.version != version or row.scope_line_id != data.line_id:
            fail(409, "business_connector_version", "连接范围或版本已变化")
        for key, value in data.model_dump(exclude={"line_id", "version"}).items():
            setattr(row, key, value)
        row.version += 1
    else:
        row = svc.add(m.BusinessConnector, scope_line_id=data.line_id, **data.model_dump(exclude={"line_id"}))
    if row.enabled:
        credential(row)
    svc.audit(
        "business.connector_configured",
        row.id,
        {"enabled": row.enabled, "version": row.version, "system": row.system},
    )
    return safe(row)


def intent_key(svc, data):
    p = data.payload
    return "business:" + digest(
        [
            svc.settings.tenant_id,
            svc.settings.site_id,
            svc.settings.node_id,
            data.connector_id,
            p.action,
            p.target_id,
            p.target_version,
            p.reference_id,
            p.material_id,
        ]
    )


def intent_hash(payload, source):
    return digest(
        {
            "contract": "business-actions-v1",
            "payload": payload.model_dump(mode="json", exclude={"reason"}),
            "source_identity": source,
        }
    )


def prepare_payload(svc, data):
    p = data.payload
    line = line_scope(svc, data.line_id, "business_write")
    for id in data.evidence_ids:
        proof = svc.get(m.Evidence, id)
        if proof.status != "available" or proof.scope_line_id not in {line.id, "unassigned:" + svc.actor.id}:
            fail(403, "business_evidence_scope", "业务意图证据必须可靠保存并属于实际产线或当前上传人")
    conn = svc.get(m.BusinessConnector, data.connector_id)
    if conn.scope_line_id != line.id or p.action not in conn.actions or p.action not in EFFECTS:
        fail(403, "business_connector_scope", "连接、动作和实际产线不在同一批准范围")
    if data.expected_result != EFFECTS[p.action]:
        fail(422, "business_expected_effect", "动作与预期业务结果合同不一致")
    dependencies = {"connector": safe(conn), "line": serialize(line)}
    quantity, unit, amount, currency = p.quantity, p.unit, Decimal(0), p.currency
    source = {"system": conn.authority, "object_reference": p.target_id, "source_version": p.target_version}
    context = {"process": line.data.get("process")}
    if p.action == "retest.create":
        target = svc.get(m.Case, p.target_id)
        if (
            target.line_id != line.id
            or str(target.version) != p.target_version
            or target.status in {"closed", "cancelled"}
        ):
            fail(409, "business_case_target", "复测工单目标案件的范围、当前状态或版本已变化")
        dependencies["target"] = serialize(target)
        context.update(target.context)
    elif p.action == "inventory.reserve":
        from .material_inventory import available
        from .supply import current_plan

        stock = svc.get(m.InventorySnapshot, p.target_id)
        hold = svc.get(m.InventoryReservation, p.reference_id)
        material = svc.get(m.MaterialItem, p.material_id)
        if (
            stock.scope_line_id != line.id
            or stock.material_id != material.id
            or hold.inventory_id != stock.id
            or hold.status != "proposed"
            or hold.quantity != p.quantity
            or stock.unit != p.unit
            or stock.source_version != p.target_version
            or stock.source_system != conn.authority
        ):
            fail(409, "business_inventory_target", "源库存、预占建议、材料、单位、数量或源版本不一致")
        if hold.plan_id:
            current_plan(svc, svc.get(m.SupplyPlan, hold.plan_id))
        state = available(svc, stock)
        if (
            state["available"] is None
            or p.quantity > state["source_available"] - state["internal_proposed"] + hold.quantity
        ):
            fail(409, "business_inventory_unavailable", "执行前源库存状态或可用量需重新核对")
        dependencies.update(
            target=serialize(stock), reservation=serialize(hold), material=serialize(material)
        )
        source.update(
            object_reference=stock.lot, material_code=material.code, warehouse=stock.warehouse, lot=stock.lot
        )
    elif p.action == "inventory.release":
        hold = svc.get(m.InventoryReservation, p.target_id)
        stock = svc.get(m.InventorySnapshot, hold.inventory_id)
        material = svc.get(m.MaterialItem, p.material_id)
        if (
            hold.scope_line_id != line.id
            or hold.status != "confirmed"
            or str(hold.version) != p.target_version
            or hold.source_reference != p.reference_id
            or hold.quantity != p.quantity
            or stock.material_id != material.id
            or stock.unit != p.unit
            or stock.source_system != conn.authority
        ):
            fail(409, "business_release_target", "只能释放明确已由源系统确认的同一完整预占，不推测未知操作")
        dependencies.update(target=serialize(hold), inventory=serialize(stock), material=serialize(material))
        source.update(
            object_reference=stock.lot,
            source_version=str(svc.get(m.BusinessCommand, hold.command_id).result.get("source_version", "")),
            material_code=material.code,
            warehouse=stock.warehouse,
            lot=stock.lot,
            reservation_reference=hold.source_reference,
        )
    else:
        from .supply import current_plan
        from .costs import PRECISION

        order = svc.get(m.BusinessOrder, p.target_id)
        material = svc.get(m.MaterialItem, p.material_id)
        if (
            order.scope_line_id != line.id
            or str(order.version) != p.target_version
            or order.source_system != conn.authority
            or material.scope_line_id not in {"", line.id}
            or material.unit != p.unit
            or p.currency != order.currency
            or p.currency not in PRECISION
        ):
            fail(409, "business_purchase_target", "采购须绑定当前订单版本、材料主单位及明确税币种报价")
        source.update(
            object_reference=order.external_id,
            source_version=order.source_version,
            material_code=material.code,
        )
        dependencies.update(target=serialize(order), material=serialize(material))
        if p.action == "purchase.draft":
            plan = svc.get(m.SupplyPlan, p.reference_id)
            if plan.order_id != order.id:
                fail(422, "business_purchase_plan", "缺口计划不属于此订单")
            current_plan(svc, plan)
            demands = svc.db.scalars(
                svc.scoped(m.MaterialDemand).where(
                    m.MaterialDemand.plan_id == plan.id, m.MaterialDemand.material_id == material.id
                )
            ).all()
            shortage = sum((d.shortage for d in demands), Decimal(0))
            lock(svc, ["purchase-shortage", order.id, material.id])
            other_quantity = Decimal(0)
            for other in svc.db.scalars(
                svc.scoped(m.BusinessCommand).where(
                    m.BusinessCommand.target_id == order.id,
                    m.BusinessCommand.action == "purchase.draft",
                    m.BusinessCommand.status.in_(PENDING | {"applied", "blocked"}),
                )
            ):
                other_input = s.BusinessCommandInput.model_validate(other.input)
                if (
                    other.target_version == p.target_version
                    and other_input.payload.material_id == material.id
                    and other.idempotency_key != intent_key(svc, data)
                ):
                    other_quantity += other_input.payload.quantity
            if not demands or p.quantity + other_quantity > shortage:
                fail(409, "business_purchase_shortage", "采购草稿数量不能超过明确的当前材料缺口")
            dependencies.update(plan=serialize(plan), demands=[serialize(d) for d in demands])
        else:
            draft = svc.get(m.BusinessCommand, p.reference_id)
            prior = s.BusinessCommandInput.model_validate(draft.input).payload
            if (
                draft.action != "purchase.draft"
                or draft.status != "applied"
                or draft.target_id != order.id
                or draft.target_version != p.target_version
                or draft.connector_id != conn.id
                or any(
                    getattr(prior, key) != getattr(p, key)
                    for key in [
                        "material_id",
                        "quantity",
                        "unit",
                        "unit_price",
                        "currency",
                        "tax_basis",
                        "tax_rate",
                    ]
                )
            ):
                fail(
                    409,
                    "business_purchase_draft",
                    "提交须对应源已创建、当前订单版本且报价数量不变的明确采购草稿",
                )
            source["draft_reference"] = draft.external_id
            dependencies["draft"] = safe(draft)
            # An applied draft is a source fact, not permanent authority to submit an obsolete shortage.
            plan = svc.get(m.SupplyPlan, prior.reference_id)
            current_plan(svc, plan)
            dependencies["plan"] = serialize(plan)
        with localcontext() as ctx:
            ctx.prec = 128
            amount = p.quantity * p.unit_price * (1 + p.tax_rate if p.tax_basis == "tax_exclusive" else 1)
    source = SourceIdentity.model_validate(source).model_dump(mode="json")
    return json_value(
        {
            **dependencies,
            "source_identity": source,
            "context": context,
            "metrics": {"quantity": quantity, "unit": unit, "amount": amount, "currency": currency},
        }
    )


def create(svc, data, task=None):
    prepared = prepare_payload(svc, data)
    from .enterprise import evidence

    evidence(svc, data.evidence_ids, data.line_id)
    key = intent_key(svc, data)
    lock(svc, ["business-intent", key])
    hashed = intent_hash(data.payload, prepared["source_identity"])
    prior = svc.db.scalar(
        svc.scoped(m.BusinessCommand)
        .where(m.BusinessCommand.connector_id == data.connector_id, m.BusinessCommand.idempotency_key == key)
        .with_for_update()
    )
    if data.approval_id:
        fail(
            422,
            "business_approval_path",
            "业务命令审批由绑定此意图的数字任务记录，不接受客户端自行指定审批引用",
        )
    if prior:
        if prior.input_hash != hashed:
            fail(409, "business_intent_conflict", "同一业务意图身份已有不同数量或报价，不能换幂等键重复发送")
        row = prior
    else:
        pending = (
            svc.db.scalar(
                svc.scoped(m.BusinessCommand)
                .where(m.BusinessCommand.status.in_(PENDING))
                .with_only_columns(func.count())
            )
            or 0
        )
        if pending >= svc.settings.business_queue_limit:
            fail(429, "business_queue_full", "业务命令待核对队列已达容量")
        row = svc.add(
            m.BusinessCommand,
            connector_id=data.connector_id,
            task_id=None,
            action=data.payload.action,
            contract_version="business-actions-v1",
            idempotency_key=key,
            actor_id=svc.actor.id,
            policy_id=data.policy_id,
            approval_id=None,
            target_id=data.payload.target_id,
            target_version=data.payload.target_version,
            input_hash=hashed,
            input=data.model_dump(mode="json"),
            snapshot={"prepared": prepared, "node_id": svc.settings.node_id},
            expected_result={"effect": data.expected_result, **prepared["metrics"]},
            status="proposed",
            scope_line_id=data.line_id,
        )
    if task:
        if row.status not in {"proposed", "awaiting_task"} or row.task_id and row.task_id != task.id:
            if row.task_id == task.id:
                return safe(row)
            fail(409, "business_command_in_flight", "原业务意图已在途或已确认，禁止从新任务重复发送")
        if (
            task.actor_id != svc.actor.id
            or task.kind != row.action
            or task.scope_line_id != row.scope_line_id
        ):
            fail(403, "business_task_binding", "系统执行任务与业务意图不匹配")
        row.task_id = task.id
        row.policy_id = task.policy_id
        row.status = "queued"
        row.version += 1
        row.snapshot = {
            **row.snapshot,
            "prepared": prepared,
            "task_generation": task.target_snapshot["generation"],
            "task_fingerprint": __import__("quality.digital_tasks", fromlist=["fingerprint"]).fingerprint(
                task
            ),
        }
    svc.audit(
        "business.intent_retained",
        row.id,
        {"status": row.status, "key": row.idempotency_key, "physical_action": False},
    )
    return safe(row)


def runtime(svc):
    svc.actor.require("admin", svc.settings.site_id)
    return {
        "send_enabled": svc.settings.business_send_enabled,
        "send_frozen": frozen(svc.settings),
        "node_id": svc.settings.node_id,
        "node_epoch": epoch(svc.settings),
        "unfreeze_requires": "all_uncertain_results_reconciled_and_explicit_source_consumer_handover",
    }


def unfreeze(svc, data):
    state = runtime(svc)
    if data.node_epoch != state["node_epoch"] or not data.source_handover_confirmed:
        fail(409, "business_handover_required", "须核对此节点新运行身份，并明确确认源系统消费者交接")
    uncertain = svc.db.scalar(
        svc.scoped(m.BusinessCommand)
        .where(m.BusinessCommand.status.in_({"sending", "accepted", "unknown", "partial"}))
        .with_only_columns(func.count())
    )
    if uncertain:
        fail(
            409, "business_reconciliation_required", "仍有在途、已接受、未知或部分结果，先按原键核对全部命令"
        )
    svc.audit(
        "business.restore_unfreeze_requested",
        svc.settings.node_id,
        {"reason": data.reason, "node_epoch": data.node_epoch, "source_handover_confirmed": True},
    )
    from .model_journal import write

    svc.db.info.setdefault("after_commit", []).append(
        lambda: write(
            svc.settings.data_dir / "business-frozen.json",
            {
                "allow_send": True,
                "reason": data.reason,
                "node_epoch": data.node_epoch,
                "actor_id": svc.actor.id,
                "confirmed_at": current_time().isoformat(),
            },
        )
    )
    return {"unfreeze_requested": True, "verify_state_after_commit": "/api/industrial/business-runtime"}


def control(svc, id, data):
    row = svc.get(m.BusinessCommand, id, True)
    svc.actor.require("business_write", row.site_id, row.scope_line_id)
    if row.version != data.version:
        fail(409, "version_conflict", "业务命令版本已变化")
    if data.action == "submit":
        if row.status not in {"proposed", "blocked"}:
            fail(409, "business_command_state", "只有尚未发送的原意图可提交授权；未知结果须先查询原键")
        from .digital_tasks import enqueue

        payload = s.BusinessCommandInput.model_validate(row.input)
        prepared = prepare_payload(svc, payload)
        metrics = prepared["metrics"]
        task = enqueue(
            svc,
            s.DigitalTaskInput(
                line_id=row.scope_line_id,
                action=row.action,
                object_id=row.target_id,
                object_version=row.target_version,
                policy_id=row.policy_id,
                quantity=metrics["quantity"],
                amount=metrics["amount"],
                currency=metrics["currency"],
                evidence_ids=[*payload.evidence_ids, *payload.prerequisite_ids],
                payload=payload.model_dump(mode="json"),
            ),
        )
        if task.get("task_created"):
            row.task_id = task["task"]["id"]
            row.status = "awaiting_task"
            row.version += 1
        return {"command": safe(row), "authorization": task}
    if data.action == "cancel":
        if row.status not in {"proposed", "blocked", "awaiting_task", "queued"}:
            fail(
                409,
                "business_cancel_unknown",
                "在途、已接受或未知外部效果不能本地取消；须查询并走独立补偿动作",
            )
        if row.task_id:
            task = svc.get(m.DigitalTask, row.task_id, True)
            if task.status not in {"succeeded", "cancelled"}:
                task.status = "cancelled"
                task.lease_token = ""
                task.lease_until = None
                task.version += 1
        row.status = "cancelled"
        row.lease_token = ""
        row.lease_until = None
        row.version += 1
    elif data.action == "lookup":
        if row.status not in {"accepted", "unknown", "partial", "sending"}:
            fail(409, "business_lookup_state", "当前没有需要按原键核对的外部结果")
        if row.status == "sending" and row.lease_until and aware(row.lease_until) > current_time():
            fail(409, "business_request_in_flight", "原发送请求仍在租约内，不并发重复办理")
        row.status = "unknown" if row.status == "sending" else row.status
        row.lease_until = current_time()
        row.version += 1
    else:
        fail(409, "business_send_worker", "外部发送仅由已授权数字任务和持久Worker办理，不能从接口直接绕过")
    svc.audit(
        "business.command_" + data.action, row.id, {"reason": data.reason, "stable_key": row.idempotency_key}
    )
    return safe(row)


def frozen(settings):
    path = settings.data_dir / "business-frozen.json"
    if not path.exists():
        return False
    try:
        return not json.loads(path.read_text()).get("allow_send", False)
    except (OSError, ValueError, TypeError):
        return True


def epoch(settings):
    from .model_journal import write

    path = settings.data_dir / "business-runtime.json"
    if not path.exists():
        write(path, {"node_id": settings.node_id, "epoch": m.uid(), "created_at": current_time().isoformat()})
    value = json.loads(path.read_text())
    if value.get("node_id") != settings.node_id:
        fail(409, "business_node_identity", "业务消费者身份与本机节点配置不一致")
    return value["epoch"]


def connection_for(svc, row):
    conn = svc.get(m.BusinessConnector, row.connector_id)
    original = row.snapshot["prepared"]["connector"]
    if (
        not conn.enabled
        or conn.contract_version != "business-actions-v1"
        or not conn.supports_lookup
        or not conn.supports_idempotency
    ):
        fail(409, "business_connector_disabled", "业务连接未启用或缺少原键查询／幂等合同")
    if any(getattr(conn, key) != original[key] for key in ["endpoint", "authority", "credential_reference"]):
        fail(
            409, "business_counterparty_changed", "原业务对端已变更；先核对原对端，不能向新地址盲目查询或重发"
        )
    endpoint(conn.endpoint, svc.settings)
    return conn


def claim(settings, database):
    with database.session() as db:
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        svc = Service(db, settings, Actor("business-dispatcher", "admin", settings.sites))
        now = current_time()
        ready = or_(m.BusinessCommand.lease_until.is_(None), m.BusinessCommand.lease_until <= now)
        states = ["accepted", "unknown", "partial", "sending"]
        if settings.business_send_enabled and not frozen(settings):
            states.append("queued")
        row = db.scalar(
            svc.scoped(m.BusinessCommand)
            .where(m.BusinessCommand.status.in_(states), ready)
            .order_by(m.BusinessCommand.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not row:
            return None
        operation = "send" if row.status == "queued" else "lookup"
        try:
            conn = connection_for(svc, row)
            secret = credential(conn)
            if operation == "send":
                if row.snapshot.get("node_id") != settings.node_id:
                    fail(409, "business_node_identity", "恢复的命令节点身份尚未核对")
                from .digital_tasks import actor_for, revalidate, fingerprint

                task = svc.get(m.DigitalTask, row.task_id, True)
                if task.status != "succeeded" or fingerprint(task) != row.snapshot.get("task_fingerprint"):
                    fail(409, "business_task_authority", "缺少已完成且绑定此意图的数字授权任务")
                actor_svc = actor_for(db, settings, task, lock_user=True)
                revalidate(actor_svc, task)
                if conn.version != row.snapshot["prepared"]["connector"]["version"]:
                    fail(409, "business_connector_changed", "连接版本已变化，未发送旧授权意图")
        except HTTPException as exc:
            row.error_code = exc.detail.get("code", "business_authority")
            if operation == "send":
                row.status = "blocked"
            row.lease_until = now + timedelta(minutes=1)
            row.version += 1
            svc.audit("business.blocked", row.id, {"code": row.error_code, "operation": operation})
            return None
        row.fencing += 1
        row.lease_token = m.uid()
        row.lease_until = now + timedelta(seconds=settings.business_timeout + 15)
        row.status = "sending" if operation == "send" else row.status
        row.version += 1
        attempt = svc.add(
            m.BusinessCommandAttempt,
            command_id=row.id,
            fencing=row.fencing,
            kind=operation,
            status="started",
            request_hash=row.input_hash,
            scope_line_id=row.scope_line_id,
        )
        return {
            "id": row.id,
            "lease": row.lease_token,
            "fencing": row.fencing,
            "attempt_id": attempt.id,
            "operation": operation,
            "endpoint": conn.endpoint,
            "credential_reference": conn.credential_reference,
            "secret": secret,
            "request": BusinessRequest(
                tenant_id=settings.tenant_id,
                site_id=row.site_id,
                node_id=settings.node_id,
                node_epoch=epoch(settings),
                idempotency_key=row.idempotency_key,
                input_hash=row.input_hash,
                fencing=row.fencing,
                payload=s.BusinessCommandInput.model_validate(row.input).payload,
                source_identity=SourceIdentity.model_validate(row.snapshot["prepared"]["source_identity"]),
                expected_effect=row.expected_result["effect"],
            ).model_dump(mode="json"),
        }


def receive(svc, row, envelope, secret):
    parsed = SignedBusinessReceipt.model_validate(envelope)
    if not hmac.compare_digest(sign(envelope["body"], secret), parsed.signature):
        fail(403, "business_receipt_auth", "业务回执签名无效")
    body = parsed.body
    if (
        body.tenant_id,
        body.site_id,
        body.node_id,
        body.idempotency_key,
        body.input_hash,
        body.action,
        body.target_id,
        body.target_version,
    ) != (
        row.tenant_id,
        row.site_id,
        row.snapshot["node_id"],
        row.idempotency_key,
        row.input_hash,
        row.action,
        row.target_id,
        row.target_version,
    ):
        fail(
            409, "business_receipt_identity", "回执未绑定原企业、站点、节点、幂等键、动作、数量合同及目标版本"
        )
    if body.status == "not_found":
        if row.status in {"applied", "rejected"}:
            fail(409, "business_receipt_transition", "源查无结果不能撤销已确认终态")
        row.status = "unknown"
        row.error_code = "source_not_found_requires_reconciliation"
        row.result = {"lookup": envelope["body"], "not_resent": True}
        return "unconfirmed"
    if body.source_revision < 1 or not body.external_id:
        fail(422, "business_receipt_revision", "有效业务回执须有源实体标识和单调修订序号")
    hashed = digest(envelope["body"])
    old = svc.db.scalar(
        svc.scoped(m.BusinessReceipt).where(
            m.BusinessReceipt.command_id == row.id, m.BusinessReceipt.source_revision == body.source_revision
        )
    )
    if old:
        if old.payload_hash != hashed:
            fail(409, "business_receipt_conflict", "同一源回执序号出现冲突内容")
        return "duplicate"
    last = svc.db.scalar(
        svc.scoped(m.BusinessReceipt)
        .where(m.BusinessReceipt.command_id == row.id)
        .order_by(m.BusinessReceipt.source_revision.desc())
        .limit(1)
    )
    svc.add(
        m.BusinessReceipt,
        command_id=row.id,
        external_id=body.external_id,
        source_revision=body.source_revision,
        status=body.status,
        payload_hash=hashed,
        payload=envelope["body"],
        scope_line_id=row.scope_line_id,
    )
    if last and body.source_revision < last.source_revision:
        return "late_ignored"
    if row.status in {"applied", "rejected"} and row.status != body.status:
        fail(409, "business_receipt_transition", "已确认的终态回执不得被异步回执倒退或反转")
    expected = row.expected_result
    if body.status in {"applied", "accepted"} and (
        body.effect != expected["effect"]
        or body.quantity != Decimal(expected["quantity"])
        or body.unit != expected["unit"]
        or body.amount != Decimal(expected["amount"])
        or body.currency != expected["currency"]
    ):
        if row.status not in {"applied", "rejected"}:
            row.status = "unknown"
        row.error_code = "business_receipt_effect_conflict"
        row.result = {"observed": envelope["body"]}
        return "requires_reconciliation"
    row.status = body.status
    row.external_id = body.external_id
    row.result = envelope["body"]
    row.error_code = body.error_code
    if row.action.startswith("inventory.") and body.status in {"applied", "partial"}:
        payload = s.BusinessCommandInput.model_validate(row.input).payload
        hold = svc.get(
            m.InventoryReservation,
            payload.reference_id if row.action == "inventory.reserve" else row.target_id,
            True,
        )
        hold.command_id = row.id
        hold.version += 1
        if body.status == "partial":
            hold.status = "unknown"
        else:
            hold.status = "confirmed" if row.action == "inventory.reserve" else "released"
            hold.source_reference = body.external_id
    svc.audit(
        "business.receipt_recorded", row.id, {"status": row.status, "source_revision": body.source_revision}
    )
    return "recorded"


def run_once(settings, database):
    item = claim(settings, database)
    if not item:
        return False
    response = None
    error = ""
    try:
        url = item["endpoint"].rstrip("/") + (
            "/commands" if item["operation"] == "send" else "/commands/lookup"
        )
        with controlled_client(settings, url, settings.business_timeout) as http:
            with http.stream(
                "POST",
                url,
                headers={"Authorization": "Bearer " + item["secret"]},
                json=item["request"],
            ) as reply:
                reply.raise_for_status()
                raw = b""
                for chunk in reply.iter_bytes():
                    raw += chunk
                    if len(raw) > 65536:
                        raise ValueError("business_receipt_size")
                response = json.loads(raw)
    except Exception as exc:
        error = "business_transport_" + type(exc).__name__
    with database.session() as db:
        svc = Service(db, settings, Actor("business-reconciler", "admin", settings.sites))
        row = svc.get(m.BusinessCommand, item["id"], True)
        attempt = svc.get(m.BusinessCommandAttempt, item["attempt_id"], True)
        if row.lease_token != item["lease"] or row.fencing != item["fencing"]:
            return True
        if response is not None:
            try:
                with db.begin_nested():
                    result = receive(svc, row, response, item["secret"])
                attempt.status = result
                attempt.response = response["body"]
            except Exception as exc:
                error = (
                    exc.detail.get("code", "business_receipt_invalid")
                    if isinstance(exc, HTTPException)
                    else "business_receipt_invalid"
                )
        if error and row.status in {"applied", "rejected"}:
            # A signed callback may commit while the HTTP response is lost. A
            # transport failure cannot erase that independently confirmed fact.
            attempt.status = "resolved_by_receipt"
            attempt.response = {
                "transport_error": error,
                "observed_receipt": response,
                "confirmed_status": row.status,
                "source_revision": row.result.get("source_revision"),
            }
            svc.audit("business.response_after_terminal", row.id, {"code": error, "status": row.status})
            error = ""
        if error:
            row.status = "unknown"
            row.error_code = error
            attempt.status = "unknown"
            attempt.response = {
                "error_code": error,
                "lookup_by_original_key_required": True,
                "observed_receipt": response,
            }
            if row.action.startswith("inventory."):
                payload = s.BusinessCommandInput.model_validate(row.input).payload
                hold = svc.get(
                    m.InventoryReservation,
                    payload.reference_id if row.action == "inventory.reserve" else row.target_id,
                    True,
                )
                hold.status, hold.command_id = "unknown", row.id
                hold.version += 1
            svc.audit(
                "business.outcome_unknown",
                row.id,
                {"code": error, "operation": item["operation"], "new_key_created": False},
            )
        attempt.finished_at = current_time()
        row.lease_token = ""
        row.lease_until = current_time() + timedelta(seconds=5)
        row.version += 1
    return True


def serve(settings, stopping):
    database = Database(settings)
    try:
        while not stopping.is_set():
            try:
                run_once(settings, database)
            except Exception:
                stopping.wait(1)
            stopping.wait(1)
    finally:
        database.engine.dispose()
