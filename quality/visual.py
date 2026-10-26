"""Deterministic visual/manual normalization shared by HTTP and durable recovery."""

from datetime import timedelta
from . import models as m, schemas as s
from .security import aware, current_time, digest, fail
from .service import serialize
from .templates import effective_steps


def flag_corrected_evidence(svc, original_id, replacement_id):
    from sqlalchemy import cast, Text
    query = svc.scoped(m.Case).where(cast(m.Case.context, Text).contains('"'+original_id+'"', autoescape=True))
    for candidate in svc.db.scalars(query.order_by(m.Case.id)).all():
        case = svc.get(m.Case, candidate.id, True)
        if any(v.get('correction_id') == replacement_id for v in case.review_flags):
            continue
        case.review_flags = [*case.review_flags, {'original_id': original_id, 'correction_id': replacement_id,
                'context_hash': digest(case.context), 'flagged_at': current_time().isoformat(), 'resolved': False}]
        # Preserve frozen evidence and every executed/closed fact. Require an explicit review.
        before_execution = case.status in {'accepted','needs_evidence','diagnosed','awaiting_approval','approved','rejected'}
        for plan in svc.db.scalars(svc.scoped(m.Plan).where(m.Plan.case_id == case.id,
                                   m.Plan.status.in_(['awaiting_approval','approved']))):
            plan.status = 'superseded'
        svc.advance(case, 'needs_evidence' if before_execution else case.status, 'inspection.correction_flagged')


def ingest_inspection(svc, data, source_id, provenance):
    line = svc.get(m.Master, data.line_id)
    if line.kind != "line":
        fail(422, "line_invalid", "检测绑定产线不存在")
    if source_id != "manual":
        adapter = svc.get(m.Adapter, source_id)
        if adapter.config.get("line_id") != data.line_id:
            fail(403, "source_binding", "检测源未绑定该产线")
    svc.evidence(data.image_ids, data.line_id)
    if any(not svc.get(m.Evidence, id).media_type.startswith("image/") for id in data.image_ids):
        fail(422, "image_type", "检测图片引用必须指向有效图片文件")
    record_key = data.record_id if not data.source_session_id else "session:" + digest(
        [data.source_session_id, data.record_id]
    )
    previous = svc.db.scalar(
        svc.scoped(m.Inspection).where(
            m.Inspection.source_id == source_id, m.Inspection.record_id == record_key
        )
    )
    payload = data.model_dump(mode="json")
    if data.correction_of:
        original = svc.get(m.Inspection, data.correction_of)
        if len(data.correction_reason) < 3 or original.line_id != data.line_id:
            fail(422, "correction_reason", "更正须保留原记录并说明理由，产线必须一致")
    if previous:
        prior = s.InspectionInput.model_validate({
            key: value for key, value in previous.result.items() if key in s.InspectionInput.model_fields
        }).model_dump(mode="json")
        if prior != payload:
            fail(409, "source_record_conflict", "来源记录已存在且内容不同；请追加更正记录")
        return serialize(previous)
    if data.correction_of:
        original = svc.get(m.Inspection, data.correction_of, True)
        if source_id != "manual" and original.source_id != source_id:
            fail(403, "correction_source", "检测源不能更正其他来源记录")
        if svc.db.scalar(
            svc.scoped(m.Inspection).where(
                m.Inspection.result["correction_of"].as_string() == original.id
            )
        ):
            fail(409, "correction_branch", "该记录已有更正版本，请更正最新记录")
    for key, kind in [
        ("production_batch_id", "production_batch"),
        ("load_id", "load"),
        ("hanger_id", "hanger"),
        ("equipment_run_id", "equipment_run"),
        ("furnace_run_id", "equipment_run"),
    ]:
        if getattr(data, key):
            ref = svc.get(m.Master, getattr(data, key))
            if ref.kind != kind:
                fail(422, "trace_reference", f"{key}关联类型不匹配")
    if data.load_id:
        load = svc.get(m.Master, data.load_id)
        if (
            data.hanger_id
            and load.data.get("hanger_id") != data.hanger_id
            or data.production_batch_id
            and load.data.get("production_batch_id") != data.production_batch_id
        ):
            fail(422, "load_mismatch", "挂次与挂具/批次关联不一致")
    status = data.status
    if not data.object_id or data.association == "unresolved":
        status = "unassociated"
    elif not data.applicable:
        status = "out_of_scope"
    elif data.occurred_at < current_time() - timedelta(days=7):
        status = "stale"
    elif data.status in ("OK", "NG") and not data.image_ids and source_id != "manual":
        status = "missing_image"
    from .ontology import normalize_terms

    normalized = normalize_terms(svc, [d.category for d in data.defects], data.process, data.line_id)
    if (
        source_id != "manual"
        and status in ("OK", "NG")
        and any(d["status"] != "known" for d in normalized)
    ):
        status = "unknown_defect"
    payload["normalized_defects"] = normalized
    if data.passage_id:
        passage = svc.get(m.Passage, data.passage_id)
        route = svc.get(m.Route, passage.route_id)
        steps = effective_steps(route.steps)
        if (
            passage.scope_line_id != data.line_id
            or passage.object_id != data.object_id
            or passage.granularity != data.granularity
            or aware(passage.exited_at) > data.occurred_at
        ):
            fail(422, "inspection_passage", "检测结果与工序对象、产线、粒度或实际时间不一致")
        conforms = passage.data.get('conformance','consistent') == 'consistent'
        payload["terminal_gate"] = bool(
            conforms and steps and passage.step_id == steps[-1]["id"] and steps[-1].get("quality_gate")
        )
        # Physical production cycles start at 1; quality statistics use 0 for the first pass.
        payload["verified_cycle"] = max(0, passage.data.get("cycle", 1) - 1) if conforms else None
        payload["cycle_semantics"] = "production_one_based_v2" if conforms else "unverified_actual_fact"
    if data.occurred_at > current_time() + timedelta(minutes=5):
        fail(422, "future_event", "事件发生时间过度超前")
    row = svc.add(
        m.Inspection,
        source_id=source_id,
        record_id=record_key,
        line_id=data.line_id,
        object_id=data.object_id,
        granularity=data.granularity,
        association=data.association,
        occurred_at=data.occurred_at,
        status=status,
        provenance=provenance,
        result=payload,
    )
    svc.audit("inspection.ingested", row.id, {"status": status, "source": source_id})
    svc.event(
        "inspection.recorded",
        {"id": row.id, "line_id": data.line_id, "status": status, "fact": serialize(row)},
    )
    if data.correction_of:
        flag_corrected_evidence(svc, data.correction_of, row.id)
    from .anomalies import trigger

    triggered = trigger(svc, line, row)
    if triggered:
        existing = svc.db.scalar(svc.scoped(m.Case).where(m.Case.anomaly_key == triggered["key"]))
        if not existing:
            case_data = s.CaseInput(
                title=f"自动异常：{data.defects[0].category if data.defects else '未分类'}",
                line_id=data.line_id,
                process=data.process,
                inspection_ids=triggered["inspection_ids"],
                route_id=line.data.get("route_id", ""),
                defect=data.defects[0].category if data.defects else "未分类",
            )
            created = svc.create_case(case_data)
            case_row = svc.get(m.Case, created["id"])
            case_row.anomaly_key = triggered["key"]
            case_row.context = {**case_row.context, "anomaly_window": triggered}
            svc.event(
                "case.analysis.requested",
                {"case_id": created["id"], "version": created["version"], "line_id": data.line_id},
            )
        else:
            svc.audit("anomaly.merged", existing.id, {"inspection_id": row.id, "window": triggered})
    return serialize(row)

