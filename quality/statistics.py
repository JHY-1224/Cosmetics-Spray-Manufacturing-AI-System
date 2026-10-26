from collections import defaultdict
from datetime import datetime
from . import models as m
from .security import aware, fail, current_time
from .service import serialize


def verified_cycle_facts(svc, facts):
    """Recalculate legacy stages only from exact authoritative passage evidence."""
    ids = {r['result'].get('passage_id') for r in facts if not r['result'].get('cycle_semantics')}
    passages = {p.id:p for p in svc.db.scalars(svc.scoped(m.Passage).where(m.Passage.id.in_(ids))) } if ids and svc.settings.node_role=='site' else {}
    output=[]
    for row in facts:
        result=row['result']
        if result.get('verified_cycle') is not None and not result.get('cycle_semantics'):
            passage=passages.get(result.get('passage_id'))
            cycle=passage.data.get('cycle') if passage else None
            confirmed=bool(passage and isinstance(cycle,int) and cycle>=1 and passage.scope_line_id==row['line_id']
                           and passage.object_id==row.get('object_id') and passage.granularity==row['granularity'])
            row={**row,'result':{**result,'verified_cycle':cycle-1 if confirmed else None,
                 'cycle_semantics':'recalculated_from_passage_v2' if confirmed else 'legacy_requires_review'}}
        output.append(row)
    return output


def normalized_observations(facts):
    """Remove superseded facts and combine explicitly identified views, never time-nearest guesses."""
    unique = {row['id']: row for row in facts}
    superseded = {r['result'].get('correction_of') for r in unique.values()}
    grouped = {}
    for row in sorted(unique.values(), key=lambda r: (r['occurred_at'], r['id'])):
        if row['id'] in superseded or row['status'] not in ('OK', 'NG'):
            continue
        value = row['result']
        event = value.get('inspection_event_id')
        key = (row.get('site_id', ''), row.get('line_id', ''), row['granularity'], row.get('source_id', ''),
               value.get('source_session_id', ''), event, row.get('object_id'),
               value.get('inspection_round', 1), value.get('station_id', '')) if (
                   event and row.get('object_id') and row.get('association') != 'unresolved'
               ) else (row['id'],)
        if key not in grouped:
            grouped[key] = {**row, 'result': dict(value), 'observation_refs': [row['id']]}
            continue
        target = grouped[key]
        target['observation_refs'].append(row['id'])
        prior = target['result']
        if any(prior.get(k) != value.get(k) for k in
               ('inspected_count', 'model_version', 'config_version', 'product_id', 'process')):
            target['statistical_conflict'] = True
        if row['status'] == 'NG':
            target['status'] = 'NG'
        prior['defective_count'] = max(prior['defective_count'], value['defective_count'])
        prior['image_ids'] = list(dict.fromkeys(prior.get('image_ids', []) + value.get('image_ids', [])))
        # Per-view boxes remain in original facts. Do not invent unique spatial defects.
        by_category = {d['category']: d for d in prior.get('defects', [])}
        for defect in value.get('defects', []):
            if defect['category'] not in by_category or defect['count'] > by_category[defect['category']]['count']:
                by_category[defect['category']] = defect
        prior['defects'] = list(by_category.values())
    return list(grouped.values())


