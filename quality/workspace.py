"""Typed, scoped assistant responsibilities and actionable workbench data; no autonomous control."""

from datetime import datetime
from typing import Literal

from fastapi import Depends, Request
from pydantic import Field

from . import models as m
from .querying import QueryScope, apply_scope, get_scope
from .schemas import Strict, Action
from .scopes import applicable
from .security import current_time, aware, fail
from .service import serialize
from .templates import effective_steps


class AssistantInput(Strict):
    case_id: str = ''
    version: int | None = Field(default=None, ge=1)
    query: str = Field(default='', max_length=1000)
    scope: QueryScope = Field(default_factory=QueryScope)


class ToolReference(Strict):
    kind: Literal['case','knowledge','inspection','passage','measurement','evidence','plan','retest','run','raw_record','lab']
    id: str
    version: int | None = None


class ToolResult(Strict):
    role: Literal['A1','A2','A3','A4','A5','A6']
    status: Literal['success','no_data','partial','stale','forbidden','failure']
    generated_at: datetime
    engine: Literal['deterministic_tools'] = 'deterministic_tools'
    scope: dict
    references: list[ToolReference]
    data: dict
    limitations: list[str]
    workflow_version: str = 'delivery-tools-v1'


def assistant_result(svc, role, data):
    svc.actor.require('read')
    if role not in {'A1','A2','A3','A4','A5','A6'}:
        fail(404, 'assistant_role', '只支持六类受控业务职责，不接受任意工具或命令')
    case = svc.get(m.Case, data.case_id) if data.case_id else None
    if role in {'A2','A3','A4','A5'} and case is None:
        fail(422, 'assistant_case', '此职责需要明确案件及当前版本')
    if case and data.version != case.version:
        fail(409, 'version_conflict', '案件版本已变化，请刷新证据和草稿')
    if role == 'A4':
        svc.actor.require('plan', case.site_id, case.line_id)
    scope = QueryScope(site_id=case.site_id, line_id=case.line_id, product_id=case.context.get('product_id',''),
                       process=case.process) if case else data.scope
    apply_scope(svc, svc.scoped(m.Case), m.Case, scope)  # Validate even a no-data request's scope.
    references = [ToolReference(kind='case', id=case.id, version=case.version)] if case else []
    status = 'success'
    limitations = ['确定性工具结果，不冒充模型调用；不自动审批、放行或控制设备。']
    model = None
    if case and role in {'A3', 'A4'}:
        from .model_assistance import assistance
        model = assistance(svc, case)
    if role == 'A1':
        context = case.context if case else {'product_id': scope.product_id}
        settings = svc.settings.model_copy(update={'site_id': scope.site_id}) if scope.site_id else svc.settings
        entries = [k for k in svc.db.scalars(svc.scoped(m.Knowledge).where(m.Knowledge.status == 'published'))
                   if aware(k.valid_until) >= current_time()
                   and applicable(k.scope, settings, scope.process, scope.line_id, context)
                   and (not data.query or data.query.lower() in (k.title+' '+k.content).lower())][:20]
        references.extend(ToolReference(kind='knowledge', id=k.id, version=k.revision) for k in entries)
        result = {'knowledge': [serialize(k) for k in entries], 'approved_only': True}
        if not entries:
            status = 'no_data'
        limitations.append('只返回授权且适用的已发布知识；未选择工艺/产线可能没有适用结果。')
    elif role == 'A2':
        refs = case.diagnosis.get('evidence_refs', [])
        references.extend(ToolReference(kind='inspection', id=i['id']) for i in case.context.get('inspection_snapshots', []))
        references.extend(ToolReference(kind='evidence', id=i) for i in case.context.get('evidence_ids', []))
        result = {'context': case.context, 'analysis_snapshot': case.context.get('analysis_snapshot'),
                  'missing_evidence': case.diagnosis.get('missing_evidence', []), 'evidence_refs': refs,
                  'association': case.context.get('association','needs_check')}
        if not result['analysis_snapshot'] or result['missing_evidence']:
            status = 'partial'
    elif role == 'A3':
        candidates = case.diagnosis.get('candidates', [])
        result = {'dimensions': [{
            'dimension': dimension,
            'candidates': [v for v in candidates if v['dimension'] == dimension],
            'state': 'evidence_available' if any(v['dimension'] == dimension for v in candidates) else 'insufficient_evidence',
        } for dimension in ['equipment','material','process','environment','management']],
                  'run_id': case.diagnosis.get('run_id'), 'missing_evidence': case.diagnosis.get('missing_evidence', []),
                  'model_status': case.diagnosis.get('model_status','not_run')}
        result['model'] = model
        if result['run_id']:
            references.append(ToolReference(kind='run', id=result['run_id']))
        status = 'partial' if any(not d['candidates'] for d in result['dimensions']) else 'success'
        limitations.append('没有证据的维度不补评分；相对排序不代表因果概率，作业管理不自动归责个人。')
    elif role == 'A4':
        checks = list(dict.fromkeys(t['description'] for t in case.diagnosis.get('evidence_tasks', []) if t['status'] == 'pending'))[:10]
        suggestions = [Action(type='inspect', description=v[:1000]).model_dump() for v in checks if len(v) >= 2]
        combined = []
        seen = set()
        for action in model['actions'] + suggestions:
            signature = (action['type'], action['description'], action.get('sop_id'), str(sorted(action.get('parameters', {}).items())))
            if signature not in seen:
                combined.append(action)
                seen.add(signature)
        suggestions = combined[:30]
        result = {'actions': suggestions, 'executable': False, 'case_id': case.id, 'case_version': case.version,
                  'model': model, 'draft_origin': 'validated_model_and_evidence_checks' if model['draft_available'] else 'evidence_checks',
                  'responsibility': '工程师补全，独立审批人审核，现场执行人留回执',
                  'required_retest_fields': ['method','target_defects','sample_unit','min_samples','max_defect_rate',
                                             'stabilization_minutes','observation_minutes','conditions','other_constraints']}
        status = 'partial'
        limitations.append('只起草核查动作，不编造正式参数、样本量或效果阈值；人必须补全复测方案后另存草稿/提交审批。')
        if model['run_id']:
            references.append(ToolReference(kind='run', id=model['run_id']))
        if model['draft_available']:
            limitations.append('已合并真实模型运行中通过校验的动作候选；人员责任、复测标准和提交审批须由工程师核对。读取既有运行记录，不再次调用模型。')
    elif role == 'A5':
        retests = svc.db.scalars(svc.scoped(m.Retest).where(m.Retest.case_id == case.id).order_by(m.Retest.created_at)).all()
        references.extend(ToolReference(kind='retest', id=r.id) for r in retests)
        from .statistics import normalized_observations
        snapshots = [r for r in normalized_observations(case.context.get('inspection_snapshots', [])) if not r.get('statistical_conflict')]
        units = {r['granularity'] for r in snapshots if r['status'] in {'OK','NG'}}
        denominator = sum(r['result']['inspected_count'] for r in snapshots if r['status'] in {'OK','NG'})
        numerator = sum(r['result']['defective_count'] for r in snapshots if r['status'] in {'OK','NG'})
        result = {'before': {'sample_count': denominator if len(units)==1 else None,
                             'defective_count': numerator if len(units)==1 else None,
                             'sample_unit': next(iter(units)) if len(units)==1 else 'unavailable'},
                  'retests': [serialize(r) for r in retests], 'outcome': case.outcome,
                  'causal_conclusion': 'requires_independent_human_confirmation', 'comparison_confirmed': False}
        status = 'partial' if retests else 'no_data'
        limitations.append('真实前后计数不自动证明可比：产品、材料、工艺/检验版本、时间和执行范围仍需人核验；未自动结案。')
    else:
        from .statistics import quality_statistics
        result = quality_statistics(svc, scope.start, scope.end, '', scope.site_id, scope.line_id, scope.product_id, scope.process)
        status = 'no_data' if not result['record_count'] and not result['case_count'] else 'partial'
        limitations.append('跨厂/批次仅按可比口径解释；缺分母、终态放行或客户指标时不制造收益/OEE/准确率。')
    return ToolResult(role=role, status=status, generated_at=current_time(), scope=scope.model_dump(mode='json'),
                      references=references, data=result, limitations=limitations).model_dump(mode='json')


