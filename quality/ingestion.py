import time
import json
import os
from datetime import datetime
from . import models as m, schemas as s
from .adapters import AdapterConfig, mapped, normalize, read_adapter
from .security import current_time, digest, fail


def error_code(exc):
    value=str(exc).split(':',1)[0]
    allowed={'missing_fields','missing_field','timestamp_requires_timezone','source_clock_ahead',
             'boolean_is_not_measurement','non_finite_value','unknown_value_type','unit_mismatch',
             'missing_source_timestamp','external_master_kind_denied','load_reference'}
    if value in allowed:
        return value
    detail=getattr(exc,'detail',None)
    return detail['code'] if isinstance(detail,dict) and isinstance(detail.get('code'),str) else type(exc).__name__


def committed_marker(row, path):
    from sqlalchemy import inspect
    if inspect(row).persistent:
        path.with_suffix('.done').touch(exist_ok=True)


def stage_raw(svc, adapter, raw, config):
    """Transfer raw-record ownership to durable local storage before normalization."""
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode()
    if len(encoded) > min(svc.settings.max_upload_bytes, 1024**2):
        raise ValueError('raw_record_too_large')
    payload_hash = digest(encoded)
    try:
        identity = ({'record_id': digest([raw.get('node_id'), raw.get('occurred_at')])}
                    if adapter.protocol == 'opcua' else mapped(raw, config))
    except (ValueError, TypeError, KeyError):
        identity = raw if isinstance(raw, dict) else {}
    record_id = str(identity.get('record_id') or 'missing:' + payload_hash)
    session_id = str(identity.get('source_session_id', ''))
    if config.source_instance_id:
        session_id = digest([config.source_instance_id, session_id])
    if len(record_id) > 160 or len(session_id) > 100:
        raise ValueError('source_identity_too_long')
    id = digest([svc.settings.tenant_id, svc.settings.site_id, adapter.id, session_id, record_id, payload_hash])[:40]
    previous = svc.db.get(m.RawRecord, id)
    if previous:
        return previous
    folder = svc.settings.data_dir / 'ingestion-raw'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    used = sum(p.stat().st_size for p in folder.glob('*.json'))
    if used + len(encoded) > svc.settings.cache_max_bytes:
        fail(507, 'raw_cache_full', '原始接收缓存已满，拒绝新增且不删除原始记录')
    filename = id + '.json'
    envelope = {'id': id, 'tenant_id': svc.settings.tenant_id, 'site_id': svc.settings.site_id,
                'source_id': adapter.id, 'received_at': current_time().isoformat(),
                'mapping': config.model_dump(), 'payload_hash': payload_hash, 'raw': raw}
    path = folder / filename
    try:
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
            json.dump(envelope, output, ensure_ascii=False, sort_keys=True, default=str)
            output.flush()
            os.fsync(output.fileno())
        directory_fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        saved = json.loads(path.read_text())
        if saved.get('payload_hash') != payload_hash or saved.get('source_id') != adapter.id:
            fail(409, 'raw_integrity', '原始接收缓存标识冲突，禁止覆盖')
        envelope = saved
    conflict = svc.db.scalar(svc.scoped(m.RawRecord).where(
        m.RawRecord.source_id == adapter.id, m.RawRecord.record_id == record_id,
        m.RawRecord.source_session_id == session_id, m.RawRecord.payload_hash != payload_hash,
    ).limit(1))
    row = svc.add(m.RawRecord, id=id, source_id=adapter.id, record_id=record_id,
                   source_session_id=session_id, payload_hash=payload_hash, object_key=filename,
                   mapping_version=config.mapping_version,
                   received_at=datetime.fromisoformat(envelope['received_at']),
                   scope_line_id=config.line_id or 'unassigned:' + svc.actor.id,
                   status='conflict' if conflict else 'received',
                   errors=[{'code': 'source_record_conflict'}] if conflict else [])
    svc.db.info.setdefault('after_commit', []).append(lambda r=row, p=path: committed_marker(r, p))
    return row


def recover_raw(svc):
    """Recover fsynced records after process death, before reading any new source data."""
    folder = svc.settings.data_dir/'ingestion-raw'
    recovered = 0
    attempted = 0
    for path in sorted(folder.glob('*.json')):
        if path.with_suffix('.done').exists():
            continue
        attempted += 1
        if attempted > 500:
            break
        try:
            envelope = json.loads(path.read_text())
            if envelope['tenant_id'] != svc.settings.tenant_id or envelope['site_id'] != svc.settings.site_id:
                continue
            if envelope.get('raw',{}).get('contract') == 'actual-passage-v1':
                from .actual_passages import recover_envelope
                with svc.db.begin_nested():
                    row=recover_envelope(svc,envelope)
                    svc.db.info.setdefault('after_commit', []).append(lambda r=row, p=path: committed_marker(r,p))
                    recovered+=1
                continue
            adapter = svc.get(m.Adapter, envelope['source_id'])
            if not adapter.enabled:
                continue
            with svc.db.begin_nested():
                row = stage_raw(svc, adapter, envelope['raw'], AdapterConfig.model_validate(envelope['mapping']))
                try:
                    with svc.db.begin_nested():
                        apply_raw(svc, adapter, row)
                except Exception as exc:
                    row.status, row.errors = 'quarantined', [{'code': error_code(exc)}]
                svc.db.info.setdefault('after_commit', []).append(lambda r=row, p=path: committed_marker(r, p))
                recovered += 1
        except Exception as exc:
            svc.audit('raw.recovery_failed', '', {'file_id': path.stem, 'error_type': type(exc).__name__})
        if recovered >= 500:
            break
    return recovered


