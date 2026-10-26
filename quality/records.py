"""Allowlisted server-side queues. Filters precede counts, sorting and pagination."""

from typing import Literal
from fastapi import Depends, Query
from sqlalchemy import cast, func, or_, select, Text
from . import models as m
from .querying import QueryScope, get_scope, apply_scope
from .security import fail, ROLES
from .service import serialize, command_response


def json_members(query, svc, expression, values, *, subset=False):
    """Empty applicability means shared; nonempty lists must match, without Python truncation."""
    if svc.db.bind.dialect.name == "sqlite":
        members = func.json_each(expression).table_valued("value")
        length = func.json_array_length(expression)
        condition = (
            ~select(1).select_from(members).where(members.c.value.not_in(values)).exists()
            if subset
            else select(1).select_from(members).where(members.c.value.in_(values)).exists()
        )
    else:
        from sqlalchemy.dialects.postgresql import JSONB, array

        members = func.jsonb_array_elements_text(cast(expression, JSONB)).table_valued("value")
        length = func.jsonb_array_length(cast(expression, JSONB))
        condition = (
            ~select(1).select_from(members).where(members.c.value.not_in(values)).exists()
            if subset
            else cast(expression, JSONB).has_any(array(values))
        )
    return query.where(or_(length.is_(None), length == 0, condition))


def knowledge_scope(svc, query, scope):
    for key, value in [
        ("factory_ids", scope.site_id),
        ("line_ids", scope.line_id),
        ("product_ids", scope.product_id),
    ]:
        if value:
            query = json_members(query, svc, m.Knowledge.scope[key], [value])
    if scope.process:
        query = query.where(m.Knowledge.scope["process"].as_string().in_([scope.process, "all"]))
    return query


COLLECTIONS = {
    "passages": (
        m.Passage,
        ["object_id", "route_id", "step_id", "sequence", "entered_at", "exited_at"],
        "read",
    ),
    "cases": (m.Case, ["title", "status", "process", "version"], "read"),
    "masters": (m.Master, ["name", "code", "kind", "version"], "read"),
    "routes": (m.Route, ["name", "process", "status", "revision"], "read"),
    "inspections": (m.Inspection, ["record_id", "object_id", "status", "occurred_at"], "read"),
    "files": (m.Evidence, ["filename", "media_type", "size", "status"], "read"),
    "lab-results": (m.LabResult, ["object_id", "metric", "occurred_at"], "read"),
    "knowledge": (m.Knowledge, ["title", "category", "status", "revision"], "read"),
    "adapters": (m.Adapter, ["name", "protocol", "enabled"], "read"),
    "sites": (m.Site, ["name", "node_id", "last_seen"], "read"),
    "snapshots": (m.Snapshot, ["entity_id", "version", "source_node"], "read"),
    "remote-requests": (m.RemoteRequest, ["target_node", "kind", "status"], "read"),
    "dispositions": (m.Disposition, ["object_id", "action", "status", "cycle"], "read"),
    "recipes": (m.Master, ["name", "code", "version"], "read"),
    "distributions": (m.Distribution, ["kind", "record_id", "status", "generation", "target_node"], "read"),
    "audit": (m.Audit, ["action", "actor_id", "target_id"], "read"),
    "runs": (m.Run, ["case_id", "status"], "read"),
    "events": (m.Outbox, ["kind", "status", "attempts"], "read"),
    "raw-records": (
        m.RawRecord,
        ["record_id", "source_id", "status", "mapping_version", "received_at"],
        "admin",
    ),
    "measurements": (
        m.Measurement,
        ["equipment_id", "metric", "occurred_at", "value", "unit", "quality"],
        "read",
    ),
    "learning-records": (m.LearningRecord, ["title", "kind", "status", "revision", "version"], "read"),
    "learning-tasks": (m.LearningTask, ["kind", "status", "attempts", "record_id"], "read"),
}


