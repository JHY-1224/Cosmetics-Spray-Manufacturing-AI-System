"""Signed, staged configuration rollout. Receipt is separate from explicit local activation."""

import base64
import json
from datetime import datetime, timedelta
from typing import Literal

from fastapi import Depends, Request
from pydantic import Field
from sqlalchemy import func, select

from .querying import get_scope, apply_scope
from . import models as m
from .schemas import Strict
from .security import aware, current_time, digest, fail
from .service import serialize

from .industrial_distribution import CLASSES as INDUSTRIAL_CLASSES

CLASSES = {"knowledge": m.Knowledge, "route": m.Route, **INDUSTRIAL_CLASSES}
TERMINAL = {"applied", "rejected", "expired", "conflict"}


class RolloutInput(Strict):
    kind: Literal["knowledge", "route", "definition", "formulation", "policy", "bom"]
    record_id: str = Field(min_length=1, max_length=64)
    target_nodes: list[str] = Field(min_length=1, max_length=100)
    action: Literal["publish", "retire", "rollback"] = "publish"
    expires_in_hours: int = Field(default=24, ge=1, le=168)
    reason: str = Field(min_length=4, max_length=2000)
    expected_hash: str = Field(default="", max_length=64)


class DecisionInput(Strict):
    decision: Literal["apply", "reject"]
    reason: str = Field(min_length=4, max_length=2000)


def content_hash(record):
    # Lifecycle is versioned in the delivery; lineage FK may be absent in a fresh site.
    return digest({k: v for k, v in record.items() if k not in ("status", "previous_id")})


def check_scope(svc, record, site=None, kind=None):
    svc.actor.require("knowledge_review", site or svc.settings.site_id)
    if kind in INDUSTRIAL_CLASSES:
        svc.actor.require(
            "policy_review" if kind == "policy" else "industrial_review",
            site or svc.settings.site_id,
            record.get("scope_line_id", ""),
        )
    lines = record.get("scope", {}).get("line_ids", [])
    if record.get("scope_line_id"):
        lines = list(set([*lines, record["scope_line_id"]]))
    if svc.actor.lines and (not lines or set(lines) - set(svc.actor.lines)):
        fail(403, "distribution_scope", "分发范围超出当前账号授权产线")


def build_package(settings, row, kind, delivery, svc=None):
    from .sync import private_key, public_key

    body = {
        "tenant_id": settings.tenant_id,
        "compatibility": {"contract": "quality-config-v1", "minimum_schema": "0012"},
        "source": settings.node_id,
        "kind": kind,
        "record": serialize(row),
        "published_at": current_time().isoformat(),
        "delivery": delivery,
    }
    if kind in INDUSTRIAL_CLASSES:
        if svc is None:
            fail(422, "industrial_package_context", "工业配置签名须先核对实际完整依赖")
        from .industrial_distribution import extension

        body["industrial_config"] = extension(svc, kind, row)
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return {
        "body": body,
        "sha256": digest(encoded),
        "signature": base64.b64encode(private_key(settings).sign(encoded)).decode(),
        "public_key": public_key(settings),
    }