def apply_raw(svc, adapter, row, config=None):
    """Normalize a retained raw record. Mapping changes append corrected measurements."""
    if row.status == 'conflict':
        return 0
    parsed = config or (AdapterConfig(endpoint='http://localhost', line_id=adapter.config.get('line_id', ''),
                                      mapping_version=adapter.config.get('mapping_version', 'legacy-v1'),
                                      source_instance_id=adapter.config.get('source_instance_id', ''))
                        if adapter.protocol == 'visual' else AdapterConfig.model_validate(adapter.config))
    path = (svc.settings.data_dir / 'ingestion-raw' / row.object_key).resolve()
    if not path.is_relative_to((svc.settings.data_dir / 'ingestion-raw').resolve()):
        fail(422, 'raw_path', '原始接收缓存路径越界')
    envelope = json.loads(path.read_text())
    raw = envelope['raw']
    if digest(json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode()) != row.payload_hash:
        fail(409, 'raw_integrity', '原始接收记录完整性失败')
    if row.status == 'processed' and row.mapping_version == parsed.mapping_version:
        return 0
    if adapter.protocol == 'visual':
        from .visual import ingest_inspection
        record = s.InspectionInput.model_validate(raw)
        result = ingest_inspection(svc, record, adapter.id, adapter.provenance)
        row.source_time = record.occurred_at
        row.normalized_ids = [result['id']]
        row.mapping_version = parsed.mapping_version
        row.status = ('processed' if result['status'] in {'OK','NG'} else
                      'pending_association' if result['status'] == 'unassociated' else 'quarantined')
        row.errors = [] if row.status == 'processed' else [{'code': result['status']}]
        svc.audit('raw.visual_processed', row.id, {'inspection_id': result['id'], 'status': row.status})
        return 1
    if parsed.record_type == 'master' and adapter.protocol == 'http':
        record = mapped(raw, parsed)
        allowed = {'work_order', 'production_batch', 'material_batch', 'product', 'substrate', 'hanger', 'load', 'equipment_run'}
        if parsed.master_kind not in allowed or record.get('kind', parsed.master_kind) != parsed.master_kind:
            raise ValueError('external_master_kind_denied')
        value = s.MasterInput.model_validate({**record, 'kind': parsed.master_kind})
        if value.kind == 'load':
            for key, kind in [('hanger_id', 'hanger'), ('production_batch_id', 'production_batch')]:
                if svc.get(m.Master, value.data.get(key, '')).kind != kind:
                    raise ValueError('load_reference')
        if value.parent_id:
            svc.get(m.Master, value.parent_id)
        old = svc.db.scalar(svc.scoped(m.Master).where(m.Master.kind == value.kind, m.Master.code == value.code))
        data = {**value.data, 'source_adapter': adapter.id, 'raw_record_id': row.id, 'mapping_version': parsed.mapping_version}
        if old:
            svc.audit('master.source_revision', old.id, {'before': {'name': old.name, 'data': old.data}, 'raw_record_id': row.id})
            old.name, old.data, old.version = value.name, data, old.version + 1
            normalized_id = old.id
        else:
            normalized_id = svc.add(m.Master, **value.model_dump(exclude={'data'}), data=data).id
    else:
        if adapter.protocol == 'opcua':
            if not raw.get('occurred_at'):
                raise ValueError('missing_source_timestamp')
            raw = {'record_id': digest([raw['node_id'], raw['occurred_at'], raw['value']]),
                   'equipment_id': raw['node_id'], 'metric': raw['node_id'], 'value': raw['value'],
                   'unit': parsed.units.get(raw['node_id'], 'unknown'), 'occurred_at': raw['occurred_at'],
                   'quality': raw['quality'], 'value_type': 'measured',
                   'opc_status_code': raw.get('status_code'), 'server_timestamp': raw.get('server_timestamp')}
        record = normalize(raw, parsed.model_copy(update={'fields': {}}) if adapter.protocol == 'opcua' else parsed)
        row.source_time = record['occurred_at']
        record_key = digest([record['record_id'], row.source_session_id, parsed.mapping_version])
        exists = svc.db.scalar(svc.scoped(m.Measurement).where(
            m.Measurement.source_id == adapter.id, m.Measurement.record_id == record_key,
            m.Measurement.metric == record['metric'],
        ))
        if exists:
            if (exists.value, exists.unit, exists.value_type, exists.quality) != (
                record['value'], record['unit'], record['value_type'], record['quality']):
                row.status, row.errors = 'conflict', [{'code': 'normalized_record_conflict'}]
                return 0
            normalized_id = exists.id
        else:
            previous_ids = list(row.normalized_ids)
            obj = svc.add(m.Measurement, scope_line_id=parsed.line_id or row.scope_line_id,
                source_id=adapter.id, record_id=record_key,
                **{k: record[k] for k in ('equipment_id','metric','value','unit','occurred_at','quality','value_type')},
                data={'provenance': adapter.provenance, 'raw_record_id': row.id,
                      'identity': {key: record[key] for key in ('object_id','production_batch_id','material_batch_id','furnace_run_id','load_id','work_order_id','product_id','step_id','route_id','granularity','substrate_id') if record.get(key)},
                      'original_value': record['original_value'], 'original_unit': record['original_unit'],
                      'original_timestamp': record['original_timestamp'], 'received_at': row.received_at.isoformat(),
                      'mapping_version': parsed.mapping_version, 'source_session_id': record['source_session_id'],
                      'granularity': record.get('granularity', ''), 'association': record.get('association', 'unresolved'),
                      'source_instance_id': parsed.source_instance_id,
                      'supersedes': previous_ids, 'opc_status_code': record.get('opc_status_code'),
                      'server_timestamp': record.get('server_timestamp')})
            normalized_id = obj.id
    row.normalized_ids = list(dict.fromkeys([*row.normalized_ids, normalized_id]))
    row.status, row.errors, row.mapping_version = 'processed', [], parsed.mapping_version
    if parsed.record_type == 'measurement' and record['quality'] != 'Good':
        row.status, row.errors = 'quarantined', [{'code': 'source_quality', 'quality': record['quality']}]
    svc.audit('raw.processed', row.id, {'mapping_version': parsed.mapping_version, 'normalized_id': normalized_id})
    return 1


