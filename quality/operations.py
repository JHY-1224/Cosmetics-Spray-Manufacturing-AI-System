"""Scoped operational alerts and explicit non-destructive retention archive."""

import json
import os
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, Request
from pydantic import Field
from sqlalchemy import func

from . import models as m
from .anomalies import AnomalyPolicy
from .schemas import Strict
from .security import aware, current_time, digest, fail
from .service import serialize
from .storage import Storage
from .querying import get_scope, apply_scope


class PolicyUpdate(Strict):
    version: int = Field(ge=1)
    policy: AnomalyPolicy


def industrial_metrics(svc, scope):
    """Read actual durable queue state without returning payloads or secret leases."""
    svc.actor.require("admin")
    if scope.product_id:
        return {"availability": "clear_product_filter", "reason": "运行队列按工厂、产线、工艺及建立时间汇总；请清除产品筛选"}
    now = current_time()
    result = {"availability": "measured", "checked_at": now.isoformat(), "scope": scope.model_dump(mode="json")}
    for name, cls, inflight, pending in (
        ("digital_tasks", m.DigitalTask, "running", {"queued", "running", "awaiting_approval", "paused"}),
        ("business_commands", m.BusinessCommand, "sending", {"queued", "sending", "accepted", "unknown", "partial"}),
    ):
        query = apply_scope(svc, svc.scoped(cls), cls, scope)
        rows = svc.db.execute(query.with_only_columns(cls.status, func.count(), func.min(cls.created_at)).group_by(cls.status)).all()
        stamps = [stamp for state, _, stamp in rows if state in pending]
        result[name] = {
            "by_status": {state: count for state, count, _ in rows},
            "expired_leases": svc.db.scalar(query.where(cls.status == inflight, cls.lease_until < now).with_only_columns(func.count())) or 0,
            "oldest_pending_age_seconds": round(max(0, (now - min(aware(stamp) for stamp in stamps)).total_seconds()), 3) if stamps else None,
        }
    states = result["business_commands"]["by_status"]
    result["business_commands"]["awaiting_source_confirmation"] = sum(states.get(state, 0) for state in ("accepted", "unknown", "partial")) + result["business_commands"]["expired_leases"]
    result["limits"] = {"digital_queue": svc.settings.digital_queue_limit, "digital_concurrency": svc.settings.digital_concurrency, "digital_timeout_seconds": svc.settings.digital_timeout, "business_queue": svc.settings.business_queue_limit, "business_timeout_seconds": svc.settings.business_timeout}
    result["external_result_authority"] = "signed_source_receipt; queue_success_is_not_external_applied"
    return result


def alerts(svc, scope=None):
    now, items = current_time(), []

    def add(code, level, target, message):
        items.append(
            {
                "code": code,
                "severity": level,
                "target_id": target,
                "message": message,
                "evaluated_at": now.isoformat(),
            }
        )

    def query(cls):
        return apply_scope(svc, svc.scoped(cls), cls, scope) if scope else svc.scoped(cls)
    adapters = svc.db.scalars(query(m.Adapter).where(m.Adapter.enabled.is_(True))).all()
    for row in adapters:
        last = row.health.get("last_success")
        if not last or now - __import__("datetime").datetime.fromisoformat(last) > timedelta(
            seconds=max(60, row.config.get("poll_seconds", 5) * 3)
        ):
            add("source_stale", "warning", row.id, row.name + " 尚无近期成功采集，请核对接入与网络")
        if row.health.get("status") in {"unavailable", "data_quality_error"}:
            add("source_error", "error", row.id, row.name + " 采集或数据质量异常")
    for state, count in svc.db.execute(query(m.RawRecord).with_only_columns(m.RawRecord.status, func.count()).group_by(m.RawRecord.status)):
        if state in {'quarantined','conflict','pending_association','received'} and count:
            add('raw_'+state, 'warning', '', f'{count} 条原始接收记录处于 {state}，在接入管理核对或重处理')
    dead = (
        svc.db.scalar(query(m.Outbox).where(m.Outbox.status == "dead").with_only_columns(func.count()))
        or 0
    )
    pending = (
        svc.db.scalar(
            query(m.Outbox)
            .where(m.Outbox.status == "pending", m.Outbox.created_at < now - timedelta(minutes=5))
            .with_only_columns(func.count())
        )
        or 0
    )
    if dead:
        add("event_dead", "error", "", f"{dead} 条死信待管理员核对重放")
    if pending:
        add("event_backlog", "warning", "", f"{pending} 条本地事件已积压超过5分钟")
    if svc.settings.center_url:
        count = (
            svc.db.scalar(
                query(m.Outbox)
                .where(m.Outbox.synced.is_(False), m.Outbox.created_at < now - timedelta(minutes=5))
                .with_only_columns(func.count())
            )
            or 0
        )
        if count:
            add("sync_backlog", "warning", "", f"{count} 条中心补传已等待超过5分钟，本站业务按授权继续")
    bad = (
        svc.db.scalar(
            query(m.Evidence)
            .where(m.Evidence.replication_status == "conflict")
            .with_only_columns(func.count())
        )
        or 0
    )
    if bad:
        add("evidence_conflict", "error", "", f"{bad} 份证据哈希或远端对象冲突，禁止覆盖")
    used = (
        svc.db.scalar(query(m.Evidence).with_only_columns(func.coalesce(func.sum(m.Evidence.size), 0)))
        or 0
    )
    if used >= svc.settings.cache_max_bytes * svc.settings.warning_ratio:
        add(
            "storage_capacity",
            "error" if used >= svc.settings.cache_max_bytes else "warning",
            "",
            "授权证据量达到容量预警，请执行独立备份、归档并由运维扩容",
        )
    for run in svc.db.scalars(query(m.Run).order_by(m.Run.created_at.desc()).limit(100)).all():
        status = run.data.get("model_status")
        if status in {"unknown", "unavailable", "budget_exhausted"}:
            add("model_" + status, "warning", run.id, "模型调用状态需人工核对，规则和人工业务仍可使用")
    return {
        "alerts": items,
        "rules_version": "operations-v1",
        "notification_channel": "工作台与管理员指标；外部通知端点待客户配置",
        "retention": {"days": svc.settings.retention_days, "action": "显式归档保留副本，不自动删除原始记录"},
    }