def preview(svc, package):
    from .sync import verify_package

    verified = verify_package(svc.settings, package)
    body, record = package["body"], package["body"]["record"]
    check_scope(svc, record, kind=body["kind"])
    delivery = body.get("delivery")
    if not delivery:
        fail(422, "delivery_required", "管理导入需要含目标节点及有效期的分发包")
    required = {
        "id",
        "target_node",
        "site_id",
        "action",
        "generation",
        "expires_at",
        "reason",
        "expected_hash",
    }
    if set(delivery) != required:
        fail(422, "delivery_schema", "分发元数据字段无效")
    if delivery["target_node"] != svc.settings.node_id or delivery["site_id"] != svc.settings.site_id:
        fail(403, "delivery_target", "分发包目标节点或工厂不匹配")
    if (
        delivery["action"] not in {"publish", "retire", "rollback"}
        or type(delivery["generation"]) is not int
        or delivery["generation"] < 1
    ):
        fail(422, "delivery_schema", "分发操作或序列无效")
    try:
        expires = aware(datetime.fromisoformat(delivery["expires_at"]))
    except (ValueError, TypeError):
        fail(422, "delivery_time", "分发请求有效期格式无效")
    if expires <= current_time():
        fail(409, "delivery_expired", "分发请求已过期，恢复网络后不能自动生效")
    existing = svc.db.scalar(
        svc.scoped(CLASSES[body["kind"]]).where(CLASSES[body["kind"]].id == record["id"])
    )
    raw_existing = svc.db.get(CLASSES[body["kind"]], record["id"])
    if raw_existing is not None and existing is None:
        fail(403, "distribution_scope", "相同标识的本地记录不在授权范围")
    before = serialize(existing) if existing else {}
    delta = {
        key: {"before": before.get(key), "after": value}
        for key, value in record.items()
        if before.get(key) != value and key not in {"status", "previous_id"}
    }
    conflict = bool(existing and content_hash(before) != content_hash(record))
    expected = delivery.get("expected_hash")
    if expected and content_hash(before) != expected:
        conflict = True
    dependency_errors = []
    if body["kind"] in INDUSTRIAL_CLASSES:
        from .industrial_distribution import dependency_errors as check_dependencies

        dependency_errors = check_dependencies(svc, body)
    return {
        **verified,
        "delivery": delivery,
        "diff": delta,
        "local_status": before.get("status", "absent"),
        "conflict": conflict,
        "dependency_errors": dependency_errors,
        "content_hash": content_hash(record),
        "requires_local_review": True,
        "effective_status": "retired" if delivery["action"] == "retire" else "published",
        "auto_bind": False,
    }


def stage(svc, package):
    checked = preview(svc, package)
    body, delivery = package["body"], package["body"]["delivery"]
    prior = svc.db.get(m.Distribution, delivery["id"])
    if prior:
        if prior.tenant_id != svc.settings.tenant_id or digest(prior.package) != digest(package):
            fail(409, "distribution_conflict", "分发标识已经用于不同内容")
        check_scope(svc, body["record"], kind=body["kind"])
        return prior
    row = svc.add(
        m.Distribution,
        id=delivery["id"],
        target_node=delivery["target_node"],
        author_id=body["record"]["author_id"],
        kind=body["kind"],
        record_id=body["record"]["id"],
        action=delivery["action"],
        generation=delivery["generation"],
        package=package,
        expires_at=datetime.fromisoformat(delivery["expires_at"]),
        status="received",
        scope_line_id=body["record"].get("scope_line_id", ""),
        result={"status": "received", "sha256": package["sha256"], "preview_conflict": checked["conflict"]},
    )
    svc.audit("distribution.received", row.id, {"sha256": package["sha256"]})
    return row


def receive(svc, packages):
    results = []
    for package in packages:
        delivery = package.get("body", {}).get("delivery", {})
        try:
            with svc.db.begin_nested():
                row = stage(svc, package)
                result = {"status": row.status, **row.result}
                if row.status == "received" and aware(row.expires_at) <= current_time():
                    row.status = "expired"
                    row.result = {"status": "expired", "reason": "expired_before_local_review"}
                    result = row.result
        except Exception as exc:
            from fastapi import HTTPException

            code = (
                exc.detail.get("code", "invalid")
                if isinstance(exc, HTTPException) and isinstance(exc.detail, dict)
                else type(exc).__name__
            )
            result = {"status": "expired" if code == "delivery_expired" else "rejected", "reason": code}
        results.append({"id": delivery.get("id", ""), "result": result})
    return results


def outbound(svc, node_id):
    rows = svc.db.scalars(
        svc.scoped(m.Distribution)
        .where(m.Distribution.target_node == node_id, m.Distribution.status.in_(["queued", "received"]))
        .order_by(m.Distribution.created_at)
        .limit(100)
    ).all()
    for row in rows:
        if aware(row.expires_at) <= current_time():
            row.status, row.result = "expired", {"status": "expired", "reason": "center_ttl"}
    return [row.package for row in rows if row.status in {"queued", "received"}]