def task_collection(
    svc, scope, page=1, page_size=25, q="", status="", sort_by="created_at", sort_order="desc"
):
    """Role/independence and final release filters are applied before counting and paging."""
    from datetime import datetime
    from .security import aware, current_time

    svc.actor.require("read")
    perms = ROLES[svc.actor.role]
    center = svc.settings.node_role == "center"
    cls = m.Snapshot if center else m.Case
    query = apply_scope(svc, svc.scoped(cls), cls, scope)
    if center:
        if "approve" not in perms:
            query = query.where(False)
        plans = cls.data["records"]["plans"]
        if svc.db.bind.dialect.name == "sqlite":
            members = func.json_each(plans).table_valued("value")
            eligible = (func.json_extract(members.c.value, "$.status") == "awaiting_approval") & (
                func.json_extract(members.c.value, "$.author_id") != svc.actor.id
            )
        else:
            from sqlalchemy.dialects.postgresql import JSONB

            members = func.jsonb_array_elements(cast(plans, JSONB)).table_valued("value")
            value = cast(members.c.value, JSONB)
            eligible = (value["status"].as_string() == "awaiting_approval") & (
                value["author_id"].as_string() != svc.actor.id
            )
        query = query.where(
            cls.data["status"].as_string() == "awaiting_approval",
            select(1).select_from(members).where(eligible).exists(),
        )
        status_col, title_col = cls.data["status"].as_string(), cls.data["title"].as_string()
    else:
        states = set()
        for action, allowed in [
            (
                "diagnose",
                {"accepted", "needs_evidence", "retest_failed", "execution_failed", "partially_executed"},
            ),
            ("approve", {"awaiting_approval"}),
            ("execute", {"approved"}),
            ("retest", {"awaiting_retest", "inconclusive"}),
            ("close", {"retest_passed"}),
            ("release", {"closed"}),
        ]:
            if action in perms:
                states.update(allowed)
        author = (
            svc.scoped(m.Plan)
            .with_only_columns(m.Plan.author_id)
            .where(m.Plan.case_id == cls.id)
            .order_by(m.Plan.revision.desc())
            .limit(1)
            .correlate(cls)
            .scalar_subquery()
        )
        query = query.where(
            cls.status.in_(states),
            or_(cls.status != "awaiting_approval", author.is_(None), author != svc.actor.id),
        )
        query = query.where(
            or_(
                cls.status != "closed",
                func.coalesce(cls.outcome["released"].as_boolean(), False).is_(False)
                & ~func.coalesce(cls.outcome["quality_disposition"].as_string(), "").in_(
                    ["scrap", "pending"]
                ),
            )
        )  # noqa: E712
        status_col, title_col = cls.status, cls.title
    if q:
        query = query.where(title_col.contains(q, autoescape=True))
    if status:
        query = query.where(status_col == status)
    counts = dict(
        svc.db.execute(query.with_only_columns(status_col, func.count()).group_by(status_col)).all()
    )
    fields = {
        "created_at": cls.created_at,
        "title": title_col,
        "status": status_col,
        "version": cls.version,
        "id": cls.id,
    }
    if (
        sort_by not in fields
        or sort_order not in {"asc", "desc"}
        or not 1 <= page <= 100000
        or not 1 <= page_size <= 200
    ):
        fail(422, "task_pagination", "待办分页或排序参数无效")
    order = fields[sort_by].asc() if sort_order == "asc" else fields[sort_by].desc()
    items = []
    for row in svc.db.scalars(query.order_by(order, cls.id).offset((page - 1) * page_size).limit(page_size)):
        if center:
            plan = next(
                p
                for p in row.data["records"]["plans"]
                if p["status"] == "awaiting_approval" and p["author_id"] != svc.actor.id
            )
            item = {
                "id": row.id,
                "case_id": row.entity_id,
                "snapshot_id": row.id,
                "title": row.data["title"],
                "status": "awaiting_approval",
                "site_id": row.site_id,
                "line_id": row.scope_line_id,
                "version": row.version,
                "overdue": aware(datetime.fromisoformat(plan["expires_at"])) < current_time(),
                "authority": "request_to_site_only",
            }
        else:
            plan = svc.db.scalar(
                svc.scoped(m.Plan).where(m.Plan.case_id == row.id).order_by(m.Plan.revision.desc()).limit(1)
            )
            item = {
                "id": row.id,
                "case_id": row.id,
                "title": row.title,
                "status": row.status,
                "site_id": row.site_id,
                "line_id": row.line_id,
                "version": row.version,
                "overdue": bool(
                    plan
                    and plan.status in {"approved", "awaiting_approval"}
                    and aware(plan.expires_at) < current_time()
                ),
            }
        item["created_at"] = row.created_at.isoformat()
        items.append(item)
    return {
        "items": items,
        "total": sum(counts.values()),
        "counts_by_state": counts,
        "page": page,
        "page_size": page_size,
        "sort_fields": list(fields),
        "sort_by": sort_by,
        "sort_order": sort_order,
        "scope": scope.model_dump(mode="json"),
    }


