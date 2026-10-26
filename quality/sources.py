"""Source inventory, reviewed mappings and scoped raw-record recovery. No device writes."""

import json
from typing import Literal

from fastapi import Depends, Request
from pydantic import Field
from sqlalchemy import func

from . import models as m
from .adapters import AdapterConfig, read_adapter
from .schemas import Strict
from .security import fail, current_time, digest
from .service import serialize


class PointMapping(Strict):
    name: str = Field(min_length=1, max_length=120)
    source_field: str = Field(default="", max_length=200)
    meaning: str = Field(min_length=2, max_length=500)
    value_type: Literal['measured', 'setpoint', 'manual', 'derived', 'uncollected']
    data_type: Literal['number', 'string', 'boolean', 'datetime'] = 'number'
    unit: str = Field(default='', max_length=40)
    time_field: str = Field(default='', max_length=120)
    quality_field: str = Field(default='', max_length=120)
    identity_fields: list[str] = Field(default_factory=list, max_length=30)
    heartbeat_seconds: int | None = Field(default=None, ge=1, le=86400)
    required: bool = False


class SourceRegistry(Strict):
    line_id: str
    step_id: str = ''
    source_system: str = Field(min_length=2, max_length=200)
    owner: str = Field(min_length=2, max_length=120)
    authentication: Literal['environment_reference', 'certificate', 'source_token', 'manual']
    read_scope: str = Field(min_length=3, max_length=1000)
    time_source: str = Field(min_length=2, max_length=300)
    sample_period_seconds: int | None = Field(default=None, ge=1, le=86400)
    retention_days: int = Field(default=90, ge=1, le=3650)
    points: list[PointMapping] = Field(default_factory=list, max_length=1000)


class MappingDraft(Strict):
    base_version: str = Field(min_length=1, max_length=100)
    config: AdapterConfig
    reason: str = Field(min_length=4, max_length=2000)


class MappingReview(Strict):
    version: int = Field(ge=1)
    action: Literal['publish', 'reject', 'rollback']
    reason: str = Field(min_length=4, max_length=2000)


class SourceReason(Strict):
    reason: str = Field(min_length=4, max_length=2000)


class SourceDecision(SourceReason):
    enabled: bool = False


def validate_connection(config, protocol, settings):
    from .security import endpoint
    endpoint(config.endpoint, settings, {'http': ('http', 'https'), 'mqtt': ('mqtt', 'mqtts'), 'opcua': ('opc.tcp',)}[protocol])
    if config.mode != 'read':
        fail(422, 'read_only', '适配器仅允许读取遥测，不允许设备写入')
    if config.secret_env and not config.secret_env.startswith('QUALITY_CONNECTOR_'):
        fail(422, 'secret_reference', '凭据必须引用受控服务端环境变量')
    if protocol == 'mqtt' and any(part.lower() in {'cmd', 'command', 'commands', 'control', 'set'}
                                  for part in config.topic.split('/')):
        fail(422, 'telemetry_topic', '只允许读取已批准遥测主题，不订阅设备命令主题')
    if settings.mode == 'production' and not config.line_id:
        fail(422, 'source_line', '生产采集必须绑定实际产线及点表')


def redacted(value):
    if isinstance(value, dict):
        return {k: ('[已脱敏]' if any(t in k.lower() for t in ('password','secret','token','credential','private_key','authorization','api_key','apikey','cookie'))
                    else redacted(v)) for k,v in value.items()}
    if isinstance(value, list):
        return [redacted(v) for v in value[:20]]
    return value[:500] if isinstance(value, str) else value