def acknowledge(svc, node_id, entries):
    for entry in entries:
        row = svc.db.scalar(
            svc.scoped(m.Distribution)
            .where(m.Distribution.id == entry["id"], m.Distribution.target_node == node_id)
            .with_for_update()
        )
        if not row:
            fail(404, "distribution_missing", "分发确认记录不存在")
        result = entry["result"]
        if result.get("status") not in TERMINAL | {"received"}:
            fail(422, "distribution_ack", "未知分发确认状态")
        if (
            row.status == "expired"
            and result["status"] == "applied"
            and result.get("applied_at")
            and aware(datetime.fromisoformat(result["applied_at"])) <= aware(row.expires_at)
        ):
            row.status, row.result = "applied", result
            svc.audit("distribution.late_ack", row.id, {"applied_before_ttl": True})
            continue
        if row.status in TERMINAL:
            if row.result != result and not (row.status == "expired" and result["status"] == "expired"):
                fail(409, "distribution_ack_conflict", "终态分发确认不能覆盖")
            continue
        row.status, row.result = result["status"], result
        svc.audit("distribution.acknowledged", row.id, {"status": row.status})


def apply_record(svc, row):
    checked = preview(svc, row.package)
    if checked["conflict"]:
        fail(409, "package_conflict", "本地相同版本内容或预期基线不同，必须人工解决，禁止覆盖")
    if checked["dependency_errors"] and row.action != "retire":
        fail(409, "industrial_dependencies", "工业配置依赖未就绪或已变化，不能应用")
    newer = svc.db.scalar(
        svc.scoped(m.Distribution)
        .where(
            m.Distribution.target_node == row.target_node,
            m.Distribution.kind == row.kind,
            m.Distribution.record_id == row.record_id,
            m.Distribution.generation > row.generation,
        )
        .limit(1)
    )
    if newer:
        fail(409, "delivery_stale", "已有更新分发指令，请核对最新版本")
    record = row.package["body"]["record"]
    cls = CLASSES[row.kind]
    local = svc.db.scalar(svc.scoped(cls).where(cls.id == row.record_id).with_for_update())
    if row.action == "retire":
        if local:
            local.status = "retired"
    else:
        if row.kind in INDUSTRIAL_CLASSES:
            if svc.actor.id == record["author_id"]:
                fail(409, "industrial_distribution_independent", "工业配置作者不能自行批准本地分发生效")
            from .industrial_distribution import install

            local = install(svc, row.package["body"], local)
        elif local is None:
            values = {k: v for k, v in record.items() if k not in {"tenant_id", "site_id", "previous_id"}}
            values["created_at"] = datetime.fromisoformat(values["created_at"])
            if cls == m.Knowledge:
                values["valid_until"] = datetime.fromisoformat(values["valid_until"])
            local = svc.add(cls, site=record.get("site_id", ""), **values)
        local.status = "published"
    # Routing/recipe bindings remain a separately authorized explicit local action.
    row.status = "applied"
    row.result = {
        "status": "applied",
        "action": row.action,
        "record_id": row.record_id,
        "sha256": row.package["sha256"],
        "actor_id": svc.actor.id,
        "applied_at": current_time().isoformat(),
        "auto_bind": False,
    }
    svc.audit("distribution." + row.action, row.id, row.result)