def collect_adapter(svc, adapter_id):
    id = adapter_id
    settings = svc.settings
    adapter = svc.get(m.Adapter, id, True)
    if not adapter.enabled:
        fail(409, "adapter_disabled", "连接未启用")
    if settings.mode == "production" and adapter.provenance != "real":
        fail(409, "simulation_in_production", "生产不能采集模拟数据源")
    start = time.monotonic()
    try:
        result = read_adapter(adapter.protocol, adapter.config, settings)
        inserted, errors, durably_received = 0, [], 0
        for i, raw in enumerate(result["records"]):
            row = None
            try:
                parsed = AdapterConfig.model_validate(adapter.config)
                row = stage_raw(svc, adapter, raw, parsed)
                durably_received += 1
                with svc.db.begin_nested():
                    inserted += apply_raw(svc, adapter, row, parsed)
                if row.status == 'conflict':
                    errors.append({'row': i + 1, 'raw_record_id': row.id, 'error': 'source_record_conflict'})
                elif row.status == 'quarantined':
                    errors.append({'row': i + 1, 'raw_record_id': row.id, 'error': 'source_quality'})
            except Exception as exc:
                detail = {'row': i + 1, 'error': error_code(exc)}
                if row is not None:
                    row.status, row.errors = 'quarantined', [detail]
                    detail = {**detail, 'raw_record_id': row.id}
                errors.append(detail)
        adapter.health = {
            "status": "ready" if not errors else "data_quality_error",
            "last_success": current_time().isoformat(),
            "last_connected_at": current_time().isoformat(),
            "latest_source_time": max((r.source_time.isoformat() for r in svc.db.scalars(
                svc.scoped(m.RawRecord).where(m.RawRecord.source_id == id, m.RawRecord.source_time.is_not(None))
            )), default=None),
            "inserted": inserted,
            "errors": errors[:100],
            "duration_ms": round((time.monotonic() - start) * 1000),
            "connection_validation": "reference"
            if adapter.provenance != "real"
            else "connected_not_accepted",
            "field_acceptance": "not_performed",
        }
        if "cursor" in result and durably_received == len(result['records']):
            adapter.config = {**adapter.config, "cursor": result["cursor"]}
        if durably_received == len(result['records']):
            from pathlib import Path

            for filename in result.get("spool_paths", []):
                path = Path(filename).resolve()
                if not path.is_relative_to((settings.data_dir / "adapter-spool").resolve()):
                    raise ValueError("spool_path_outside_managed_directory")
                svc.db.info.setdefault("after_commit", []).append(
                    lambda p=path: p.with_suffix(".done").touch(exist_ok=True)
                )
    except Exception as exc:
        adapter.health = {
            **adapter.health,
            "status": "unavailable",
            "error_type": type(exc).__name__,
            "last_attempt": current_time().isoformat(),
            "field_acceptance": "not_performed",
        }
    svc.audit("adapter.polled", id, adapter.health)
    adapter.health = {**adapter.health, "last_attempt": current_time().isoformat()}
    return adapter.health