def quality_statistics(svc, start=None, end=None, granularity="", site_id="", line_id="", product_id="", process=""):
    if granularity not in ("", "bottle", "load", "batch", "sample"):
        fail(422, "statistics_unit", "统计粒度非法")

    def stamp(value):
        if not value:
            return None
        if value.tzinfo is None:
            fail(422, "statistics_time", "统计窗口必须带时区")
        return aware(value)

    start, end = stamp(start), stamp(end)
    if start and end and start >= end:
        fail(422, "statistics_window", "统计开始必须早于结束")
    center = svc.settings.node_role == "center"
    if center:
        events = svc.db.scalars(
            svc.scoped(m.Inbox).where(m.Inbox.payload["kind"].as_string() == "inspection.recorded")
        ).all()
        facts = [
            {**e.payload["payload"]["fact"], "site_id": e.site_id}
            for e in events
            if e.payload.get("payload", {}).get("fact")
        ]
        missing = sum(not e.payload.get("payload", {}).get("fact") for e in events)
        cases = [{**r.data, 'site_id': r.site_id} for r in svc.db.scalars(svc.scoped(m.Snapshot)).all()]
    else:
        facts = [serialize(r) for r in svc.db.scalars(svc.scoped(m.Inspection)).all()]
        cases = [serialize(r) for r in svc.db.scalars(svc.scoped(m.Case)).all()]
        missing = 0
    observations = normalized_observations(verified_cycle_facts(svc, facts))
    rows = [
        r
        for r in observations
        if not r.get('statistical_conflict')
        and (not site_id or r['site_id'] == site_id)
        and (not line_id or r['line_id'] == line_id)
        and (not product_id or r['result'].get('product_id') == product_id)
        and (not process or r['result'].get('process') == process)
        and (svc.settings.mode != "production" or r["provenance"] not in ("demo", "reference"))
    ]
    rows = [
        r
        for r in rows
        if (not granularity or r["granularity"] == granularity)
        and (not start or aware(datetime.fromisoformat(r["occurred_at"])) >= start)
        and (not end or aware(datetime.fromisoformat(r["occurred_at"])) < end)
    ]

    def summarize(items):
        units = {r["granularity"] for r in items}
        mix = len(units) > 1
        total = sum(r["result"]["inspected_count"] for r in items)
        ng = sum(r["result"]["defective_count"] for r in items)
        # First pass requires a verified final-gate passage, never a fabricated bottle id.
        first = {}
        reinspection = {}
        for row in sorted(items, key=lambda r: r["occurred_at"]):
            result = row["result"]
            if not result.get("terminal_gate") or result.get("verified_cycle") is None:
                continue
            key = (
                row["site_id"],
                row["line_id"],
                row["granularity"],
                row.get("object_id"),
                result.get("production_batch_id", ""),
            )
            initial = (result.get('inspection_kind') != 'reinspection'
                       and result.get('inspection_round', 1) == 1 and result["verified_cycle"] == 0)
            if initial:
                first.setdefault(key, row)
            else:
                reinspection[key] = row
        denominator = sum(r["result"]["inspected_count"] for r in first.values())
        first_ng = sum(r["result"]["defective_count"] for r in first.values())
        ng_only = any(r['result'].get('collection_scope') == 'ng_only' for r in items)
        population_unknown = ng_only or not any(r['status'] == 'OK' for r in items) and not all(
            r['result'].get('collection_scope') == 'all_results' for r in items)
        return {
            "inspected_count": None if mix else total,
            "defective_count": None if mix else ng,
            "defect_count": None
            if mix
            else sum(sum(d["count"] for d in r["result"]["defects"]) for r in items),
            "defect_rate": ng / total if total and not mix else None,
            "granularity": next(iter(units))
            if len(units) == 1
            else "mixed"
            if mix
            else granularity or "unavailable",
            "first_pass_inspected": None if mix else denominator,
            "first_pass_defective": None if mix else first_ng,
            "first_pass_yield": 1 - first_ng / denominator if denominator and not mix and not population_unknown else None,
            "population_yield": None,
            "coverage": {'observed_records': len(items), 'total_production_count': None,
                         'denominator_confirmed': False,
                         'limitation': '仅NG/来源覆盖未知，不能推断总体良率' if population_unknown else
                                       '仅统计已收到的有效样本；生产总分母及漏报需现场核对'},
            "reinspection_objects": len(reinspection),
            "reinspection_ok_objects": sum(r["status"] == "OK" for r in reinspection.values()),
            "unclassified_stage_records": sum(not r["result"].get("terminal_gate") for r in items),
            "legacy_stage_requires_review": sum(r['result'].get('cycle_semantics') == 'legacy_requires_review' for r in items),
            "record_count": len(items),
            "inspection_observations": sum(len(r.get('observation_refs', [r['id']])) for r in items),
            "final_yield": None,
            "final_yield_limitation": "客户终态处置口径尚未确认；复检通过不自动等同正式放行",
            "affected_loads": len(
                {
                    (r["site_id"], r["result"]["load_id"])
                    for r in items
                    if r["result"].get("load_id") and r["result"]["defective_count"]
                }
            ),
            "affected_batches": len(
                {
                    (r["site_id"], r["result"]["production_batch_id"])
                    for r in items
                    if r["result"].get("production_batch_id") and r["result"]["defective_count"]
                }
            ),
        }

    groups = defaultdict(list)
    for row in rows:
        groups[(row["site_id"], row["line_id"], row["granularity"])].append(row)
    result = summarize(rows)
    if center and not facts:
        result.update(inspected_count=None, defective_count=None, defect_count=None)
    cases = [r for r in cases if (not site_id or r.get('site_id', svc.settings.site_id) == site_id)
             and (not line_id or r.get('line_id') == line_id)
             and (not product_id or r.get('context', {}).get('product_id') == product_id)
             and (not process or r.get('process') == process)]
    return {
        **result,
        'calculation_version': 'inspection-metrics-v3',
        "by_factory_line": [
            {"site_id": key[0], "line_id": key[1], **summarize(items)}
            for key, items in sorted(groups.items())
        ],
        "case_count": len(cases),
        "closed_count": sum(r.get("status") == "closed" for r in cases),
        "pending_count": sum(r.get("status") not in ("closed", "cancelled") for r in cases),
        "mode": svc.settings.mode,
        "incomplete_source_events": missing,
        "excluded_conflicting_observation_groups": sum(bool(r.get('statistical_conflict')) for r in observations),
        "deduplication": "source/session/event/object/station/inspection_round; explicit views merge; corrections append",
        "scope": {"site_id": site_id, "line_id": line_id, "product_id": product_id, "process": process},
        "case_window": "授权范围全部案件；不由检测发生时间窗口隐式筛除",
        "as_of": current_time().isoformat(),
        "definition": "按授权工厂/产线/粒度分组；不同单位不合并分母；来源记录去重、追加更正取最新。首次通过仅含可核验末道质量关口及循环次数的对象；复检保留首次NG。中心仅统计已确认接收事实，积压可能造成暂时不完整。",
        "time_window": {
            "start_inclusive": start.isoformat() if start else None,
            "end_exclusive": end.isoformat() if end else None,
            "field": "occurred_at",
        },
    }
