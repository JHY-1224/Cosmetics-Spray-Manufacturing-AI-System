from sqlalchemy import func, text

from .querying import get_scope, apply_scope
from . import models as m
from .security import fail, current_time, ROLES
from .service import serialize
from .templates import effective_steps


def request_disposition(svc, data):
    if not ({"case", "disposition"} & ROLES[svc.actor.role]):
        fail(403, "forbidden", "没有申请质量处置的权限")
    line = svc.get(m.Master, data.line_id)
    route = svc.get(m.Route, data.route_id)
    if line.kind != "line" or line.data.get("route_id") != route.id or route.status != "published":
        fail(422, "route_binding", "处置对象必须关联已发布并绑定的实际工艺")
    if data.case_id:
        case = svc.get(m.Case, data.case_id)
        if case.line_id != data.line_id:
            fail(422, "case_scope", "案件与处置产线不一致")
    svc.evidence(data.evidence_ids, data.line_id)
    row = svc.add(
        m.Disposition,
        scope_line_id=data.line_id,
        object_id=data.object_id,
        route_id=route.id,
        case_id=data.case_id,
        action=data.action,
        author_id=svc.actor.id,
        reason=data.reason,
        data={"evidence_ids": data.evidence_ids, "route_snapshot": serialize(route)},
    )
    svc.audit("disposition.requested", row.id, {"action": data.action})
    return serialize(row)


def review_disposition(svc, id, data):
    row = svc.get(m.Disposition, id, True)
    svc.actor.require("disposition", row.site_id, row.scope_line_id)
    if row.version != data.version or row.status != "pending":
        fail(409, "disposition_state", "处置申请版本变化或已经审核")
    if row.author_id == svc.actor.id:
        fail(403, "separation_of_duties", "处置申请与审核须由不同人员完成")
    if data.approved:
        route = svc.get(m.Route, row.route_id)
        line = svc.get(m.Master, row.scope_line_id)
        rule = route.disposition
        if (
            route.status != "published"
            or line.data.get("route_id") != route.id
            or not rule.get("approved")
            or rule.get("ng") != row.action
        ):
            fail(409, "disposition_unapproved", "返工/退镀配置未批准或已变化，保持待处置")
        if rule.get("return_step_id") not in {s["id"] for s in effective_steps(route.steps)}:
            fail(409, "disposition_return", "缺少有效且已批准的返回工序")
        if svc.db.bind.dialect.name == "postgresql":
            svc.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": svc.settings.tenant_id + ":" + row.site_id + ":" + row.object_id},
            )
        used = (
            svc.db.scalar(
                svc.scoped(m.Disposition)
                .where(
                    m.Disposition.object_id == row.object_id,
                    m.Disposition.route_id == row.route_id,
                    m.Disposition.status == "approved",
                )
                .with_only_columns(func.count())
            )
            or 0
        )
        if used >= rule.get("max_cycles", 0):
            fail(409, "disposition_budget", "达到该对象的已批准返工/退镀次数上限")
        row.cycle, row.return_step_id = used + 2, rule["return_step_id"]
    row.status = "approved" if data.approved else "rejected"
    row.reviewer_id = svc.actor.id
    row.version += 1
    row.data = {**row.data, "review_reason": data.reason, "reviewed_at": current_time().isoformat()}
    svc.audit("disposition.reviewed", row.id, {"status": row.status, "cycle": row.cycle})
    return serialize(row)


def validate_reentry(svc, data):
    if not isinstance(data.data.get("cycle", 1), int) or not 1 <= data.data.get("cycle", 1) <= 100:
        fail(422, "cycle_invalid", "经过循环次数必须为有效整数")
    duplicate = svc.db.scalar(
        svc.scoped(m.Passage).where(
            m.Passage.object_id == data.object_id,
            m.Passage.route_id == data.route_id,
            m.Passage.step_id == data.step_id,
        )
    )
    if duplicate or data.data.get("cycle", 1) > 1:
        if not data.data.get("disposition_id"):
            fail(409, "reentry_unapproved", "重复经过须先申请并批准返工/退镀")
        disposition = svc.get(m.Disposition, data.data.get("disposition_id", ""))
        if (
            disposition.status != "approved"
            or disposition.object_id != data.object_id
            or disposition.route_id != data.route_id
            or data.data.get("cycle") != disposition.cycle
        ):
            fail(409, "reentry_unapproved", "新经过须引用本对象已批准的质量处置与实际循环次数")
        first = svc.db.scalar(
            svc.scoped(m.Passage)
            .where(m.Passage.object_id == data.object_id, m.Passage.route_id == data.route_id)
            .order_by(m.Passage.sequence.desc())
            .limit(1)
        )
        if (
            first
            and first.data.get("cycle", 1) != disposition.cycle
            and data.step_id != disposition.return_step_id
        ):
            fail(409, "reentry_step", "返工/退镀首个经过必须是已批准的返回节点")


def register_routes(app, service, command):
    from fastapi import Depends, Request
    from .schemas import DispositionInput, DispositionReview

    @app.get("/api/dispositions")
    def dispositions(scope=Depends(get_scope), svc=Depends(service)):
        return [
            serialize(r)
            for r in svc.db.scalars(
                apply_scope(svc, svc.scoped(m.Disposition), m.Disposition, scope).order_by(m.Disposition.created_at.desc()).limit(500)
            ).all()
        ]

    @app.post("/api/dispositions")
    def disposition(data: DispositionInput, request: Request, svc=Depends(service)):
        return command(svc, request, data.model_dump(), lambda: request_disposition(svc, data))

    @app.post("/api/dispositions/{id}/review")
    def review(id: str, data: DispositionReview, request: Request, svc=Depends(service)):
        return command(svc, request, data.model_dump(), lambda: review_disposition(svc, id, data))
