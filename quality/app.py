import csv
import io
import json
import logging
import hmac
import time
from contextlib import asynccontextmanager
from datetime import timedelta, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from . import models as m, schemas as s
from .adapters import AdapterConfig
from .agent import analyze, validate_rules
from .config import Settings
from .db import Database
from .security import Actor, PASSWORDS, ROLES, aware, check_password, current_time, digest, fail, token
from .service import Service, serialize, command_response
from .storage import Storage, parse_document, validate_file
from .templates import effective_steps, validate_route
from .querying import QueryScope, apply_scope, page_result, get_scope

LOG = logging.getLogger("quality")
REQUESTS = Counter("quality_requests_total", "API requests", ["method", "status"])
LATENCY = Histogram("quality_request_seconds", "API request duration")


def create_app(settings=None, *, frontend_dir=None):
    settings = settings or Settings()
    database = Database(settings)
    storage = Storage(settings)
    from .observability import setup

    trace_provider, tracer, recent_spans = setup(settings)

    @asynccontextmanager
    async def lifespan(app):
        if settings.mode == "production":
            database.check_runtime_role()
        yield
        trace_provider.shutdown()
        database.engine.dispose()

    from .versioning import RELEASE
    app = FastAPI(title="涂镀智溯 API", version=RELEASE, lifespan=lifespan)
    app.state.settings, app.state.database, app.state.storage = settings, database, storage
    from .maintenance import MaintenanceBusy

    @app.exception_handler(MaintenanceBusy)
    async def maintenance_busy(request, exc):
        return JSONResponse(status_code=503, content={"detail": {"code": "maintenance_busy", "message": str(exc)}})

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = request.headers.get("x-request-id", token())[:120]
        bearer = request.headers.get("authorization", "").startswith("Bearer ")
        if (request.method not in ("GET", "HEAD", "OPTIONS") and request.cookies.get("quality_session")
                and (not bearer or request.url.path == "/api/auth/login")):
            origin = request.headers.get("origin")
            if origin and origin not in settings.trusted_origins:
                return JSONResponse(
                    status_code=403,
                    content={"detail": {"code": "origin_denied", "message": "请求来源不受信任"}},
                )
            supplied = request.headers.get("x-csrf-token", "")
            expected = digest("quality-csrf-v1:" + request.cookies["quality_session"])
            cookie = request.cookies.get("quality_csrf", "")
            if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()) or not hmac.compare_digest(cookie.encode(), expected.encode()):
                return JSONResponse(status_code=403, content={"detail": {
                    "code": "csrf_denied", "message": "会话提交缺少有效CSRF校验，请刷新登录状态后再办理"}})
        started = time.monotonic()
        with tracer.start_as_current_span(request.method + " " + request.url.path) as span:
            span.set_attributes(
                {
                    "tenant_id": settings.tenant_id,
                    "site_id": settings.site_id,
                    "request_id": request.state.request_id,
                }
            )
            response = await call_next(request)
            span.set_attribute("http.status_code", response.status_code)
        elapsed = time.monotonic() - started
        REQUESTS.labels(request.method, str(response.status_code)).inc()
        LATENCY.observe(elapsed)
        response.headers.update(
            {
                "X-Request-ID": request.state.request_id,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
            }
        )
        LOG.info(
            json.dumps(
                {
                    "request_id": request.state.request_id,
                    "tenant_id": settings.tenant_id,
                    "site_id": settings.site_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(elapsed * 1000),
                }
            )
        )
        return response

    @app.exception_handler(IntegrityError)
    async def duplicate(request, exc):
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "duplicate_or_reference", "message": "重复记录或关联约束不满足"}},
        )

    @app.exception_handler(StaleDataError)
    async def concurrent(request, exc):
        return JSONResponse(
            status_code=409,
            content={"detail": {"code": "version_conflict", "message": "记录已并发修改，请刷新"}},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "validation",
                    "message": "字段校验失败",
                    "fields": [{"loc": list(e["loc"]), "message": e["msg"]} for e in exc.errors()],
                }
            },
        )

    def actor(request: Request):
        header = request.headers.get("authorization", "")
        raw = header.removeprefix("Bearer ") if header.startswith("Bearer ") else request.cookies.get("quality_session")
        if not raw:
            fail(401, "authentication", "请先登录")
        with database.session() as db:
            session = db.scalar(
                select(m.Session).where(
                    m.Session.tenant_id == settings.tenant_id, m.Session.token_hash == digest(raw)
                )
            )
            user = db.get(m.User, session.user_id) if session else None
            if (
                not session
                or aware(session.expires_at) <= current_time()
                or not user
                or not user.active
                or user.auth_revision != session.auth_revision
            ):
                fail(401, "session_expired", "会话已过期或被撤销")
            sites = user.allowed_sites
            from .identity import validate_identity

            validate_identity(user, settings)
            if settings.node_role == "site":
                sites = [site for site in sites if site == settings.site_id]
                if not sites:
                    fail(403, "factory_denied", "账号没有本厂授权")
            return Actor(user.id, user.role, sites, user.username, user.allowed_lines)

    def service(request: Request, current: Actor = Depends(actor)):
        with database.session(
            current.sites, ["unassigned:" + current.id, *current.lines] if current.lines else None
        ) as db:
            yield Service(db, settings, current, request.state.request_id)

    def command(svc, request, body, fn):
        key = request.headers.get("idempotency-key", "")
        if not 8 <= len(key) <= 120:
            fail(400, "idempotency_required", "写入操作必须提供8至120字符的Idempotency-Key")
        fingerprint = digest({"path": request.url.path, "body": body})
        prior = svc.db.scalar(
            svc.scoped(m.Command).where(m.Command.actor_id == svc.actor.id, m.Command.key == key)
        )
        if prior:
            if prior.fingerprint != fingerprint:
                fail(409, "idempotency_conflict", "相同幂等键已用于不同请求")
            return svc.cached_command_result(prior)
        if settings.node_role == "center" and request.url.path.startswith("/api/cases"):
            fail(409, "site_authority", "中心不能直接修改厂内案件，请提交所属站点指令")
        result = fn()
        svc.add(m.Command, key=key, actor_id=svc.actor.id, fingerprint=fingerprint,
                response=command_response(result), authorization_hash=svc.command_authorization())
        return result

    def site_scope(svc):
        svc.actor.require("admin", settings.site_id if settings.node_role == "site" else "")

    @app.get("/api/health")
    @app.get("/api/ready")
    def health(response: Response):
        try:
            with database.session() as db:
                revision = db.scalar(text("SELECT version_num FROM alembic_version"))
            db_status = "ready" if revision == "0012" else "migration_required"
        except Exception:
            db_status = "unavailable"
        response.status_code = 200 if db_status == "ready" else 503
        return {
            "status": "ready" if db_status == "ready" else "degraded",
            "database": db_status,
            "schema_required": "0012",
            "mode": settings.mode,
            "node_role": settings.node_role,
            "node_id": settings.node_id,
            "model": "configured" if settings.model_url else "disabled",
            "field_acceptance": "not_performed",
            "readiness_scope": "database_schema_only; use /api/readiness/dependencies and /api/readiness/business for runtime checks",
            "version": RELEASE,
            "local_instance": __import__("os").environ.get("QUALITY_LOCAL_INSTANCE", ""),
            "local_build": __import__("os").environ.get("QUALITY_LOCAL_BUILD", ""),
        }

    @app.get('/api/live')
    def live():
        return {'status': 'alive', 'version': RELEASE}

    @app.get("/metrics")
    def metrics(current: Actor = Depends(actor)):
        current.require("admin")
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/api/auth/login")
    def login(data: s.Login, request: Request, response: Response):
        with database.session() as db:
            failures = db.scalar(
                select(func.count())
                .select_from(m.Audit)
                .where(
                    m.Audit.tenant_id == settings.tenant_id,
                    m.Audit.action == "login.failed",
                    m.Audit.target_id == digest(data.username),
                    m.Audit.created_at > current_time() - timedelta(minutes=10),
                )
            )
            if failures >= 10:
                fail(429, "login_rate", "尝试次数过多，请稍后再试")
            user = db.scalar(
                select(m.User).where(m.User.tenant_id == settings.tenant_id, m.User.username == data.username)
            )
            valid = user and user.active and PASSWORDS.verify(data.password, user.password_hash)
            if not valid:
                db.add(
                    m.Audit(
                        tenant_id=settings.tenant_id,
                        site_id="",
                        actor_id="anonymous",
                        action="login.failed",
                        target_id=digest(data.username),
                        request_id=request.state.request_id,
                        detail={},
                    )
                )
                db.commit()
                fail(401, "invalid_credentials", "账号或密码不正确")
            from .identity import validate_identity

            validate_identity(user, settings)
            if settings.node_role == "site" and settings.site_id not in user.allowed_sites:
                fail(403, "factory_denied", "没有本厂登录权限")
            raw = token()
            db.add(
                m.Session(
                    tenant_id=settings.tenant_id,
                    site_id="",
                    user_id=user.id,
                    token_hash=digest(raw),
                    auth_revision=user.auth_revision,
                    expires_at=current_time() + timedelta(hours=settings.session_hours),
                )
            )
            response.set_cookie(
                "quality_session",
                raw,
                httponly=True,
                secure=settings.cookie_secure,
                samesite="strict",
                max_age=settings.session_hours * 3600,
            )
            response.set_cookie("quality_csrf", digest("quality-csrf-v1:" + raw),
                httponly=False, secure=settings.cookie_secure, samesite="strict",
                max_age=settings.session_hours * 3600)
            return {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "sites": user.allowed_sites,
                "mode": settings.mode,
            }

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, svc: Service = Depends(service)):
        header = request.headers.get("authorization", "")
        raw = header.removeprefix("Bearer ") if header.startswith("Bearer ") else request.cookies.get("quality_session", "")
        for session in svc.db.scalars(
            svc.scoped(m.Session).where(
                m.Session.user_id == svc.actor.id, m.Session.token_hash == digest(raw)
            )
        ).all():
            svc.db.delete(session)
        response.delete_cookie("quality_session")
        response.delete_cookie("quality_csrf")
        return {"revoked": True}

    @app.get("/api/auth/me")
    def me(request: Request, response: Response, svc: Service = Depends(service)):
        # Existing sessions can refresh their CSRF cookie without discarding work or weakening POST checks.
        if request.cookies.get("quality_session") and not request.headers.get("authorization"):
            response.set_cookie("quality_csrf", digest("quality-csrf-v1:" + request.cookies["quality_session"]),
                httponly=False, secure=settings.cookie_secure, samesite="strict",
                max_age=settings.session_hours * 3600)
        return {
            "id": svc.actor.id,
            "username": svc.actor.username,
            "role": svc.actor.role,
            "sites": svc.actor.sites,
            "permissions": sorted(ROLES[svc.actor.role]),
            "mode": settings.mode,
            "node_role": settings.node_role,
            "site_id": settings.site_id,
            "tenant_id": settings.tenant_id,
            "lines": svc.actor.lines or [],
            "timezone": settings.site_timezone,
        }

    @app.get("/api/users")
    def users(svc: Service = Depends(service)):
        site_scope(svc)
        return [
            serialize(u)
            for u in svc.db.scalars(svc.scoped(m.User)).all()
            if set(u.allowed_sites) <= set(svc.actor.sites)
        ]

    @app.post("/api/users")
    def create_user(data: s.UserInput, request: Request, svc: Service = Depends(service)):
        site_scope(svc)

        def apply():
            check_password(data.password)
            if set(data.allowed_sites) - set(svc.actor.sites):
                fail(403, "grant_escalation", "不能授予自己没有的工厂权限")
            if svc.actor.lines and (not data.allowed_lines or set(data.allowed_lines) - set(svc.actor.lines)):
                fail(403, "grant_escalation", "不能扩大产线授权")
            row = svc.add(
                m.User,
                site="",
                username=data.username,
                password_hash=PASSWORDS.hash(data.password),
                role=data.role,
                allowed_sites=data.allowed_sites,
                allowed_lines=data.allowed_lines,
            )
            svc.audit("user.created", row.id)
            return serialize(row)

        return command(svc, request, data.model_dump(exclude={"password"}), apply)

    @app.post("/api/users/{id}/revoke")
    def revoke(id: str, request: Request, svc: Service = Depends(service)):
        site_scope(svc)

        def apply():
            u = svc.get(m.User, id)
            if set(u.allowed_sites) - set(svc.actor.sites):
                fail(403, "user_scope", "不能撤销包含其他工厂权限的账号，须由企业管理员处理")
            if u.id == svc.actor.id:
                fail(409, "self_revoke", "不能在当前会话禁用自己")
            u.active, u.auth_revision = False, u.auth_revision + 1
            svc.audit("user.revoked", id)
            return serialize(u)

        return command(svc, request, {}, apply)

    @app.get("/api/masters")
    def masters(kind: str = "", scope: QueryScope = Depends(get_scope), svc: Service = Depends(service)):
        q = apply_scope(svc, svc.scoped(m.Master), m.Master, scope, shared=True)
        if kind:
            q = q.where(m.Master.kind == kind)
        return [
            serialize(r) for r in svc.db.scalars(q.order_by(m.Master.created_at.desc()).limit(1000)).all()
        ]

    @app.post("/api/masters")
    def master(data: s.MasterInput, request: Request, svc: Service = Depends(service)):
        svc.actor.require("master", settings.site_id)

        def apply():
            return svc.create_master(data)

        return command(svc, request, data.model_dump(), apply)

    @app.get("/api/routes")
    def routes(scope: QueryScope = Depends(get_scope), svc: Service = Depends(service)):
        return [
            {**serialize(r), "effective_steps": effective_steps(r.steps)}
            for r in svc.db.scalars(apply_scope(svc, svc.scoped(m.Route), m.Route, scope, shared=True)).all()
        ]

    @app.post("/api/routes")
    def route(data: s.RouteInput, request: Request, svc: Service = Depends(service)):
        svc.actor.require("master", settings.site_id)

        def apply():
            validate_route(data.steps, data.disposition)
            previous = svc.get(m.Route, data.previous_id) if data.previous_id else None
            row = svc.add(
                m.Route,
                **data.model_dump(),
                status="draft",
                revision=previous.revision + 1 if previous else 1,
                author_id=svc.actor.id,
            )
            svc.audit("route.draft", row.id)
            return serialize(row)

        return command(svc, request, data.model_dump(), apply)

    @app.post("/api/routes/{id}/review")
    def route_review(id: str, data: s.ReviewInput, request: Request, svc: Service = Depends(service)):
        def apply():
            row = svc.get(m.Route, id, True)
            if data.action == "submit":
                svc.actor.require("master", row.site_id)
                if row.status not in ("reference", "draft"):
                    fail(409, "route_state", "只有草稿可提交审核")
                row.status = "pending_review"
            else:
                svc.actor.require("route_review", row.site_id)
                if row.author_id == svc.actor.id:
                    fail(403, "separation_of_duties", "发布人与作者必须不同")
                if data.action == "publish" and row.status != "pending_review":
                    fail(409, "route_state", "路线必须先提交审核")
                if data.action in ("retire", "rollback") and row.status != "published":
                    fail(409, "route_state", "只有已发布版本可停用或回退")
                if data.action == "reject" and row.status != "pending_review":
                    fail(409, "route_state", "只有待审核版本可驳回")
                if data.action == "rollback":
                    previous = svc.get(m.Route, row.previous_id) if row.previous_id else None
                    if (
                        not previous
                        or not previous.reviewer_id
                        or previous.status != "published"
                        or previous.process != row.process
                    ):
                        fail(409, "rollback_baseline", "没有仍有效且经审核的上一版本；不能启用未确认参考路线")
                    for line in svc.db.scalars(svc.scoped(m.Master).where(m.Master.kind == "line")).all():
                        if line.data.get("route_id") == row.id:
                            line.data, line.version = {**line.data, "route_id": previous.id}, line.version + 1
                            svc.audit("line.route_rolled_back", line.id, {"from": row.id, "to": previous.id})
                row.status = {
                    "publish": "published",
                    "reject": "draft",
                    "retire": "retired",
                    "rollback": "rolled_back",
                }[data.action]
                row.reviewer_id = svc.actor.id
            svc.audit("route." + data.action, id, {"reason": data.reason})
            return serialize(row)

        return command(svc, request, data.model_dump(), apply)

    @app.post("/api/lines/{id}/bind/{route_id}")
    def bind(id: str, route_id: str, request: Request, svc: Service = Depends(service)):
        svc.actor.require("master", settings.site_id)

        def apply():
            line, route = svc.get(m.Master, id, True), svc.get(m.Route, route_id)
            if (line.kind != "line" or route.status != "published"
                    or line.data.get("process") != route.process):
                fail(422, "route_unapproved", "只有已发布路线可绑定产线")
            line.data, line.version = {**line.data, "route_id": route.id}, line.version + 1
            svc.audit("line.route_bound", id, {"route_id": route.id})
            return serialize(line)

        return command(svc, request, {"route_id": route_id}, apply)

    @app.post("/api/files")
    async def upload(request: Request, file: UploadFile = File(...), svc: Service = Depends(service)):
        svc.actor.require("file", settings.site_id)
        data = await file.read(settings.max_upload_bytes + 1)
        media = validate_file(file.filename or "", data, settings.max_upload_bytes)

        def apply():
            id = m.uid()
            key = f"{settings.tenant_id}/{settings.site_id}/{id}{Path(file.filename).suffix.lower()}"
            state = storage.put(key, data, media)
            row = svc.add(
                m.Evidence,
                id=id,
                filename=Path(file.filename).name[:200],
                object_key=key,
                sha256=digest(data),
                media_type=media,
                size=len(data),
                source="demo" if settings.mode == "demo" else "manual",
                status="available",
                replication_status="conflict"
                if state == "conflict"
                else "synced"
                if settings.storage_backend == "s3" and state == "available"
                else "pending"
                if settings.storage_backend == "s3"
                else "local",
            )
            svc.audit("evidence.uploaded", id, {"storage": state, "sha256": row.sha256})
            return {**serialize(row), "replication": state}

        return command(svc, request, {"filename": file.filename, "hash": digest(data)}, apply)

    @app.get("/api/files/{id}")
    def download(id: str, svc: Service = Depends(service)):
        row = svc.get(m.Evidence, id)
        return Response(
            storage.get(row.object_key, row.sha256),
            media_type=row.media_type,
            headers={
                "Content-Disposition": f"{'inline' if row.media_type.startswith('image/') else 'attachment'}; filename=evidence{Path(row.filename).suffix}"
            },
        )

    @app.get("/api/files")
    def files(scope: QueryScope = Depends(get_scope), svc: Service = Depends(service)):
        return [
            serialize(r)
            for r in svc.db.scalars(
                apply_scope(svc, svc.scoped(m.Evidence), m.Evidence, scope).order_by(m.Evidence.created_at.desc()).limit(500)
            ).all()
        ]

    from .visual import ingest_inspection as ingest

    @app.post("/api/inspections")
    def manual_inspection(data: s.InspectionInput, request: Request, svc: Service = Depends(service)):
        svc.actor.require("inspect", settings.site_id)
        return command(
            svc,
            request,
            data.model_dump(mode="json"),
            lambda: ingest(svc, data, "manual", "demo" if settings.mode == "demo" else "manual"),
        )

    @app.post("/api/ingest/visual/{adapter_id}")
    async def visual(adapter_id: str, data: s.InspectionInput, request: Request):
        original_payload = await request.json()
        def accept():
            with database.session() as db:
                a = db.scalar(
                    select(m.Adapter).where(
                        m.Adapter.id == adapter_id,
                        m.Adapter.tenant_id == settings.tenant_id,
                        m.Adapter.site_id == settings.site_id,
                    )
                )
                raw = request.headers.get("authorization", "").removeprefix("Bearer ")
                import hmac
    
                if (
                    not a
                    or not a.enabled
                    or a.protocol != "visual"
                    or not raw
                    or not hmac.compare_digest(a.config.get("token_hash", ""), digest(raw))
                ):
                    fail(401, "source_auth", "检测源认证失败")
                if settings.mode == "production" and a.provenance != "real":
                    fail(409, "simulation_in_production", "生产不允许模拟源")
                svc = Service(
                    db, settings, Actor("source:" + a.id, "admin", [settings.site_id]), request.state.request_id
                )
                def apply():
                    from .adapters import AdapterConfig
                    from .ingestion import stage_raw, apply_raw
                    if a.config.get('line_id') != data.line_id:
                        fail(403, 'source_binding', '检测源未绑定该产线')
                    config = AdapterConfig(endpoint='http://localhost', line_id=data.line_id,
                                           mapping_version=a.config.get('mapping_version', 'legacy-v1'),
                                           source_instance_id=a.config.get('source_instance_id', ''))
                    row = stage_raw(svc, a, original_payload, config)
                    if row.status == 'conflict':
                        fail(409, 'source_record_conflict', '来源同事件载荷冲突；原始记录已保留，须追加更正')
                    apply_raw(svc, a, row, config)
                    result = serialize(svc.get(m.Inspection, row.normalized_ids[-1]))
                    return {**result, 'reception': {'raw_record_id': row.id, 'state': row.status,
                                                   'durably_received': True}}
                return command(svc, request, original_payload, apply)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(accept)

    @app.post("/api/ingest/visual/{adapter_id}/files")
    async def visual_image(adapter_id: str, request: Request, file: UploadFile = File(...)):
        raw = request.headers.get("authorization", "").removeprefix("Bearer ")
        with database.session() as db:
            a = db.scalar(
                select(m.Adapter).where(
                    m.Adapter.id == adapter_id,
                    m.Adapter.tenant_id == settings.tenant_id,
                    m.Adapter.site_id == settings.site_id,
                )
            )
            import hmac

            if (
                not a
                or not a.enabled
                or a.protocol != "visual"
                or not raw
                or not hmac.compare_digest(a.config.get("token_hash", ""), digest(raw))
            ):
                fail(401, "source_auth", "检测源认证失败")
            if settings.mode == "production" and a.provenance != "real":
                fail(409, "simulation_in_production", "生产不能接收参考源图片")
            svc = Service(
                db,
                settings,
                Actor("source:" + a.id, "inspector", [settings.site_id]),
                request.state.request_id,
            )
            body = await file.read(settings.max_upload_bytes + 1)
            media = validate_file(file.filename or "", body, settings.max_upload_bytes)
            if not media.startswith("image/"):
                fail(422, "image_type", "视觉源只能上传图片")

            def apply():
                id = m.uid()
                key = f"{settings.tenant_id}/{settings.site_id}/{id}{Path(file.filename).suffix.lower()}"
                replication = storage.put(key, body, media)
                row = svc.add(
                    m.Evidence,
                    id=id,
                    filename=Path(file.filename).name[:200],
                    object_key=key,
                    sha256=digest(body),
                    media_type=media,
                    size=len(body),
                    source=a.provenance,
                    replication_status="conflict"
                    if replication == "conflict"
                    else "synced"
                    if settings.storage_backend == "s3" and replication == "available"
                    else "pending"
                    if settings.storage_backend == "s3"
                    else "local",
                )
                svc.audit("visual.image_uploaded", id, {"adapter_id": a.id, "replication": replication})
                return serialize(row)

            return command(svc, request, {"filename": file.filename, "hash": digest(body)}, apply)

    @app.get("/api/inspections")
    def inspections(scope: QueryScope = Depends(get_scope), svc: Service = Depends(service)):
        return [
            serialize(r)
            for r in svc.db.scalars(
                apply_scope(svc, svc.scoped(m.Inspection), m.Inspection, scope).order_by(m.Inspection.occurred_at.desc()).limit(500)
            ).all()
        ]

    @app.post("/api/passages")
    def passage(data: s.PassageInput, request: Request, svc: Service = Depends(service)):
        svc.actor.require("inspect", settings.site_id)

        def apply():
            route = svc.get(m.Route, data.route_id)
            if route.status != "published" or data.step_id not in {
                p["id"] for p in effective_steps(route.steps)
            }:
                fail(422, "step_invalid", "工序不在已发布的有效路线中")
            if (
                data.entered_at.tzinfo is None
                or data.exited_at.tzinfo is None
                or data.exited_at < data.entered_at
            ):
                fail(422, "passage_time", "工序经过时间非法")
            from .dispositions import validate_reentry

            validate_reentry(svc, data)
            if data.line_id:
                line = svc.get(m.Master, data.line_id)
                if line.kind != "line" or line.data.get("route_id") != route.id:
                    fail(422, "route_binding", "工序经过须匹配实际产线的已绑定路线")
            elif svc.actor.lines:
                fail(422, "passage_line", "产线受限账号必须指定经过记录的实际产线")
            row = svc.add(
                m.Passage,
                **data.model_dump(exclude={"line_id"}),
                scope_line_id=data.line_id or "unassigned:" + svc.actor.id,
            )
            svc.audit("passage.recorded", row.id)
            return serialize(row)

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.get("/api/trace/{object_id}")
    def trace(object_id: str, summary: bool = False, svc: Service = Depends(service)):
        if summary:
            return {"object_id": object_id, "inspections": [], "passages": [],
                    "counts": {"inspections": svc.db.scalar(svc.scoped(m.Inspection).where(m.Inspection.object_id == object_id).with_only_columns(func.count())),
                               "passages": svc.db.scalar(svc.scoped(m.Passage).where(m.Passage.object_id == object_id).with_only_columns(func.count()))}}
        return {
            "object_id": object_id,
            "inspections": [
                serialize(r)
                for r in svc.db.scalars(
                    svc.scoped(m.Inspection)
                    .where(m.Inspection.object_id == object_id)
                    .order_by(m.Inspection.occurred_at)
                ).all()
            ],
            "passages": [
                serialize(r)
                for r in svc.db.scalars(
                    svc.scoped(m.Passage).where(m.Passage.object_id == object_id).order_by(m.Passage.sequence)
                ).all()
            ],
        }

    @app.post("/api/lab-results")
    def lab_result(data: s.LabInput, request: Request, svc: Service = Depends(service)):
        svc.actor.require("inspect", settings.site_id)

        def apply():
            return svc.record_lab(data)

        return command(svc, request, data.model_dump(mode="json"), apply)

    @app.get("/api/lab-results")
    def lab_results(scope: QueryScope = Depends(get_scope), svc: Service = Depends(service)):
        return [
            serialize(r)
            for r in svc.db.scalars(
                apply_scope(svc, svc.scoped(m.LabResult), m.LabResult, scope).order_by(m.LabResult.occurred_at.desc()).limit(500)
            ).all()
        ]

    @app.get("/api/cases")
    def cases(status: str = "", q: str = "", page: int | None = None, page_size: int = 25,
              scope: QueryScope = Depends(get_scope), svc: Service = Depends(service)):
        query = apply_scope(svc, svc.scoped(m.Case), m.Case, scope)
        if status:
            query = query.where(m.Case.status == status)
        if q:
            query = query.where(m.Case.title.contains(q, autoescape=True))
        if page is not None:
            return page_result(svc, query, m.Case, page, page_size)
        return [
            serialize(r) for r in svc.db.scalars(query.order_by(m.Case.created_at.desc()).limit(500)).all()
        ]

    @app.post("/api/cases")
    def create_case(data: s.CaseInput, request: Request, svc: Service = Depends(service)):
        return command(svc, request, data.model_dump(), lambda: svc.create_case(data))

    @app.get("/api/cases/{id}")
    def case_detail(id: str, svc: Service = Depends(service)):
        result = serialize(svc.get(m.Case, id))
        for key, cls in [
            ("plans", m.Plan),
            ("approvals", m.Approval),
            ("receipts", m.Receipt),
            ("retests", m.Retest),
            ("runs", m.Run),
        ]:
            result[key] = [
                serialize(r)
                for r in svc.db.scalars(
                    svc.scoped(cls).where(cls.case_id == id).order_by(cls.created_at)
                ).all()
            ]
        for plan in result["plans"]:
            # Historic JSON stays immutable; stable view identities are derived for legacy actions.
            plan["content"] = {**plan["content"], "actions": [
                {**action, "action_id": action.get("action_id") or digest([plan["id"], index])[:32]}
                for index, action in enumerate(plan["content"]["actions"])]}
        result["timeline"] = [
            serialize(r)
            for r in svc.db.scalars(
                svc.scoped(m.Audit).where(m.Audit.target_id == id).order_by(m.Audit.created_at)
            ).all()
        ]
        return result

    @app.post("/api/cases/{id}/diagnose")
    def diagnose(id: str, data: s.VersionInput, request: Request, svc: Service = Depends(service)):
        svc.actor.require("diagnose", settings.site_id)

        def apply():
            case = svc.get(m.Case, id, True)
            svc.version(
                case,
                data.version,
                {"accepted", "needs_evidence", "diagnosed", "retest_failed", "rejected", "execution_failed", "partially_executed"},
            )
            return analyze(svc, case)

        return command(svc, request, data.model_dump(), apply)

    @app.post("/api/cases/{id}/evidence")
    def add_evidence(id: str, data: s.EvidenceUpdate, request: Request, svc: Service = Depends(service)):
        svc.actor.require("case", settings.site_id)

        def apply():
            case = svc.get(m.Case, id, True)
            svc.version(case, data.version)
            if case.status in ("closed", "cancelled"):
                fail(409, "immutable_case", "已结案证据不可更改；请新建关联调查")
            svc.evidence(data.evidence_ids, case.line_id)
            if data.purpose == 'supplemental':
                if data.inspection_ids:
                    fail(422, 'critical_inspection', '新增检测事实必须通过关键证据路径核对并重新审核')
                case.context = {**case.context, 'supplemental_evidence': [
                    *case.context.get('supplemental_evidence', []),
                    {'evidence_ids': data.evidence_ids, 'reason': data.reason, 'actor_id': svc.actor.id,
                     'recorded_at': current_time().isoformat(), 'classification': 'not_used_for_approval'}]}
                svc.advance(case, case.status, 'evidence.supplemental_added')
                return serialize(case)
            inspections = [svc.get(m.Inspection, iid) for iid in data.inspection_ids]
            if any(i.line_id != case.line_id or i.result["process"] != case.process for i in inspections):
                fail(422, "context_mismatch", "新证据不属于本案件产线/工艺")
            case.review_flags = [{**flag, 'resolved': True, 'reviewed_by': svc.actor.id,
                                  'reviewed_at': current_time().isoformat(), 'reason': data.reason}
                                 if flag.get('correction_id') in data.inspection_ids and not flag.get('resolved') else flag
                                 for flag in case.review_flags]
            history = case.context.get("evidence_history", [])
            case.context = {
                **case.context,
                "evidence_history": [
                    *history,
                    {
                        "context_hash": digest(case.context),
                        "reason": data.reason,
                        "occurred_at": current_time().isoformat(),
                    },
                ],
                "evidence_ids": list(dict.fromkeys(case.context.get("evidence_ids", []) + data.evidence_ids)),
                "inspection_ids": list(
                    dict.fromkeys(case.context.get("inspection_ids", []) + data.inspection_ids)
                ),
                "inspection_snapshots": case.context.get("inspection_snapshots", [])
                + [serialize(i) for i in inspections],
                "snapshot_version": case.context.get("snapshot_version", 1) + 1,
            }
            for p in svc.db.scalars(
                svc.scoped(m.Plan).where(
                    m.Plan.case_id == id, m.Plan.status.in_(["approved", "awaiting_approval"])
                )
            ):
                p.status = "superseded"
            svc.advance(case, "needs_evidence", "evidence.appended")
            return serialize(case)

        return command(svc, request, data.model_dump(), apply)

    @app.post("/api/cases/{id}/plans")
    def plan(id: str, data: s.PlanInput, request: Request, svc: Service = Depends(service)):
        return command(svc, request, data.model_dump(), lambda: svc.save_plan(id, data))

    @app.post('/api/cases/{id}/drafts')
    def save_draft(id: str, data: s.DraftPlanInput, request: Request, svc: Service = Depends(service)):
        return command(svc, request, data.model_dump(), lambda: svc.save_draft(id, data))

    @app.post('/api/cases/{id}/plans/{plan_id}/submit')
    def submit_draft(id: str, plan_id: str, data: s.VersionInput, request: Request, svc: Service = Depends(service)):
        return command(svc, request, data.model_dump(), lambda: svc.submit_draft(id, plan_id, data.version))

    @app.post("/api/cases/{id}/approve")
    def approve(id: str, data: s.ApprovalInput, request: Request, svc: Service = Depends(service)):
        def apply():
            result = svc.approve(id, data)
            case = svc.get(m.Case, id)
            run_id = case.diagnosis.get("run_id")
            if run_id:
                # Commit business approval first; checkpoint acknowledgement can be retried by worker.
                svc.event(
                    "workflow.reviewed",
                    {"run_id": run_id, "approved": data.approved, "plan_id": data.plan_id},
                )
            return result

        return command(svc, request, data.model_dump(), apply)

    @app.post("/api/cases/{id}/execute")
    def execute(id: str, data: s.ReceiptInput, request: Request, svc: Service = Depends(service)):
        return command(svc, request, data.model_dump(mode="json"), lambda: svc.execute(id, data))

    @app.post("/api/cases/{id}/retest")
    def retest(id: str, data: s.RetestInput, request: Request, svc: Service = Depends(service)):
        return command(svc, request, data.model_dump(mode="json"), lambda: svc.retest(id, data))

    @app.post("/api/cases/{id}/close")
    def close(id: str, data: s.CloseInput, request: Request, svc: Service = Depends(service)):
        return command(svc, request, data.model_dump(), lambda: svc.close(id, data))

    @app.post("/api/cases/{id}/cancel")
    def cancel(id: str, data: s.VersionInput, request: Request, svc: Service = Depends(service)):
        svc.actor.require("close", settings.site_id)

        def apply():
            case = svc.get(m.Case, id, True)
            svc.version(case, data.version)
            if case.status in ("closed", "cancelled"):
                fail(409, "terminal_state", "案件已处于终态")
            svc.advance(case, "cancelled", "case.cancelled")
            return serialize(case)

        return command(svc, request, data.model_dump(), apply)

    @app.post("/api/cases/{id}/release")
    def release(id: str, data: s.ReleaseInput, request: Request, svc: Service = Depends(service)):
        svc.actor.require("release", settings.site_id)

        def apply():
            case = svc.get(m.Case, id, True)
            svc.version(case, data.version, {"closed"})
            svc.require_reviewed_corrections(case)
            if case.outcome.get('released'):
                fail(409, 'already_released', '此版本已经放行，请读取原决定而不是重复登记')
            if case.outcome.get('quality_disposition') in {'pending', 'scrap'}:
                fail(409, 'release_disposition', '处置待确认或报废对象不能登记正式放行')
            svc.evidence(data.evidence_ids, case.line_id)
            case.outcome = {
                **case.outcome,
                "released": True,
                "released_by": svc.actor.id,
                "released_at": current_time().isoformat(),
                "release_reason": data.reason,
                "release_evidence_ids": data.evidence_ids,
                "external_release_reference": data.external_reference,
            }
            svc.advance(case, "closed", "quality.released")
            return serialize(case)

        return command(svc, request, data.model_dump(), apply)

    @app.post('/api/cases/{id}/reopen')
    def reopen(id: str, data: s.ReopenInput, request: Request, svc: Service = Depends(service)):
        svc.actor.require('close', settings.site_id)
        def apply():
            case = svc.get(m.Case, id, True)
            svc.version(case, data.version, {'closed', 'cancelled'})
            svc.evidence(data.evidence_ids, case.line_id)
            history = case.context.get('closed_history', [])
            previous = {'version': case.version, 'status': case.status, 'context_hash': digest(case.context),
                        'outcome': case.outcome, 'reason': data.reason, 'reopened_by': svc.actor.id,
                        'reopened_at': current_time().isoformat()}
            case.context = {**case.context, 'closed_history': [*history, previous],
                            'evidence_ids': list(dict.fromkeys([*case.context.get('evidence_ids', []), *data.evidence_ids])),
                            'snapshot_version': case.context.get('snapshot_version', 1)+1}
            case.outcome = {'rectification_effect': 'pending', 'root_cause_confirmation': 'unverified',
                            'quality_disposition': 'pending', 'released': False}
            svc.advance(case, 'needs_evidence', 'case.reopened')
            return serialize(case)
        return command(svc, request, data.model_dump(), apply)

    @app.get('/api/commands/{key}')
    def command_result(key: str, svc: Service = Depends(service)):
        row = svc.db.scalar(svc.scoped(m.Command).where(m.Command.actor_id == svc.actor.id, m.Command.key == key))
        if row and not svc.can_read_command_result(row):
            return {'status':'committed_restricted', 'message':'原提交已完成；当前授权不能读取旧结果，请按原对象核对，不要重复提交'}
        return {'status': 'committed', 'response': svc.cached_command_result(row)} if row else {'status': 'unknown', 'message': '尚未找到已提交结果，请保留原请求标识继续核对'}

    @app.get("/api/knowledge")
    def knowledge(q: str = "", process: str = "", scope: QueryScope = Depends(get_scope), svc: Service = Depends(service)):
        query = apply_scope(svc, svc.scoped(m.Knowledge), m.Knowledge, scope, shared=True)
        if q:
            query = query.where(
                m.Knowledge.content.contains(q, autoescape=True)
                | m.Knowledge.title.contains(q, autoescape=True)
            )
        rows = svc.db.scalars(query.order_by(m.Knowledge.created_at.desc()).limit(300)).all()
        def selected(r):
            return ((not process or r.scope.get('process') in (process,'all'))
                    and (not scope.site_id or not r.scope.get('factory_ids') or scope.site_id in r.scope['factory_ids'])
                    and (not scope.line_id or not r.scope.get('line_ids') or scope.line_id in r.scope['line_ids'])
                    and (not scope.product_id or not r.scope.get('product_ids') or scope.product_id in r.scope['product_ids']))
        return [serialize(r) for r in rows if selected(r)]

    @app.get("/api/knowledge-search")
    def knowledge_search(
        q: str, process: str, product_id: str = "", vector: bool = False, line_id: str = '', site_id: str = '',
        route_id: str = '', recipe_id: str = '', substrate_id: str = '', svc: Service = Depends(service)
    ):
        from .retrieval import search

        return search(svc, q, process, product_id, vector, line_id,
                      {'route_id': route_id, 'recipe_id': recipe_id, 'substrate_id': substrate_id}, site_id)

    @app.post("/api/knowledge/{id}/index")
    def index_knowledge(id: str, request: Request, svc: Service = Depends(service)):
        svc.actor.require("knowledge", settings.site_id)

        def apply():
            from .retrieval import embed

            row = svc.get(m.Knowledge, id)
            if row.status != "published":
                fail(409, "unpublished", "只索引已发布知识")
            vector = embed(settings, row.title + "\n" + row.content)
            old = svc.db.scalar(svc.scoped(m.KnowledgeVector).where(m.KnowledgeVector.knowledge_id == id))
            if old:
                old.embedding, old.model_version, old.content_hash = (
                    vector,
                    settings.embedding_model,
                    digest(row.content),
                )
            else:
                svc.add(
                    m.KnowledgeVector,
                    knowledge_id=id,
                    embedding=vector,
                    model_version=settings.embedding_model,
                    content_hash=digest(row.content),
                )
            svc.audit("knowledge.indexed", id)
            return {"indexed": True, "dimensions": 384, "model_version": settings.embedding_model}

        return command(svc, request, {}, apply)

    @app.post("/api/knowledge")
    def knowledge_create(data: s.KnowledgeInput, request: Request, svc: Service = Depends(service)):
        svc.actor.require("knowledge", settings.site_id)

        def apply():
            from .scopes import knowledge_write_scope
            knowledge_write_scope(svc.actor, data.scope)
            if set(data.source) & {'learning_record_id','lineage_groups','origin_node'}:
                fail(422, 'knowledge_provenance', '内部经验血缘由审核流程生成，不接受客户端冒充')
            validate_rules(data.rules)
            if (
                data.category == "root_cause"
                and not {"dimension", "defects", "cause", "next_checks"} <= data.rules.keys()
            ):
                fail(422, "rule_schema", "根因规则必须定义维度、缺陷、候选原因和核验步骤")
            previous = svc.get(m.Knowledge, data.previous_id) if data.previous_id else None
            if settings.mode == "production" and data.source.get("provenance") in ("demo", "reference"):
                fail(422, "synthetic_knowledge", "模拟知识不能进入生产正式知识库")
            row = svc.add(
                m.Knowledge,
                category=data.category,
                title=data.title,
                content=data.content,
                scope={**data.scope, "process": data.process},
                source={
                    **data.source,
                    "provenance": "demo"
                    if settings.mode == "demo"
                    else data.source.get("provenance", "manual"),
                },
                rules=data.rules,
                author_id=svc.actor.id,
                previous_id=data.previous_id,
                revision=previous.revision + 1 if previous else 1,
                valid_until=current_time() + timedelta(days=data.valid_days),
            )
            svc.audit("knowledge.draft", row.id)
            return serialize(row)

        return command(svc, request, data.model_dump(), apply)

    @app.post("/api/knowledge/parse/{file_id}")
    def knowledge_parse(file_id: str, request: Request, svc: Service = Depends(service)):
        svc.actor.require("knowledge", settings.site_id)

        def apply():
            evidence = svc.get(m.Evidence, file_id)
            try:
                result = parse_document(evidence.filename, storage.get(evidence.object_key, evidence.sha256))
            except Exception as exc:
                result = {"status": "needs_review", "text": "", "error_type": type(exc).__name__}
            svc.audit("knowledge.parsed", file_id, {"status": result["status"]})
            return {**result, "source_file_id": file_id}

        return command(svc, request, {}, apply)

    @app.post("/api/knowledge/{id}/review")
    def knowledge_review(id: str, data: s.ReviewInput, request: Request, svc: Service = Depends(service)):
        def apply():
            svc.actor.require("knowledge" if data.action == "submit" else "knowledge_review")
            k = svc.get(m.Knowledge, id, True)
            from .scopes import knowledge_write_scope
            knowledge_write_scope(svc.actor, k.scope)
            if k.source.get('learning_record_id'):
                fail(409, 'governed_knowledge', '此知识由专家版本治理，请在经验/规则工作台修订或废止，不能旁路审核')
            if data.action == 'publish':
                if k.category == 'root_cause':
                    fail(409, 'rule_governance_required', '新增根因规则须经条件字典、开发回放与独立审核生效；旧已发布版本保留')
                from .learning import reject_heldout_sources
                lineage = reject_heldout_sources(svc, k.source)
                if lineage:
                    k.source = {**k.source, "lineage_groups": lineage}
                if k.source.get('case_id'):
                    from .learning import require_not_holdout
                    require_not_holdout(svc, svc.get(m.Case, k.source['case_id']))
            if data.action == "submit":
                svc.actor.require("knowledge", k.site_id)
                if k.status != "draft":
                    fail(409, "knowledge_state", "只有草稿可提交")
                k.status = "pending_review"
            else:
                svc.actor.require("knowledge_review", k.site_id)
                if k.author_id == svc.actor.id:
                    fail(403, "separation_of_duties", "知识作者不能审核自己")
                if data.action == "publish" and k.status != "pending_review":
                    fail(409, "knowledge_state", "必须先提交审核")
                if data.action in ("retire", "rollback") and k.status != "published":
                    fail(409, "knowledge_state", "只有已发布版本可停用或回退")
                if data.action == "reject" and k.status != "pending_review":
                    fail(409, "knowledge_state", "只有待审核版本可驳回")
                previous = svc.get(m.Knowledge, k.previous_id, True) if k.previous_id else None
                if (
                    data.action == "publish"
                    and previous
                    and previous.scope == k.scope
                    and previous.category == k.category
                ):
                    if previous.reviewer_id and previous.status != "published":
                        fail(409, "publication_conflict", "前置版本已变化，请重新核对版本分支")
                    if previous.status == "published":
                        previous.status = "retired"
                if data.action == "rollback":
                    if (
                        not previous
                        or not previous.reviewer_id
                        or previous.status not in ("retired", "published")
                    ):
                        fail(409, "rollback_baseline", "没有经审核的上一版本，禁止自动启用草稿")
                    previous.status = "published"
                k.status = {
                    "publish": "published",
                    "reject": "draft",
                    "retire": "retired",
                    "rollback": "rolled_back",
                }[data.action]
                k.reviewer_id = svc.actor.id
            svc.audit("knowledge." + data.action, id, {"reason": data.reason})
            return serialize(k)

        return command(svc, request, data.model_dump(), apply)

    @app.get("/api/diff/{kind}/{a}/{b}")
    def diff(kind: str, a: str, b: str, svc: Service = Depends(service)):
        cls = {"knowledge": m.Knowledge, "route": m.Route, "plan": m.Plan}.get(kind)
        if not cls:
            fail(404, "kind_invalid", "比较类型非法")
        left, right = serialize(svc.get(cls, a)), serialize(svc.get(cls, b))
        return {
            "changes": [
                {"field": k, "before": left.get(k), "after": right.get(k)}
                for k in right
                if left.get(k) != right.get(k)
            ]
        }

    @app.get("/api/adapters")
    def adapters(scope: QueryScope = Depends(get_scope), svc: Service = Depends(service)):
        rows = []
        for row in svc.db.scalars(apply_scope(svc, svc.scoped(m.Adapter), m.Adapter, scope)).all():
            item = serialize(row)
            item["config"] = {k: v for k, v in row.config.items() if k != "token_hash"}
            rows.append(item)
        return rows

    @app.post("/api/adapters")
    def adapter_create(data: dict, request: Request, svc: Service = Depends(service)):
        site_scope(svc)

        def apply():
            if set(data) - {"name", "protocol", "config", "enabled", "provenance"} or data.get(
                "protocol"
            ) not in {"visual", "http", "mqtt", "opcua"}:
                fail(422, "adapter_schema", "适配器配置字段非法")
            provenance = data.get("provenance", "reference")
            if (
                provenance not in ("reference", "demo", "real")
                or settings.mode == "production"
                and provenance != "real"
            ):
                fail(422, "simulation_in_production", "生产不能启用模拟源")
            config, raw = data.get("config", {}), None
            if data["protocol"] == "visual":
                line = svc.get(m.Master, config.get("line_id", ""))
                if line.kind != "line":
                    fail(422, "line_invalid", "视觉源须绑定产线")
                raw = token()
                config = {"line_id": line.id, "token_hash": digest(raw)}
            else:
                parsed = AdapterConfig.model_validate(config)
                from .sources import validate_connection
                validate_connection(parsed, data['protocol'], settings)
                if parsed.line_id and svc.get(m.Master, parsed.line_id).kind != 'line':
                    fail(422, 'source_line', '来源必须绑定实际产线')
                config = parsed.model_dump()
            row = svc.add(
                m.Adapter,
                name=str(data.get("name", "新连接"))[:200],
                protocol=data["protocol"],
                config=config,
                enabled=bool(data.get("enabled", False)) and not (settings.mode == 'production' and data['protocol'] != 'visual'),
                provenance=provenance,
                scope_line_id=config.get('line_id') or 'unassigned:' + svc.actor.id,
                source_registry={'mapping_governance': 'pending' if settings.mode == 'production' else 'legacy_integration',
                          'field_acceptance': 'not_performed'},
            )
            svc.audit("adapter.created", row.id)
            return {"id": row.id, "token": raw, "status": "not_validated"}

        return command(svc, request, data, apply)

    @app.post("/api/adapters/{id}/poll")
    def adapter_poll(id: str, request: Request, svc: Service = Depends(service)):
        site_scope(svc)

        def apply():
            from .ingestion import collect_adapter

            return collect_adapter(svc, id)

        return command(svc, request, {}, apply)

    @app.get("/api/adapter-schemas")
    def adapter_schemas(svc: Service = Depends(service)):
        return {
            "configuration": AdapterConfig.model_json_schema(),
            "capabilities": {p: {"read": True, "write": False} for p in ("visual", "mqtt", "opcua", "http")},
        }

    @app.post("/api/import/preview")
    async def import_preview(request: Request, file: UploadFile = File(...), svc: Service = Depends(service)):
        svc.actor.require("inspect", settings.site_id)
        raw = await file.read(settings.max_upload_bytes + 1)
        validate_file(file.filename or "", raw, settings.max_upload_bytes)
        try:
            rows = (
                json.loads(raw)
                if file.filename.endswith(".json")
                else list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
            )
            if not isinstance(rows, list) or len(rows) > 2000:
                raise ValueError("导入应为数组且不超过2000行")
            errors, valid = [], []
            for i, row in enumerate(rows):
                try:
                    for field in ("defects", "image_ids", "material_batch_ids"):
                        if isinstance(row.get(field), str):
                            row[field] = json.loads(row[field]) if row[field] else []
                    valid.append(s.InspectionInput.model_validate(row).model_dump(mode="json"))
                except Exception as exc:
                    errors.append({"row": i + 1, "message": str(exc)[:500]})
            return {"valid": valid, "errors": errors, "count": len(rows), "commit_allowed": not errors}
        except (ValueError, UnicodeError) as exc:
            fail(422, "import_parse", str(exc))

    @app.post("/api/import/commit")
    def import_commit(data: list[s.InspectionInput], request: Request, svc: Service = Depends(service)):
        svc.actor.require("inspect", settings.site_id)
        if len(data) > 2000:
            fail(422, "import_limit", "单次最多2000条")

        def apply():
            rows = [ingest(svc, r, "manual", "demo" if settings.mode == "demo" else "manual") for r in data]
            svc.audit("import.committed", request.state.request_id, {"count": len(rows)})
            return {"count": len(rows), "ids": [r["id"] for r in rows]}

        return command(svc, request, [r.model_dump(mode="json") for r in data], apply)

    @app.get("/api/statistics")
    def statistics(
        start: datetime | None = None,
        end: datetime | None = None,
        granularity: str = "",
        site_id: str = "", line_id: str = "", product_id: str = "", process: str = "",
        svc: Service = Depends(service),
    ):
        from .statistics import quality_statistics

        if site_id:
            svc.actor.require("read", site_id)
        if line_id:
            from .querying import readonly_line_scope
            readonly_line_scope(svc, line_id, site_id)
        if process not in ('', 'plastic_spray', 'glass_vacuum'):
            fail(422, 'filter_process', '工艺筛选无效')
        return quality_statistics(svc, start, end, granularity, site_id, line_id, product_id, process)

    @app.get("/api/operations")
    def operations(scope: QueryScope = Depends(get_scope), svc: Service = Depends(service)):
        svc.actor.require("read")
        objects = storage.root
        used = sum(p.stat().st_size for p in objects.rglob("*") if p.is_file())
        def status_file(name):
            try:
                return __import__('json').loads((settings.data_dir/name).read_text())
            except (OSError, ValueError):
                return {'status': 'not_recorded'}
        import shutil
        raw_cache = settings.data_dir/'ingestion-raw'
        return {
            'worker': status_file('worker-status.json'), 'backup': status_file('backup-status.json'),
            'disk_free_bytes': shutil.disk_usage(settings.data_dir).free,
            'raw_cache_bytes': sum(p.stat().st_size for p in raw_cache.glob('*.json')),
            "outbox": [
                serialize(r)
                for r in svc.db.scalars(
                    apply_scope(svc, svc.scoped(m.Outbox), m.Outbox, scope).order_by(m.Outbox.created_at.desc()).limit(100)
                ).all()
            ],
            "runs": [
                serialize(r)
                for r in svc.db.scalars(apply_scope(svc, svc.scoped(m.Run), m.Run, scope).order_by(m.Run.created_at.desc()).limit(100)).all()
            ],
            "audit": [
                serialize(r)
                for r in svc.db.scalars(
                    apply_scope(svc, svc.scoped(m.Audit), m.Audit, scope).order_by(m.Audit.created_at.desc()).limit(300)
                ).all()
            ],
            "storage": {
                "used_bytes": used,
                "limit_bytes": settings.cache_max_bytes,
                "warning": used / settings.cache_max_bytes >= settings.warning_ratio,
            },
            "model": "模型增强未启用" if not settings.model_url else "已配置，连接结果以运行记录为准",
            "field_acceptance": "未执行",
            "offline_auth_hours": settings.offline_auth_hours,
            "sso": "标准扩展接口尚未接入客户身份源",
        }

    @app.get("/api/traces")
    def traces(svc: Service = Depends(service)):
        svc.actor.require("admin")
        return list(recent_spans.items)

    @app.post("/api/events/{id}/replay")
    def replay(id: str, request: Request, svc: Service = Depends(service)):
        site_scope(svc)

        def apply():
            row = svc.get(m.Outbox, id, True)
            if row.status != "dead":
                fail(409, "replay_state", "仅死信可人工重试；历史回放查看原记录，不产生业务动作")
            row.status, row.attempts, row.next_at = "pending", 0, current_time()
            svc.audit("event.retry_authorized", id)
            return serialize(row)

        return command(svc, request, {}, apply)

    from .sync import register_sync
    from .dispositions import register_routes
    from .imports import register_imports
    from .exports import register_exports
    from .recipes import register_recipes

    register_recipes(app, service, command)

    from .regression import register_regression

    register_regression(app, service, command)

    register_exports(app, service, settings)

    register_imports(app, service, command, settings)

    register_routes(app, service, command)

    register_sync(app, settings, database, service, command)
    from .records import register_records
    from .learning import register_learning
    register_records(app, service)
    register_learning(app, service, command)

    from .distribution import register_distribution

    register_distribution(app, settings, service, command)
    from .operations import register_operations

    register_operations(app, service, command)
    from .evidence_tasks import register_evidence_tasks

    register_evidence_tasks(app, service, command)
    from .sources import register_sources
    register_sources(app, service, command)
    from .workspace import register_workspace
    register_workspace(app, service, command)
    from .enterprise_api import register_enterprise
    register_enterprise(app, service, command)
    dist = Path(frontend_dir) if frontend_dir else Path(__file__).resolve().parent.parent / "frontend/dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="workbench")
    return app


app = create_app()