def register_sources(app, service, command):
    @app.post('/api/adapters/{id}/rotate-token')
    def rotate_token(id: str, data: SourceReason, request: Request, svc=Depends(service)):
        svc.actor.require('admin', svc.settings.site_id)
        def apply():
            from .security import token
            adapter = svc.get(m.Adapter, id, True)
            if adapter.protocol != 'visual':
                fail(422, 'source_protocol', '此操作仅轮换本站视觉推送令牌')
            raw = token()
            adapter.config = {**adapter.config, 'token_hash': digest(raw)}
            svc.audit('source.token_rotated', id, {'reason': data.reason})
            return {'id': id, 'token': raw, 'requires_manual_secret_update': True}
        return command(svc, request, data.model_dump(), apply)

    @app.get('/api/measurements')
    def measurements(line_id: str = '', equipment_id: str = '', page: int = 1, page_size: int = 50, svc=Depends(service)):
        svc.actor.require('read')
        if not 1 <= page <= 100000 or not 1 <= page_size <= 500:
            fail(422, 'pagination', '分页参数超出范围')
        query = svc.scoped(m.Measurement)
        if line_id:
            svc.get(m.Master, line_id)
            query = query.where(m.Measurement.scope_line_id == line_id)
        if equipment_id:
            query = query.where(m.Measurement.equipment_id == equipment_id)
        total = svc.db.scalar(query.with_only_columns(func.count())) or 0
        rows = svc.db.scalars(query.order_by(m.Measurement.occurred_at.desc(), m.Measurement.id).offset((page-1)*page_size).limit(page_size)).all()
        return {'items': [serialize(r) for r in rows], 'total': total, 'page': page, 'page_size': page_size}

    @app.post('/api/adapters/{id}/registry')
    def registry(id: str, data: SourceRegistry, request: Request, svc=Depends(service)):
        svc.actor.require('admin', svc.settings.site_id, data.line_id)
        def apply():
            adapter = svc.get(m.Adapter, id, True)
            line = svc.get(m.Master, data.line_id)
            if line.kind != 'line':
                fail(422, 'source_line', '来源台账必须绑定实际产线')
            if adapter.scope_line_id and not adapter.scope_line_id.startswith('unassigned:') and adapter.scope_line_id != line.id:
                fail(409, 'source_rebind', '已有来源不能改绑另一产线；请新建独立来源')
            if adapter.protocol == 'visual' and adapter.config['line_id'] != line.id:
                fail(422, 'source_binding', '来源台账与视觉源绑定不一致')
            if data.step_id:
                route = svc.get(m.Route, line.data.get('route_id', ''))
                if data.step_id not in {s['id'] for s in route.steps}:
                    fail(422, 'source_step', '采集位置不属于当前产线路线')
            previous = adapter.source_registry.get('inventory', {})
            history = adapter.source_registry.get('inventory_history', [])
            adapter.source_registry = {**adapter.source_registry, 'inventory': data.model_dump(),
                                'inventory_history': [*history, {'before': previous, 'actor_id': svc.actor.id,
                                                               'changed_at': current_time().isoformat()}]}
            adapter.scope_line_id = line.id
            if adapter.protocol != 'visual':
                adapter.config = {**adapter.config, 'line_id': line.id}
            svc.audit('source.registry_updated', id, {'line_id': line.id})
            return serialize(adapter)
        return command(svc, request, data.model_dump(), apply)

    @app.post('/api/adapters/{id}/mapping')
    def draft(id: str, data: MappingDraft, request: Request, svc=Depends(service)):
        svc.actor.require('admin', svc.settings.site_id)
        def apply():
            adapter = svc.get(m.Adapter, id, True)
            if adapter.protocol == 'visual':
                fail(422, 'source_protocol', '视觉推送使用标准合同，不通过采集映射改写')
            validate_connection(data.config, adapter.protocol, svc.settings)
            if data.base_version != adapter.config.get('mapping_version', 'legacy-v1'):
                fail(409, 'mapping_version', '当前映射已变更，请重新预览差异')
            if data.config.line_id != adapter.config.get('line_id', ''):
                fail(422, 'source_rebind', '映射版本不能改变已绑定产线')
            versions = adapter.source_registry.get('mapping_versions', [])
            revision = max([v['version'] for v in versions], default=0) + 1
            config = {**data.config.model_dump(), 'mapping_version': f'mapping-v{revision}'}
            row = {'version': revision, 'status': 'pending_review', 'author_id': svc.actor.id,
                   'created_at': current_time().isoformat(), 'config': config, 'reason': data.reason,
                   'base_config': adapter.config, 'base_hash': digest(adapter.config)}
            adapter.source_registry = {**adapter.source_registry, 'mapping_versions': [*versions, row]}
            svc.audit('source.mapping_submitted', id, {'version': revision})
            return row
        return command(svc, request, data.model_dump(), apply)

    @app.post('/api/adapters/{id}/mapping/review')
    def review(id: str, data: MappingReview, request: Request, svc=Depends(service)):
        svc.actor.require('knowledge_review', svc.settings.site_id)
        def apply():
            adapter = svc.get(m.Adapter, id, True)
            versions = [dict(v) for v in adapter.source_registry.get('mapping_versions', [])]
            row = next((v for v in versions if v['version'] == data.version), None)
            if not row:
                fail(404, 'mapping_missing', '映射版本不存在')
            if row['author_id'] == svc.actor.id:
                fail(403, 'separation_of_duties', '采集映射作者不能审核自己的配置')
            if data.action == 'rollback':
                if row['status'] != 'published' or digest(adapter.config) != digest(row['config']):
                    fail(409, 'mapping_state', '只能回退当前生效且经审核的映射')
                target = row['base_config']
                validate_connection(AdapterConfig.model_validate(target), adapter.protocol, svc.settings)
                adapter.config, row['status'] = target, 'rolled_back'
            else:
                if row['status'] != 'pending_review' or digest(adapter.config) != row['base_hash']:
                    fail(409, 'mapping_state', '映射状态或生效基线已变化')
                row['status'] = 'published' if data.action == 'publish' else 'rejected'
                if data.action == 'publish':
                    validate_connection(AdapterConfig.model_validate(row['config']), adapter.protocol, svc.settings)
                    adapter.config = row['config']
            row.update(reviewer_id=svc.actor.id, review_reason=data.reason, reviewed_at=current_time().isoformat())
            adapter.source_registry = {**adapter.source_registry, 'mapping_versions': versions, 'mapping_governance': 'reviewed'}
            svc.audit('source.mapping_'+data.action, id, {'version': data.version})
            return row
        return command(svc, request, data.model_dump(), apply)

    @app.post('/api/adapters/{id}/enabled')
    def enabled(id: str, data: SourceDecision, request: Request, svc=Depends(service)):
        svc.actor.require('admin', svc.settings.site_id)
        def apply():
            adapter = svc.get(m.Adapter, id, True)
            if data.enabled and svc.settings.mode == 'production' and adapter.protocol != 'visual':
                if not adapter.source_registry.get('inventory') or adapter.source_registry.get('mapping_governance') != 'reviewed':
                    fail(409, 'source_unreviewed', '生产采集启用前须完成来源点表与独立映射审核')
            adapter.enabled = data.enabled
            svc.audit('source.enabled_changed', id, data.model_dump())
            return serialize(adapter)
        return command(svc, request, data.model_dump(), apply)

    @app.post('/api/adapters/{id}/sample')
    def sample(id: str, svc=Depends(service)):
        svc.actor.require('admin', svc.settings.site_id)
        adapter = svc.get(m.Adapter, id)
        if adapter.protocol == 'visual':
            fail(422, 'source_protocol', '视觉源由供应商推送，不能拉取伪造样本')
        try:
            result = read_adapter(adapter.protocol, adapter.config, svc.settings)
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc, HTTPException):
                raise
            # Do not echo provider response bodies, endpoints or credentials.
            fail(502, 'source_read_failed', '只读连接未成功；检查服务可达性、凭据引用、证书和来源日志')
        svc.audit('source.sample_read', id, {'count': min(5, len(result['records']))})
        return {'samples': redacted(result['records'][:5]), 'read_only': True,
                'field_acceptance': 'not_performed', 'persisted_business_records': False}

    @app.get('/api/raw-records')
    def raw_records(source_id: str = '', status: str = '', page: int = 1, page_size: int = 50, svc=Depends(service)):
        svc.actor.require('admin', svc.settings.site_id)
        if not 1 <= page <= 100000 or not 1 <= page_size <= 200:
            fail(422, 'pagination', '分页参数超出范围')
        query = svc.scoped(m.RawRecord)
        if source_id:
            svc.get(m.Adapter, source_id)
            query = query.where(m.RawRecord.source_id == source_id)
        if status:
            query = query.where(m.RawRecord.status == status)
        total = svc.db.scalar(query.with_only_columns(func.count())) or 0
        return {'items': [serialize(r) for r in svc.db.scalars(query.order_by(m.RawRecord.created_at.desc()).offset((page-1)*page_size).limit(page_size))],
                'total': total, 'page': page, 'page_size': page_size}

    @app.get('/api/raw-records/{id}/sample')
    def raw_sample(id: str, svc=Depends(service)):
        svc.actor.require('admin', svc.settings.site_id)
        row = svc.get(m.RawRecord, id)
        path = (svc.settings.data_dir/'ingestion-raw'/row.object_key).resolve()
        if not path.is_relative_to((svc.settings.data_dir/'ingestion-raw').resolve()):
            fail(422, 'raw_path', '原始记录路径越界')
        payload = json.loads(path.read_text())
        if digest(json.dumps(payload['raw'],ensure_ascii=False,sort_keys=True,default=str).encode()) != row.payload_hash:
            fail(409, 'raw_integrity', '原始记录哈希不匹配')
        svc.audit('raw.sample_read', id)
        return {'record': serialize(row), 'sample': redacted(payload['raw'])}

    @app.post('/api/raw-records/{id}/reprocess')
    def reprocess(id: str, data: SourceReason, request: Request, svc=Depends(service)):
        svc.actor.require('admin', svc.settings.site_id)
        def apply():
            from .ingestion import apply_raw, error_code
            row = svc.get(m.RawRecord, id, True)
            adapter = svc.get(m.Adapter, row.source_id)
            if row.status == 'conflict':
                fail(409, 'source_record_conflict', '同事件不同载荷须向来源追加更正，不能将冲突强行覆盖')
            try:
                with svc.db.begin_nested():
                    inserted = apply_raw(svc, adapter, row)
            except Exception as exc:
                row.status, row.errors = 'quarantined', [{'code': error_code(exc)}]
                inserted = 0
            svc.audit('raw.reprocess_requested', id, {'reason': data.reason, 'inserted': inserted})
            return serialize(row)
        return command(svc, request, data.model_dump(), apply)