def register_workspace(app, service, command):
    @app.get('/api/evidence-records/{kind}/{id}')
    def evidence_record(kind: str, id: str, svc=Depends(service)):
        cls = {'case': m.Case, 'knowledge': m.Knowledge, 'inspection': m.Inspection,
               'passage': m.Passage, 'measurement': m.Measurement, 'lab': m.LabResult, 'evidence': m.Evidence,
               'plan': m.Plan, 'retest': m.Retest, 'run': m.Run, 'raw_record': m.RawRecord, 'snapshot': m.Snapshot, 'learning': m.LearningRecord}.get(kind)
        if cls is None:
            fail(404, 'evidence_kind', '不支持任意对象或外部地址读取')
        svc.actor.require('admin' if cls == m.RawRecord else 'read')
        if cls == m.LearningRecord:
            from .learning import get_record
            row = get_record(svc, id)
        else:
            row = svc.get(cls, id)
        result = {'kind': kind, 'record': serialize(row)}
        if cls == m.Evidence:
            result['content_url'] = '/api/files/'+row.id
        svc.audit('evidence_record.read', row.id, {'kind': kind})
        return result

    @app.get('/api/workspace/scope-options')
    def scope_options(svc=Depends(service)):
        lines = {r.id: serialize(r) for r in svc.db.scalars(svc.scoped(m.Master).where(m.Master.kind == 'line'))}
        if svc.settings.node_role == 'center':
            for row in svc.db.scalars(svc.scoped(m.Snapshot).order_by(m.Snapshot.created_at.desc())):
                if row.scope_line_id and row.scope_line_id not in lines:
                    lines[row.scope_line_id] = {'id': row.scope_line_id, 'site_id': row.site_id,
                        'name': row.data.get('line_name') or '已同步产线 '+row.scope_line_id[:8],
                        'source_node': row.source_node, 'data': {'process': row.data.get('process')},
                        'authority': 'read_only_remote_summary'}
        return {'sites': svc.actor.sites, 'lines': list(lines.values()),
                'limitation': '中心产线来自授权摘要，属于只读筛选，不代表取得现场主数据写入权'}

    @app.post('/api/assistants/{role}', response_model=ToolResult)
    def assistant(role: str, data: AssistantInput, request: Request, svc=Depends(service)):
        def apply():
            result = assistant_result(svc, role, data)
            from .security import digest
            case = svc.get(m.Case, data.case_id) if data.case_id else None
            svc.add(m.ToolInvocation, actor_id=svc.actor.id, role=role, tool='legacy.assistant.'+role,
                    tool_version='delivery-tools-v1', status=result['status'],
                    input_hash=digest(data.model_dump(mode='json')), input=data.model_dump(mode='json'),
                    result=result, authorization={'role': svc.actor.role, 'engine': 'deterministic_tools',
                    'permission': 'read'}, evidence_ids=[r['id'] for r in result['references'] if r['kind']=='evidence'],
                    scope_line_id=case.line_id if case else data.scope.line_id or 'unassigned:'+svc.actor.id)
            svc.audit('assistant.tool_read', data.case_id or role, {'role': role, 'status': result['status'],
                      'workflow_version': result['workflow_version'], 'reference_count': len(result['references'])})
            return result
        return command(svc, request, data.model_dump(mode='json'), apply)

    @app.get('/api/workspace/tasks')
    def tasks(scope: QueryScope = Depends(get_scope), svc=Depends(service)):
        from .records import task_collection
        return task_collection(svc, scope, page_size=100)

    @app.get('/api/process-context/{line_id}')
    def process_context(line_id: str, svc=Depends(service)):
        line = svc.get(m.Master, line_id)
        if line.kind != 'line':
            fail(422, 'line_required', '请选择实际产线')
        route = svc.get(m.Route, line.data['route_id']) if line.data.get('route_id') else None
        sources = svc.db.scalars(svc.scoped(m.Adapter).where(m.Adapter.scope_line_id == line.id)).all()
        steps = []
        for step in effective_steps(route.steps) if route else []:
            from .templates import candidate_points
            linked = [s for s in sources if s.source_registry.get('inventory',{}).get('step_id') == step['id']]
            steps.append({**step, 'sources': [{'id': s.id, 'name': s.name, 'health': s.health,
                         'mapping_version': s.config.get('mapping_version','legacy-v1'),
                         'points': s.source_registry.get('inventory',{}).get('points',[])} for s in linked],
                         'collection_state': 'configured' if linked else 'uncollected',
                         'candidate_points': candidate_points(line.data.get('process',''), step['name'])})
        return {'line': serialize(line), 'route': serialize(route) if route else None, 'steps': steps,
                'as_of': current_time().isoformat(), 'field_acceptance': 'not_performed',
                'limitations': ['已配置/通信正常不等于工艺正常。空气温度不是瓶体温度，设定不是实测。',
                                '点位不存在时显示未采集；参数允许范围只能来自批准SOP。']}