def register_distribution(app, settings, service, command):
    @app.get("/api/distributions")
    def list_distributions(scope=Depends(get_scope), svc=Depends(service)):
        svc.actor.require("read")
        rows = svc.db.scalars(
            apply_scope(svc, svc.scoped(m.Distribution), m.Distribution, scope)
            .order_by(m.Distribution.created_at.desc())
            .limit(300)
        ).all()
        if svc.actor.lines:
            rows = [
                r
                for r in rows
                if (r.scope_line_id in svc.actor.lines)
                or (
                    r.package["body"]["record"].get("scope", {}).get("line_ids")
                    and not set(r.package["body"]["record"]["scope"]["line_ids"]) - set(svc.actor.lines)
                )
            ]
        return [serialize(r) for r in rows]

    @app.post("/api/distributions")
    def create_rollout(data: RolloutInput, request: Request, svc=Depends(service)):
        svc.actor.require("knowledge_review")
        if settings.node_role != "center":
            fail(409, "center_only", "分发任务由企业中心创建；本站可导入中心签名包")

        def apply():
            source = svc.get(CLASSES[data.kind], data.record_id)
            if not source.reviewer_id or source.status not in (
                {"published"} if data.action == "publish" else {"published", "retired", "rolled_back"}
            ):
                fail(409, "distribution_source", "来源必须是独立审核通过的正式版本")
            if (
                data.kind == "knowledge"
                and aware(source.valid_until) <= current_time()
                and data.action != "retire"
            ):
                fail(409, "package_expired", "过期知识不能发布或回退")
            result = []
            for target in dict.fromkeys(data.target_nodes):
                site = svc.db.scalar(svc.scoped(m.Site).where(m.Site.node_id == target).with_for_update())
                if not site or not site.active:
                    fail(409, "site_unregistered", "目标节点未注册、无权访问或已撤销")
                if settings.mode == "production" and site.data.get("schema_version") != "0012":
                    fail(409, "node_incompatible", "目标节点尚未确认0012；先升级、备份并完成节点版本握手")
                check_scope(svc, serialize(source), site.site_id, data.kind)
                if source.site_id not in ("", site.site_id) or (
                    data.kind == "knowledge"
                    and source.scope.get("factory_ids")
                    and site.site_id not in source.scope["factory_ids"]
                ):
                    fail(403, "package_scope", "来源版本不适用于目标工厂")
                generation = (
                    svc.db.scalar(
                        select(func.max(m.Distribution.generation)).where(
                            m.Distribution.tenant_id == settings.tenant_id,
                            m.Distribution.target_node == target,
                            m.Distribution.kind == data.kind,
                            m.Distribution.record_id == source.id,
                        )
                    )
                    or 0
                ) + 1
                row_id, expires = m.uid(), current_time() + timedelta(hours=data.expires_in_hours)
                delivery = {
                    "id": row_id,
                    "target_node": target,
                    "site_id": site.site_id,
                    "generation": generation,
                    "action": data.action,
                    "expires_at": expires.isoformat(),
                    "reason": data.reason,
                    "expected_hash": data.expected_hash,
                }
                package = build_package(settings, source, data.kind, delivery, svc)
                row = svc.add(
                    m.Distribution,
                    site=site.site_id,
                    id=row_id,
                    target_node=target,
                    author_id=svc.actor.id,
                    kind=data.kind,
                    record_id=source.id,
                    action=data.action,
                    generation=generation,
                    package=package,
                    expires_at=expires,
                    scope_line_id=source.scope_line_id,
                )
                svc.audit(
                    "distribution.queued",
                    row.id,
                    {"target": target, "action": data.action, "reason": data.reason},
                )
                result.append(serialize(row))
            return result

        return command(svc, request, data.model_dump(), apply)

    @app.post("/api/distributions/import/preview")
    def import_preview(data: dict, svc=Depends(service)):
        return preview(svc, data)

    @app.post("/api/distributions/import")
    def import_package(data: dict, request: Request, svc=Depends(service)):
        if settings.node_role != "site":
            fail(409, "site_only", "中心版本应走本级审核，分发包在目标站点导入")
        return command(svc, request, data, lambda: serialize(stage(svc, data)))

    @app.post("/api/distributions/{id}/preview")
    def row_preview(id: str, svc=Depends(service)):
        row = svc.get(m.Distribution, id)
        return preview(svc, row.package)

    @app.post("/api/distributions/{id}/decide")
    def decide(id: str, data: DecisionInput, request: Request, svc=Depends(service)):
        if settings.node_role != "site":
            fail(409, "site_only", "中心只跟踪站点办理回执")

        def apply():
            row = svc.get(m.Distribution, id, True)
            check_scope(svc, row.package["body"]["record"], kind=row.kind)
            if row.status in TERMINAL:
                fail(409, "distribution_terminal", "该分发已办理，请查看结果；新操作需要新分发版本")
            if aware(row.expires_at) <= current_time():
                row.status, row.result = (
                    "expired",
                    {"status": "expired", "reason": "expired_before_local_review"},
                )
            elif data.decision == "reject":
                row.status, row.result = (
                    "rejected",
                    {"status": "rejected", "reason": data.reason, "actor_id": svc.actor.id},
                )
            else:
                apply_record(svc, row)
                row.result = {**row.result, "reason": data.reason}
            svc.audit("distribution.decided", row.id, row.result)
            return serialize(row)

        return command(svc, request, data.model_dump(), apply)
