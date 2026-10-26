"""Shared, narrowing-only query scope for lists, tasks and exports."""

from datetime import datetime
from typing import Literal
from pydantic import Field, model_validator, ValidationError

from . import models as m
from .schemas import Strict
from .security import fail


class QueryScope(Strict):
    site_id: str = Field(default='', max_length=80)
    line_id: str = Field(default='', max_length=100)
    product_id: str = Field(default='', max_length=120)
    process: Literal['', 'plastic_spray', 'glass_vacuum'] = ''
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode='after')
    def window(self):
        if any(v is not None and v.tzinfo is None for v in (self.start, self.end)):
            raise ValueError('筛选时间必须带时区')
        if self.start and self.end and self.start >= self.end:
            raise ValueError('开始时间必须早于结束')
        return self


def get_scope(site_id: str = '', line_id: str = '', product_id: str = '', process: str = '',
              start: datetime | None = None, end: datetime | None = None):
    try:
        return QueryScope(site_id=site_id, line_id=line_id, product_id=product_id, process=process, start=start, end=end)
    except ValidationError:
        fail(422, 'filter_scope', '筛选范围、时区或时间窗口无效')


def readonly_line_scope(svc, line_id, site_id=''):
    line = svc.db.scalar(svc.scoped(m.Master).where(m.Master.id == line_id, m.Master.kind == 'line'))
    factory = line.site_id if line else None
    if line is None and svc.settings.node_role == 'center':
        snapshot = svc.db.scalar(svc.scoped(m.Snapshot).where(m.Snapshot.scope_line_id == line_id).limit(1))
        factory = snapshot.site_id if snapshot else None
    if factory is None:
        fail(404, 'not_found', '产线不存在或不在授权的本站/已同步范围')
    if site_id and factory != site_id:
        fail(422, 'filter_line', '筛选产线与实际工厂不一致')
    return factory


def apply_scope(svc, query, cls, scope, shared=False):
    if scope.site_id:
        svc.actor.require('read', scope.site_id)
        query = query.where(cls.site_id.in_(['', scope.site_id]) if shared else cls.site_id == scope.site_id)
    if scope.line_id:
        readonly_line_scope(svc, scope.line_id, scope.site_id)
        query = query.where(cls.scope_line_id.in_(['', scope.line_id]) if shared else cls.scope_line_id == scope.line_id)
    payload = cls.context if cls == m.Case else cls.result if cls == m.Inspection else cls.data if cls == m.Snapshot else None
    if scope.product_id and payload is not None:
        product = payload['context']['product_id'] if cls == m.Snapshot else payload['product_id']
        query = query.where(product.as_string() == scope.product_id)
    elif scope.product_id and hasattr(cls, 'product_id'):
        query = query.where(cls.product_id == scope.product_id)
    elif scope.product_id and cls in (m.DecisionDefinition, m.MaterialFormulation, m.AutomationPolicy, m.BillOfMaterials):
        from .records import json_members
        query = json_members(query, svc, cls.scope['product_ids'], [scope.product_id])
    if scope.process:
        if cls in (m.Case, m.Route):
            query = query.where(cls.process == scope.process)
        elif cls in (m.Inspection, m.Snapshot):
            query = query.where(payload['process'].as_string() == scope.process)
        elif hasattr(cls, 'process'):
            query = query.where(cls.process == scope.process)
        elif cls in (m.DecisionDefinition, m.MaterialFormulation, m.AutomationPolicy, m.BillOfMaterials):
            query = query.where(cls.scope['process'].as_string() == scope.process)
        elif cls in m.ENTERPRISE_MODELS:
            # Materials, inventory and tasks inherit the actual line's process; filter before count.
            lines = svc.scoped(m.Master).with_only_columns(m.Master.id).where(
                m.Master.kind == 'line', m.Master.data['process'].as_string() == scope.process)
            query = query.where(cls.scope_line_id.in_(lines))
    if cls in (m.Master, m.Route, m.Knowledge):
        return query
    stamp = cls.occurred_at if cls in (m.Inspection, m.Measurement, m.LabResult) else cls.created_at
    if scope.start:
        query = query.where(stamp >= scope.start)
    if scope.end:
        query = query.where(stamp < scope.end)
    return query


def page_result(svc, query, cls, page, page_size, limit=500):
    from sqlalchemy import func
    if not 1 <= page <= 100000 or not 1 <= page_size <= limit:
        fail(422, 'pagination', '分页参数超出范围')
    total = svc.db.scalar(query.with_only_columns(func.count()).order_by(None)) or 0
    rows = svc.db.scalars(query.order_by(cls.created_at.desc(), cls.id).offset((page-1)*page_size).limit(page_size)).all()
    from .service import serialize
    return {'items': [serialize(r) for r in rows], 'total': total, 'page': page, 'page_size': page_size}
