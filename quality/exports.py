import csv
import io
import json

from fastapi import Depends
from sqlalchemy import func
from fastapi.responses import StreamingResponse

from . import models as m
from .security import fail
from .service import serialize
from .querying import QueryScope, apply_scope, get_scope


def register_exports(app, service, settings):
    @app.get("/api/export/{kind}")
    def export(kind: str, q: str = '', status: str = '', scope: QueryScope = Depends(get_scope), svc=Depends(service)):
        cls = {
            "cases": m.Case,
            "inspections": m.Inspection,
            "dispositions": m.Disposition,
            "lab-results": m.LabResult,
        }.get(kind)
        if not cls:
            fail(404, "export_kind", "不支持该导出类型")
        svc.actor.require("read")
        query = apply_scope(svc, svc.scoped(cls), cls, scope)
        if cls == m.Case and q:
            query = query.where(cls.title.contains(q, autoescape=True))
        if status and cls in (m.Case, m.Inspection, m.Disposition):
            query = query.where(cls.status == status)
        total = svc.db.scalar(query.with_only_columns(func.count())) or 0
        svc.audit("export.created", kind, {"count": total, "mode": settings.mode})
        columns = ["runtime_mode", "export_scope", *[c.name for c in cls.__table__.columns]]

        def safe(value):
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
                return "'" + value
            return value

        def content():
            yield "\ufeff"
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=columns)
            writer.writeheader()
            yield out.getvalue()
            for record in svc.db.scalars(query.order_by(cls.created_at.desc(), cls.id).execution_options(yield_per=200)):
                row = {"runtime_mode": settings.mode, "export_scope": "全部授权筛选结果", **serialize(record)}
                out.seek(0)
                out.truncate()
                writer.writerow({key: safe(value) for key, value in row.items()})
                yield out.getvalue()

        return StreamingResponse(
            content(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename={kind}-{settings.mode}.csv",
                "Cache-Control": "no-store",
            },
        )