def archive(settings, destination):
    """Copy expired evidence and metadata to a new archive, preserving every original."""
    from .db import Database
    from .security import Actor
    from .service import Service

    target = Path(destination).resolve()
    if target.exists() or target.is_relative_to(settings.data_dir.resolve()):
        raise ValueError("归档目标须为节点数据目录之外的新目录，拒绝覆盖")
    target.mkdir(parents=True, mode=0o700)
    with Database(settings).session() as db:
        svc = Service(db, settings, Actor("archive-operator", "admin", settings.sites))
        rows = db.scalars(
            svc.scoped(m.Evidence).where(
                m.Evidence.created_at < current_time() - timedelta(days=settings.retention_days)
            )
        ).all()
        store, manifest = (
            Storage(settings),
            {
                "tenant_id": settings.tenant_id,
                "site_id": settings.site_id,
                "retention_days": settings.retention_days,
                "objects": [],
                "source_deleted": False,
            },
        )
        for row in rows:
            data = store.get(row.object_key, row.sha256)
            path = target / "objects" / row.object_key
            if not path.resolve().is_relative_to(target):
                raise ValueError("非法归档对象路径")
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            with path.open("xb") as out:
                os.chmod(path, 0o600)
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            if digest(path.read_bytes()) != row.sha256:
                raise ValueError("归档对象校验失败")
            manifest["objects"].append(serialize(row))
        output = target / "archive-manifest.json"
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        output.chmod(0o600)
        svc.audit(
            "retention.archived",
            "",
            {"object_count": len(rows), "manifest_hash": digest(manifest), "source_deleted": False},
        )
    return {"archived": len(rows), "manifest_hash": digest(manifest), "source_deleted": False}


def register_operations(app, service, command):
    @app.get("/api/operations/alerts")
    def get_alerts(scope=Depends(get_scope), svc=Depends(service)):
        svc.actor.require("read")
        return alerts(svc, scope)

    @app.post("/api/lines/{id}/anomaly-policy")
    def update_policy(id: str, data: PolicyUpdate, request: Request, svc=Depends(service)):
        svc.actor.require("master", svc.settings.site_id, id)

        def apply():
            row = svc.get(m.Master, id, True)
            if row.kind != "line":
                fail(422, "line_required", "阈值只能绑定实际产线")
            if row.version != data.version:
                fail(409, "version_conflict", "产线配置已变更，请刷新")
            row.data = {**row.data, "anomaly_threshold": data.policy.model_dump()}
            row.version += 1
            svc.audit("anomaly.policy_updated", row.id, {"policy": data.policy.model_dump()})
            return serialize(row)

        return command(svc, request, data.model_dump(), apply)