def collection(
    svc,
    resource,
    scope,
    page=1,
    page_size=25,
    q="",
    status="",
    kind="",
    source_id="",
    case_id="",
    record_id="",
    sort_by="created_at",
    sort_order="desc",
    object_id="",
    parent_id="",
):
    if resource == "tasks":
        return task_collection(svc, scope, page, page_size, q, status, sort_by, sort_order)
    if resource not in COLLECTIONS:
        fail(404, "collection", "不支持任意数据库表读取")
    cls, fields, permission = COLLECTIONS[resource]
    svc.actor.require(permission)
    shared = cls in (m.Master, m.Route, m.Knowledge, m.Site)
    if cls == m.Evidence and scope.line_id:
        from .querying import readonly_line_scope

        readonly_line_scope(svc, scope.line_id, scope.site_id)
        query = apply_scope(svc, svc.scoped(cls), cls, scope.model_copy(update={"line_id": ""}))
        query = query.where(cls.scope_line_id.in_([scope.line_id, "unassigned:" + svc.actor.id]))
    else:
        query = apply_scope(svc, svc.scoped(cls), cls, scope, shared=shared)
    if cls == m.DigitalTask:
        from .digital_tasks import ACTION_PERMISSIONS, can_read

        query = query.where(
            cls.kind.in_([action for action in ACTION_PERMISSIONS if can_read(svc.actor, action)])
        )
    if cls == m.PolicyDecision:
        from .digital_tasks import ACTION_PERMISSIONS, can_read

        query = query.where(
            cls.action.in_([action for action in ACTION_PERMISSIONS if can_read(svc.actor, action)])
        )
    if parent_id:
        parents = {
            m.InventorySnapshot: "material_id",
            m.BusinessOrderLine: "order_id",
            m.MaterialDemand: "plan_id",
            m.BusinessDocumentLine: "document_id",
            m.BusinessCommandAttempt: "command_id",
            m.BusinessReceipt: "command_id",
            m.FormulationCalculation: "formulation_id",
            m.InventoryReservation: "plan_id",
        }
        if cls not in parents:
            fail(422, "collection_parent", "此资源不支持父对象筛选")
        query = query.where(getattr(cls, parents[cls]) == parent_id)
    if resource == "recipes":
        query = query.where(m.Master.kind == "recipe")
    if cls == m.Knowledge:
        query = knowledge_scope(svc, query, scope)
    if cls == m.LearningRecord:
        # Independent evaluation answers are visible only to their evaluation reviewers.
        if "knowledge_review" not in ROLES[svc.actor.role]:
            query = query.where(
                ~(
                    (cls.kind == "dataset")
                    & cls.payload["split"].as_string().in_(["validation", "locked_test"])
                )
            )
        if case_id:
            query = query.where(cls.case_id == case_id)
    if cls == m.LearningTask and record_id:
        query = query.where(cls.record_id == record_id)
    if kind:
        if not hasattr(cls, "kind") and not hasattr(cls, "category"):
            fail(422, "collection_filter", "此列表不支持类型筛选")
        query = query.where(getattr(cls, "kind", getattr(cls, "category", None)) == kind)
    if object_id:
        if not hasattr(cls, "object_id"):
            fail(422, "collection_filter", "此列表不支持对象筛选")
        query = query.where(cls.object_id == object_id)
    if source_id:
        if not hasattr(cls, "source_id"):
            fail(422, "collection_filter", "此列表不支持来源筛选")
        query = query.where(cls.source_id == source_id)
    status_expr = cls.data["status"].as_string() if resource == "recipes" else getattr(cls, "status", None)
    if status:
        if status_expr is None:
            fail(422, "collection_filter", "此列表不支持状态筛选")
        query = query.where(status_expr == status)
    if q:
        search = [
            getattr(cls, k)
            for k in fields
            if k
            not in {
                "version",
                "revision",
                "cycle",
                "generation",
                "size",
                "value",
                "attempts",
                "occurred_at",
                "received_at",
                "last_seen",
                "enabled",
                "sequence",
                "entered_at",
                "exited_at",
            }
        ]
        query = query.where(or_(*(cast(column, Text).contains(q, autoescape=True) for column in search)))
    if cls == m.Distribution and svc.actor.lines:
        # Distribution packages may span several lines; no line grants may be widened.
        expr = cls.package["body"]["record"]["scope"]["line_ids"]
        restricted = (
            json_members(select(cls.id), svc, expr, svc.actor.lines, subset=True).where(
                func.coalesce(func.json_array_length(expr), 0) > 0
            )
            if svc.db.bind.dialect.name == "sqlite"
            else json_members(select(cls.id), svc, expr, svc.actor.lines, subset=True).where(
                func.coalesce(
                    func.jsonb_array_length(
                        cast(expr, __import__("sqlalchemy.dialects.postgresql", fromlist=["JSONB"]).JSONB)
                    ),
                    0,
                )
                > 0
            )
        )
        query = query.where(or_(cls.scope_line_id.in_(svc.actor.lines), cls.id.in_(restricted)))
    if not 1 <= page <= 100000 or not 1 <= page_size <= 200:
        fail(422, "pagination", "页码须1–100000，每页1–200")
    sort_fields = ["created_at", "id", *fields]
    if sort_by not in sort_fields or sort_order not in {"asc", "desc"}:
        fail(422, "sort_field", "排序字段不在此列表白名单内")
    column = getattr(cls, sort_by)
    order = column.asc() if sort_order == "asc" else column.desc()
    total = svc.db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = svc.db.scalars(
        query.order_by(order, cls.id.asc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    result = []
    for row in rows:
        item = command_response(serialize(row))
        if cls == m.BusinessOrderLine:
            item["display_label"] = f"{row.external_line} · {row.product_id} · 订单 {row.order_id[:8]}"
        elif cls == m.InventorySnapshot:
            item["display_label"] = (
                f"{row.warehouse} / {row.lot} · {row.material_id[:8]} · {row.source_version}"
            )
        elif cls == m.BusinessDocumentLine:
            item["display_label"] = (
                f"{row.external_line} · {row.lot} · {row.quantity} {row.unit} · 单据 {row.document_id[:8]}"
            )
        if cls == m.Adapter:
            item["config"] = {k: v for k, v in item["config"].items() if k != "token_hash"}
        if cls in (m.LearningTask, m.DigitalTask, m.BusinessCommand):
            item = {k: v for k, v in item.items() if k not in {"authorization_hash", "lease_token"}}
        if cls == m.Route:
            from .templates import effective_steps

            item["effective_steps"] = effective_steps(row.steps)
        result.append(item)
    return {
        "items": result,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "sort_fields": sort_fields,
        "scope": scope.model_dump(mode="json"),
    }


def register_records(app, service):
    @app.get("/api/records/{resource}")
    def records(
        resource: str,
        page: int = 1,
        page_size: int = 25,
        q: str = Query("", max_length=200),
        status: str = "",
        kind: str = "",
        source_id: str = "",
        case_id: str = "",
        record_id: str = "",
        object_id: str = Query("", max_length=120),
        parent_id: str = Query("", max_length=120),
        sort_by: str = "created_at",
        sort_order: Literal["asc", "desc"] = "desc",
        scope: QueryScope = Depends(get_scope),
        svc=Depends(service),
    ):
        return collection(
            svc,
            resource,
            scope,
            page,
            page_size,
            q,
            status,
            kind,
            source_id,
            case_id,
            record_id,
            sort_by,
            sort_order,
            object_id,
            parent_id,
        )
