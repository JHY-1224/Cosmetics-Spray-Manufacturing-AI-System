from .http_control import client as controlled_client
import base64
import hmac
import json
from datetime import timedelta

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import Depends, Request, Response
from sqlalchemy import func, select

from .querying import get_scope, apply_scope
from . import models as m
from .schemas import ApprovalInput
from .security import Actor, aware, current_time, digest, endpoint, fail, sign, token
from .service import Service, serialize
from .identity import authorization_snapshot, apply_authorizations, validate_identity
from . import distribution


def private_key(settings):
    if not settings.package_secret:
        fail(409, "signing_key_missing", "未配置中心发布签名私钥种子")
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(digest(settings.package_secret)))


def public_key(settings):
    return base64.b64encode(
        private_key(settings)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()


def register_sync(app, settings, database, service, command):
    def site_identity(request, db):
        if settings.node_role != "center":
            fail(404, "center_only", "当前不是企业中心")
        raw = request.headers.get("authorization", "").removeprefix("Bearer ")
        site = db.scalar(
            select(m.Site).where(
                m.Site.tenant_id == settings.tenant_id,
                m.Site.token_hash == digest(raw),
                m.Site.active.is_(True),
            )
        )
        if not raw or not site:
            fail(401, "site_auth", "站点身份已失效或未注册")
        return site, raw

    @app.get("/api/sites")
    def sites(scope=Depends(get_scope), svc: Service = Depends(service)):
        if scope.site_id:
            svc.actor.require("read", scope.site_id)
        query = svc.scoped(m.Site)
        if scope.site_id:
            query = query.where(m.Site.site_id == scope.site_id)
        return [serialize(r) for r in svc.db.scalars(query).all()]

    @app.post("/api/sites")
    def register(data: dict, request: Request, svc: Service = Depends(service)):
        svc.actor.require("admin")
        if settings.node_role != "center":
            fail(409, "center_only", "站点在企业中心注册")

        def apply():
            if (
                set(data) - {"node_id", "site_id", "name", "base_url"}
                or not {"node_id", "site_id", "name"} <= set(data)
                or data["site_id"] not in svc.actor.sites
            ):
                fail(422, "site_binding", "注册需明确已授权工厂、节点和名称")
            base_url = data.get("base_url", "").rstrip("/")
            if base_url:
                endpoint(base_url, settings)
                if settings.mode == "production" and not base_url.startswith("https://"):
                    fail(422, "endpoint_tls", "生产站点地址必须使用HTTPS")
            raw = token()
            row = svc.add(
                m.Site,
                site=data["site_id"],
                node_id=data["node_id"],
                name=data["name"],
                token_hash=digest(raw),
                data={"base_url": base_url},
            )
            svc.audit("site.registered", row.id)
            return {
                "id": row.id,
                "node_id": row.node_id,
                "token": raw,
                "package_public_key": public_key(settings) if settings.package_secret else "",
            }

        return command(svc, request, data, apply)

    @app.post("/api/sites/{id}/endpoint")
    def site_endpoint(id: str, data: dict, request: Request, svc: Service = Depends(service)):
        svc.actor.require("admin")

        def apply():
            row = svc.get(m.Site, id, True)
            if set(data) != {"base_url"}:
                fail(422, "endpoint_fields", "仅接受受控站点base_url")
            value = data["base_url"].rstrip("/")
            endpoint(value, settings)
            if settings.mode == "production" and not value.startswith("https://"):
                fail(422, "endpoint_tls", "生产跨站点证据必须使用HTTPS")
            row.data = {**row.data, "base_url": value}
            svc.audit("site.endpoint_configured", row.id)
            return serialize(row)

        return command(svc, request, data, apply)

    @app.post("/api/sites/{id}/revoke")
    def revoke(id: str, request: Request, svc: Service = Depends(service)):
        svc.actor.require("admin")

        def apply():
            site = svc.get(m.Site, id, True)
            site.active = False
            svc.audit("site.revoked", id)
            return {"revoked": True, "offline_limit": "本地授权在到期前不能即时感知中心撤销；需现场应急禁用"}

        return command(svc, request, {}, apply)

    @app.post("/api/sites/{id}/rotate-token")
    def rotate_site_token(id: str, data: dict, request: Request, svc: Service = Depends(service)):
        svc.actor.require("admin")
        if (
            set(data) != {"reason"}
            or not isinstance(data.get("reason"), str)
            or not 4 <= len(data["reason"]) <= 2000
        ):
            fail(422, "rotation_reason", "轮换须填写4至2000字依据")

        def apply():
            site = svc.get(m.Site, id, True)
            raw = token()
            site.token_hash = digest(raw)
            site.data = {**site.data, "token_rotated_at": current_time().isoformat()}
            svc.audit("site.token_rotated", id, {"reason": data["reason"]})
            return {"id": id, "token": raw, "requires_manual_secret_update": True}

        return command(svc, request, data, apply)

    @app.post("/api/sync/push")
    def push(data: dict, request: Request):
        # Registration identity is looked up with center's configured service scope, then every
        # envelope is rebound to that factory. Payload tenant/site fields are never authoritative.
        with database.session(settings.sites or ["*"]) as db:
            site, raw = site_identity(request, db)
            body = data.get("body", {})
            if not hmac.compare_digest(sign(body, raw), data.get("signature", "")):
                fail(403, "sync_signature", "同步签名无效")
            if body.get("source") != site.node_id or body.get("target") != settings.node_id:
                fail(403, "sync_binding", "来源或目标节点不匹配注册身份")
            schema_version = body.get("schema_version")
            if schema_version is not None and schema_version not in {"0009", "0010", "0011", "0012"}:
                fail(409, "node_incompatible", "节点数据库/事件合同版本不兼容；暂停补传并核对升级窗口")
            site.data = {
                **site.data,
                "schema_version": schema_version or "unknown",
                "schema_reported_at": current_time().isoformat(),
            }
            items = body.get("events", [])
            if not isinstance(items, list) or len(items) > 200:
                fail(422, "sync_batch", "同步批次超过上限")
            accepted, duplicates, conflicts = [], [], []
            for event in items:
                prior = db.scalar(
                    select(m.Inbox).where(
                        m.Inbox.tenant_id == settings.tenant_id,
                        m.Inbox.source_node == site.node_id,
                        m.Inbox.event_id == event["id"],
                    )
                )
                if prior:
                    if prior.payload != event:
                        conflicts.append(event["id"])
                    else:
                        duplicates.append(event["id"])
                    continue
                expected = site.sequence + 1
                if event.get("sequence") != expected:
                    conflicts.append(event["id"])
                    break
                db.add(
                    m.Inbox(
                        tenant_id=settings.tenant_id,
                        site_id=site.site_id,
                        source_node=site.node_id,
                        scope_line_id=event.get("payload", {}).get("line_id", ""),
                        event_id=event["id"],
                        sequence=event["sequence"],
                        payload=event,
                    )
                )
                payload = event.get("payload", {})
                fact = payload.get("fact") if event.get("kind") == "inspection.recorded" else None
                if fact:
                    from .schemas import InspectionInput

                    try:
                        InspectionInput.model_validate(
                            {k: v for k, v in fact["result"].items() if k in InspectionInput.model_fields}
                        )
                    except Exception:
                        fail(422, "sync_fact", "质量事实结构无效")
                    if (
                        fact.get("id") != payload.get("id")
                        or fact.get("line_id") != payload.get("line_id")
                        or fact.get("site_id") != site.site_id
                        or fact.get("tenant_id") != settings.tenant_id
                    ):
                        fail(403, "sync_fact_scope", "质量事实与认证站点、产线或记录标识不一致")
                if event["kind"] == "case.changed":
                    old = db.scalar(
                        select(m.Snapshot).where(
                            m.Snapshot.tenant_id == settings.tenant_id,
                            m.Snapshot.source_node == site.node_id,
                            m.Snapshot.entity_id == payload["id"],
                        )
                    )
                    if old and payload["version"] <= old.version:
                        conflicts.append(event["id"])
                        db.rollback()
                        fail(409, "snapshot_conflict", "站点记录版本倒退或重复版本，拒绝覆盖")
                    if old:
                        old.version, old.data = payload["version"], payload
                    else:
                        db.add(
                            m.Snapshot(
                                scope_line_id=payload.get("line_id", ""),
                                tenant_id=settings.tenant_id,
                                site_id=site.site_id,
                                source_node=site.node_id,
                                entity_id=payload["id"],
                                version=payload["version"],
                                data=payload,
                            )
                        )
                site.sequence = event["sequence"]
                accepted.append(event["id"])
            site.last_seen, site.acknowledged = current_time(), site.sequence
            return {
                "accepted": accepted,
                "duplicates": duplicates,
                "conflicts": conflicts,
                "ack_sequence": site.sequence,
                "received_at": current_time().isoformat(),
                "received_count": site.sequence,
            }

    @app.get("/api/site-snapshots")
    def snapshots(scope=Depends(get_scope), svc: Service = Depends(service)):
        return [
            serialize(r)
            for r in svc.db.scalars(apply_scope(svc, svc.scoped(m.Snapshot), m.Snapshot, scope)).all()
        ]

    @app.get("/api/remote-requests")
    def remote_list(scope=Depends(get_scope), svc: Service = Depends(service)):
        return [
            serialize(r)
            for r in svc.db.scalars(
                apply_scope(svc, svc.scoped(m.RemoteRequest), m.RemoteRequest, scope)
                .order_by(m.RemoteRequest.created_at.desc())
                .limit(100)
            ).all()
        ]

    @app.get("/api/site-snapshots/{id}/files/{file_id}")
    def center_file(id: str, file_id: str, svc: Service = Depends(service)):
        snapshot = svc.get(m.Snapshot, id)
        if file_id not in [r["id"] for r in snapshot.data.get("evidence_refs", [])]:
            fail(404, "evidence_scope", "证据不属于已授权案件快照")
        registration = svc.db.scalar(
            svc.scoped(m.Site).where(m.Site.node_id == snapshot.source_node, m.Site.active.is_(True))
        )
        if not registration or not registration.data.get("base_url"):
            fail(409, "site_endpoint_missing", "站点尚未配置受控访问地址；证据引用已保存")
        base = endpoint(registration.data["base_url"], settings)
        body = {
            "purpose": "evidence_read_v1",
            "tenant_id": settings.tenant_id,
            "site_id": snapshot.site_id,
            "node_id": snapshot.source_node,
            "line_id": snapshot.scope_line_id,
            "file_id": file_id,
            "actor_id": svc.actor.id,
            "expires_at": (current_time() + timedelta(seconds=45)).isoformat(),
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        signed = {"body": body, "signature": base64.b64encode(private_key(settings).sign(encoded)).decode()}
        try:
            with controlled_client(settings, base, 15) as client:
                response = client.post(base + "/api/center/evidence", json=signed)
                if response.status_code != 200:
                    fail(409, "remote_evidence_unavailable", "站点证据暂不可用或授权已变化，请查看本站记录")
                if len(response.content) > settings.max_upload_bytes:
                    fail(413, "remote_evidence_size", "远程证据超过大小限制")
                reference = next(r for r in snapshot.data["evidence_refs"] if r["id"] == file_id)
                if digest(response.content) != reference["sha256"]:
                    fail(409, "remote_evidence_integrity", "远程证据与原快照哈希不一致")
                svc.audit("center.evidence_read", snapshot.id, {"evidence_id": file_id})
                return Response(
                    response.content,
                    media_type=reference["media_type"],
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
                )
        except httpx.HTTPError:
            fail(503, "site_offline", "站点离线；中心快照仍可查看，原始证据待站点恢复")

    @app.post("/api/center/evidence")
    def site_evidence(data: dict):
        from datetime import datetime
        from .storage import Storage

        if settings.node_role != "site" or not settings.package_public_key:
            fail(403, "center_trust", "没有可信中心证书")
        body = data.get("body", {})
        try:
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            Ed25519PublicKey.from_public_bytes(base64.b64decode(settings.package_public_key)).verify(
                base64.b64decode(data["signature"]), encoded
            )
            expires = aware(datetime.fromisoformat(body["expires_at"]))
        except Exception:
            fail(403, "center_signature", "中心证据请求签名或有效期无效")
        if (
            body.get("purpose") != "evidence_read_v1"
            or body.get("tenant_id") != settings.tenant_id
            or body.get("site_id") != settings.site_id
            or body.get("node_id") != settings.node_id
            or not current_time() < expires <= current_time() + timedelta(seconds=60)
        ):
            fail(403, "center_scope", "中心证据请求不属于本站或已经过期")
        with database.session([settings.site_id], [body["line_id"]]) as db:
            svc = Service(
                db, settings, Actor(body["actor_id"], "viewer", [settings.site_id], lines=[body["line_id"]])
            )
            row = svc.get(m.Evidence, body["file_id"])
            if row.scope_line_id != body["line_id"]:
                fail(403, "center_line_scope", "证据不属于请求产线")
            raw = Storage(settings).get(row.object_key, row.sha256)
            svc.audit("evidence.center_read", row.id)
            return Response(
                raw,
                media_type=row.media_type,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

    @app.post("/api/remote-requests")
    def remote(data: dict, request: Request, svc: Service = Depends(service)):
        svc.actor.require("approve")

        def apply():
            target = svc.get(m.Site, data.get("site_registration_id", ""))
            svc.actor.require("approve", target.site_id)
            if settings.node_role != "center" or data.get("kind") != "approve":
                fail(422, "remote_action", "首期中心委托只支持待站点复核的方案审批")
            try:
                payload = ApprovalInput.model_validate(data["payload"]).model_dump()
            except Exception:
                fail(422, "remote_schema", "审批请求结构无效")
            case_id = str(data.get("case_id", ""))
            snapshot = svc.db.scalar(
                svc.scoped(m.Snapshot).where(
                    m.Snapshot.entity_id == case_id, m.Snapshot.source_node == target.node_id
                )
            )
            if not snapshot:
                fail(404, "case_snapshot_scope", "没有此站点案件的授权快照")
            svc.actor.require("approve", target.site_id, snapshot.scope_line_id)
            if snapshot.version != payload["version"] or snapshot.data.get("status") != "awaiting_approval":
                fail(409, "remote_stale", "中心快照已变化或案件不处于待审核")
            row = svc.add(
                m.RemoteRequest,
                site=target.site_id,
                scope_line_id=snapshot.scope_line_id,
                author_id=svc.actor.id,
                target_node=target.node_id,
                kind="approve",
                payload={**payload, "case_id": case_id},
                expires_at=current_time() + timedelta(minutes=15),
            )
            svc.audit("remote.requested", row.id)
            return serialize(row)

        return command(svc, request, data, apply)

    @app.get("/api/sync/pull")
    def pull(request: Request):
        with database.session(["*"]) as db:
            site, raw = site_identity(request, db)
            rows = db.scalars(
                select(m.RemoteRequest)
                .where(
                    m.RemoteRequest.tenant_id == settings.tenant_id,
                    m.RemoteRequest.target_node == site.node_id,
                    m.RemoteRequest.status == "pending",
                )
                .order_by(m.RemoteRequest.created_at)
                .limit(100)
            ).all()
            body = {
                "source": settings.node_id,
                "target": site.node_id,
                "requests": [serialize(r) for r in rows],
                "authorizations": authorization_snapshot(db, settings, site),
                "distributions": distribution.outbound(
                    Service(db, settings, Actor("distribution-pull", "admin", [site.site_id])), site.node_id
                ),
            }
            return {"body": body, "signature": sign(body, raw)}

    @app.post("/api/sync/ack")
    def ack(data: dict, request: Request):
        with database.session(["*"]) as db:
            site, raw = site_identity(request, db)
            if not hmac.compare_digest(sign(data.get("body", {}), raw), data.get("signature", "")):
                fail(403, "sync_signature", "确认签名错误")
            for entry in data["body"].get("results", []):
                row = db.scalar(
                    select(m.RemoteRequest).where(
                        m.RemoteRequest.id == entry["id"],
                        m.RemoteRequest.tenant_id == settings.tenant_id,
                        m.RemoteRequest.target_node == site.node_id,
                    )
                )
                if not row:
                    fail(404, "request_missing", "远程请求不存在")
                if row.status != "pending" and row.result != entry["result"]:
                    fail(409, "ack_conflict", "已确认请求不能被覆盖")
                row.status, row.result = "acknowledged", entry["result"]
            distribution.acknowledge(
                Service(db, settings, Actor("distribution-ack", "admin", [site.site_id])),
                site.node_id,
                data["body"].get("distributions", []),
            )
            return {
                "confirmed": len(data["body"].get("results", [])),
                "distributions": len(data["body"].get("distributions", [])),
            }

    @app.get("/api/packages/{kind}/{id}")
    def package(kind: str, id: str, svc: Service = Depends(service)):
        svc.actor.require("knowledge_review")
        if settings.node_role != "center":
            fail(409, "center_only", "正式分发包由中心发布")
        cls = {"route": m.Route, "knowledge": m.Knowledge}.get(kind)
        if not cls:
            fail(422, "package_kind", "未知发布类型")
        row = svc.get(cls, id)
        if row.status != "published":
            fail(409, "unpublished", "只能分发已发布版本")
        body = {
            "tenant_id": settings.tenant_id,
            "compatibility": {"contract": "quality-config-v1", "minimum_schema": "0012"},
            "source": settings.node_id,
            "kind": kind,
            "record": serialize(row),
            "published_at": current_time().isoformat(),
        }
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        return {
            "body": body,
            "sha256": digest(encoded),
            "signature": base64.b64encode(private_key(settings).sign(encoded)).decode(),
            "public_key": public_key(settings),
        }

    @app.post("/api/packages/preview")
    def preview(data: dict, svc: Service = Depends(service)):
        svc.actor.require("knowledge_review")
        return verify_package(settings, data)

    @app.post("/api/packages/apply")
    def apply_package(data: dict, request: Request, svc: Service = Depends(service)):
        svc.actor.require("knowledge_review")

        def apply():
            result = verify_package(settings, data)
            body = data["body"]
            cls = m.Route if body["kind"] == "route" else m.Knowledge
            record = body["record"]
            managed = svc.db.scalar(
                svc.scoped(m.Distribution).where(m.Distribution.record_id == record["id"]).limit(1)
            )
            if managed:
                fail(409, "managed_distribution", "受分发治理的版本请通过分发管理办理，不能绕过停用或回退")
            existing = svc.db.get(cls, record["id"])
            if existing:
                if digest(serialize(existing)) != digest(record):
                    fail(409, "package_conflict", "相同ID内容不同，拒绝静默覆盖")
                return {"id": existing.id, "already_applied": True}
            values = {k: v for k, v in record.items() if k not in ("tenant_id", "site_id", "previous_id")}
            from datetime import datetime

            values["created_at"] = datetime.fromisoformat(values["created_at"])
            if cls == m.Knowledge:
                values["valid_until"] = __import__("datetime").datetime.fromisoformat(values["valid_until"])
            row = svc.add(cls, site=record.get("site_id", ""), **values)
            svc.audit("package.applied", row.id, {"sha256": data["sha256"], **result})
            return {"id": row.id, "status": row.status}

        return command(svc, request, data, apply)


def verify_package(settings, data):
    body = data.get("body", {})
    if not isinstance(body, dict) or not isinstance(body.get("record"), dict):
        fail(422, "package_schema", "发布包必须包含对象类型的 body 和 record")
    compatibility = body.get("compatibility")
    if compatibility is not None and (
        not isinstance(compatibility, dict)
        or compatibility.get("contract") != "quality-config-v1"
        or compatibility.get("minimum_schema") not in {"0009", "0010", "0011", "0012"}
    ):
        fail(409, "package_incompatible", "配置包合同或最低数据库版本不兼容，请先核对节点升级顺序")
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    if (
        body.get("tenant_id") != settings.tenant_id
        or digest(encoded) != data.get("sha256")
        or body.get("kind") not in ("route", "knowledge", "definition", "formulation", "policy", "bom")
    ):
        fail(422, "package_integrity", "知识包企业、类型或完整性校验失败")
    if not settings.package_public_key:
        fail(409, "trust_missing", "未配置可信中心公钥；不能信任包自带公钥")
    record = body.get("record", {})
    if record.get("site_id") not in ("", settings.site_id):
        fail(403, "package_scope", "发布包不属于当前工厂，不得扩大适用范围")
    if record.get("scope", {}).get("factory_ids") and settings.site_id not in record["scope"]["factory_ids"]:
        fail(403, "package_scope", "发布包未适用于当前工厂")
    if record.get("valid_until") and body.get("delivery", {}).get("action") != "retire":
        from datetime import datetime

        try:
            expired = aware(datetime.fromisoformat(record["valid_until"])) <= current_time()
        except (ValueError, TypeError):
            fail(422, "package_time", "发布包有效期格式无效")
        if expired:
            fail(409, "package_expired", "发布知识已经失效")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(settings.package_public_key)).verify(
            base64.b64decode(data["signature"]), encoded
        )
    except Exception:
        fail(403, "package_signature", "知识包签名无效")
    from .industrial_distribution import CLASSES as INDUSTRIAL_CLASSES

    cls = INDUSTRIAL_CLASSES.get(body["kind"]) or (m.Route if body["kind"] == "route" else m.Knowledge)
    fields = set(cls.__table__.columns.keys())
    if set(record) != fields or record.get("tenant_id") != settings.tenant_id:
        fail(422, "package_record", "发布记录字段或企业绑定无效")
    allowed = (
        {"published"}
        if body.get("delivery", {}).get("action", "publish") == "publish"
        else {"published", "retired", "rolled_back"}
    )
    if (
        record.get("status") not in allowed
        or not record.get("reviewer_id")
        or record["reviewer_id"] == record.get("author_id")
    ):
        fail(409, "package_unreviewed", "发布包缺少独立审核的正式版本")
    if body["kind"] == "knowledge":
        from .agent import validate_rules

        validate_rules(record["rules"])
    elif body["kind"] in INDUSTRIAL_CLASSES:
        if compatibility != {"contract": "quality-config-v1", "minimum_schema": "0012"}:
            fail(409, "industrial_package_compatibility", "工业配置需要0012合同，不能向旧节点降级导入")
        from .industrial_distribution import validate_package

        validate_package(body)
    return {
        "verified": True,
        "kind": body["kind"],
        "revision": body["record"]["revision"],
        "changes": body["record"],
        "auto_bind": False,
    }


def sync_once(settings, database):
    endpoint(settings.center_url, settings)
    with database.session() as db:
        svc = Service(db, settings, Actor("sync-worker", "admin", [settings.site_id]))
        rows = db.scalars(
            svc.scoped(m.Outbox)
            .where(m.Outbox.sequence == 0)
            .order_by(m.Outbox.created_at, m.Outbox.id)
            .with_for_update()
        ).all()
        maximum = db.scalar(select(func.max(m.Outbox.sequence))) or 0
        for i, row in enumerate(rows):
            row.sequence = maximum + i + 1
        db.flush()
        pending = db.scalars(
            svc.scoped(m.Outbox)
            .where(m.Outbox.sequence > 0, m.Outbox.synced.is_(False))
            .order_by(m.Outbox.sequence)
            .limit(200)
        ).all()
        events = [
            {
                "id": r.id,
                "sequence": r.sequence,
                "kind": r.kind,
                "payload": r.payload,
                "occurred_at": aware(r.created_at).isoformat(),
            }
            for r in pending
        ]
        body = {
            "source": settings.node_id,
            "target": "center",
            "schema_version": "0012",
            "events": events,
            "sent_at": current_time().isoformat(),
        }
        with controlled_client(settings, settings.center_url, 15) as client:
            headers = {"Authorization": f"Bearer {settings.sync_token}"}
            response = client.post(
                settings.center_url + "/api/sync/push",
                headers=headers,
                json={"body": body, "signature": sign(body, settings.sync_token)},
            )
            response.raise_for_status()
            ack = response.json()
            for r in pending:
                if r.id in ack["accepted"] + ack["duplicates"]:
                    r.synced = True
            pulled = client.get(settings.center_url + "/api/sync/pull", headers=headers)
            pulled.raise_for_status()
            envelope = pulled.json()
            if (
                not hmac.compare_digest(sign(envelope["body"], settings.sync_token), envelope["signature"])
                or envelope["body"]["target"] != settings.node_id
            ):
                raise ValueError("pull_identity_or_signature")
            results = []
            authorization_result = apply_authorizations(svc, envelope["body"].get("authorizations"))
            distribution_results = distribution.receive(svc, envelope["body"].get("distributions", []))
            received_ids = {entry["id"] for entry in distribution_results}
            distribution_results += [
                {"id": row.id, "result": row.result}
                for row in db.scalars(
                    svc.scoped(m.Distribution).where(
                        m.Distribution.target_node == settings.node_id,
                        m.Distribution.status.in_(distribution.TERMINAL),
                    )
                ).all()
                if row.id not in received_ids
            ]
            for remote in envelope["body"]["requests"]:
                prior = db.scalar(
                    svc.scoped(m.Command).where(m.Command.actor_id == "remote", m.Command.key == remote["id"])
                )
                if prior:
                    results.append({"id": remote["id"], "result": prior.response})
                    continue
                from datetime import datetime

                result = {"status": "rejected", "reason": "delegation_missing"}
                local_id = settings.center_delegations.get(remote["author_id"], remote["author_id"])
                local_user = db.get(m.User, local_id) if local_id else None
                if (
                    local_user
                    and local_user.auth_origin != "center"
                    and remote["author_id"] not in settings.center_delegations
                ):
                    local_user = None
                if datetime.fromisoformat(remote["expires_at"]) <= current_time():
                    result = {"status": "rejected", "reason": "expired"}
                elif local_user and local_user.active and settings.site_id in local_user.allowed_sites:
                    try:
                        with db.begin_nested():
                            local_svc = Service(
                                db,
                                settings,
                                Actor(
                                    local_user.id,
                                    local_user.role,
                                    local_user.allowed_sites,
                                    local_user.username,
                                    local_user.allowed_lines,
                                ),
                            )
                            validate_identity(local_user, settings)
                            local_svc.approve(
                                remote["payload"]["case_id"],
                                ApprovalInput.model_validate(
                                    {k: v for k, v in remote["payload"].items() if k != "case_id"}
                                ),
                            )
                            result = {"status": "applied"}
                    except Exception as exc:
                        result = {"status": "rejected", "reason": type(exc).__name__}
                svc.add(
                    m.Command,
                    key=remote["id"],
                    actor_id="remote",
                    fingerprint=digest(remote),
                    response=result,
                )
                svc.audit("remote.checked", remote["id"], result)
                results.append({"id": remote["id"], "result": result})
            # Local commits precede ACK; repeat requests find the idempotent local Command.
            db.commit()
            if results or distribution_results:
                ack_body = {"results": results, "distributions": distribution_results}
                res = client.post(
                    settings.center_url + "/api/sync/ack",
                    headers=headers,
                    json={"body": ack_body, "signature": sign(ack_body, settings.sync_token)},
                )
                res.raise_for_status()
        from .model_journal import write

        db.info.setdefault("after_commit", []).append(
            lambda: write(
                settings.data_dir / "sync-status.json",
                {"node_id": settings.node_id, "last_success": current_time().isoformat()},
            )
        )
        return {
            "sent": len(events),
            "acknowledged": ack["ack_sequence"],
            "conflicts": ack["conflicts"],
            "authorizations": authorization_result,
            "distributions": distribution_results,
        }
