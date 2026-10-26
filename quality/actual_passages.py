"""Retain actual abnormal/late facts before normalization; never invent a repaired production sequence."""

from types import SimpleNamespace
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from . import models as m, schemas as old
from .enterprise_schemas import ActualPassageInput
from .adapters import AdapterConfig
from .enterprise import evidence, line_scope, lock
from .ingestion import stage_raw
from .security import aware, current_time, digest, fail
from .service import serialize
from .templates import effective_steps


def normalize(svc, raw, payload):
    data = ActualPassageInput.model_validate(payload["record"])
    line = line_scope(svc, data.line_id, "inspect")
    lock(svc, ["actual-passage", data.object_id])
    if raw.status == "conflict":
        return None
    if raw.normalized_ids:
        return serialize(svc.get(m.Passage, raw.normalized_ids[0]))
    route = svc.db.scalar(svc.scoped(m.Route).where(m.Route.id == data.route_id))
    flags = []
    for id in data.evidence_ids:
        proof = svc.get(m.Evidence, id)
        if proof.status != "available" or proof.scope_line_id not in {
            data.line_id,
            "unassigned:" + payload["actor_id"],
        }:
            flags.append("original_evidence_requires_review")
        elif proof.scope_line_id.startswith("unassigned:"):
            proof.scope_line_id = data.line_id
    if not route or route.status != "published" or not route.reviewer_id:
        flags.append("unapproved_or_unmapped_route")
    if line.data.get("route_id") != data.route_id:
        flags.append("actual_route_differs_from_current_binding")
    if route and route.process != line.data.get("process"):
        flags.append("route_process_mismatch")
    ids = [s["id"] for s in effective_steps(route.steps)] if route else []
    if data.step_id not in ids:
        flags.append("step_not_in_approved_effective_route")
    if data.exited_at < data.entered_at or data.exited_at > current_time() + timedelta(minutes=2):
        flags.append("invalid_reported_time")
    prior = svc.db.scalar(
        svc.scoped(m.Passage)
        .where(m.Passage.object_id == data.object_id)
        .order_by(m.Passage.sequence.desc())
        .limit(1)
    )
    if prior and data.entered_at < aware(prior.exited_at):
        flags.append("late_or_overlapping_fact")
    if data.reported_sequence != (prior.data.get("reported_sequence", prior.sequence) + 1 if prior else 1):
        flags.append("reported_sequence_gap_or_repeat")
    if prior and prior.step_id in ids and data.step_id in ids:
        expected = ids[ids.index(prior.step_id) + 1 : ids.index(prior.step_id) + 2]
        allowed = next(s for s in route.steps if s["id"] == prior.step_id).get(
            "allowed_next_step_ids", expected
        )
        if data.step_id not in allowed and data.cycle == prior.data.get("cycle", 1):
            flags.append("actual_step_out_of_approved_order")
    from .dispositions import validate_reentry

    try:
        validate_reentry(
            svc,
            old.PassageInput(
                line_id=data.line_id,
                object_id=data.object_id,
                granularity=data.granularity,
                route_id=data.route_id,
                step_id=data.step_id,
                sequence=data.reported_sequence,
                entered_at=data.entered_at,
                exited_at=data.exited_at,
                data={"cycle": data.cycle, "disposition_id": data.disposition_id},
            ),
        )
    except HTTPException as exc:
        flags.append(exc.detail.get("code", "reentry_requires_review"))
    ordinal = (
        svc.db.scalar(
            svc.scoped(m.Passage)
            .where(m.Passage.object_id == data.object_id)
            .with_only_columns(func.max(m.Passage.sequence))
        )
        or 0
    ) + 1
    row = svc.add(
        m.Passage,
        object_id=data.object_id,
        granularity=data.granularity,
        route_id=data.route_id,
        step_id=data.step_id,
        sequence=ordinal,
        entered_at=data.entered_at,
        exited_at=data.exited_at,
        scope_line_id=data.line_id,
        data={
            "contract": "actual-passage-v1",
            "raw_record_id": raw.id,
            "source_id": data.source_id,
            "source_session_id": data.source_session_id,
            "source_record_id": data.source_record_id,
            "reported_sequence": data.reported_sequence,
            "platform_sequence_semantics": "arrival_order_only",
            "reported_cycle": data.cycle,
            **({"cycle": data.cycle} if not flags else {}),
            "disposition_id": data.disposition_id,
            "product_id": data.product_id,
            "production_batch_id": data.production_batch_id,
            "conformance": "review" if flags else "consistent",
            "annotations": flags,
            "evidence_ids": data.evidence_ids,
            "original_actor": payload["actor_id"],
            "recorded_at": current_time().isoformat(),
            "notes": data.notes,
            "physical_action": "reported_actual_fact_not_platform_control",
        },
    )
    raw.normalized_ids = [row.id]
    raw.source_time = data.entered_at
    raw.status = "processed"
    raw.errors = [{"code": v} for v in flags]
    svc.audit(
        "passage.actual_fact_retained",
        row.id,
        {"raw_record_id": raw.id, "annotations": flags, "reported_by": payload["actor_id"]},
    )
    return serialize(row)


def capture(svc, data):
    line_scope(svc, data.line_id, "inspect")
    if not data.actual_confirmation:
        fail(422, "actual_passage_confirmation", "只登记已实际发生且有原件的经过；建议不能充当事实")
    evidence(svc, data.evidence_ids, data.line_id)
    lock(svc, ["actual-passage", data.object_id])
    raw_payload = {
        "contract": "actual-passage-v1",
        "record_id": data.source_record_id,
        "source_session_id": data.source_session_id,
        "actor_id": svc.actor.id,
        "record": data.model_dump(mode="json"),
    }
    adapter = SimpleNamespace(
        id="actual:" + digest([data.line_id, data.source_id])[:40], protocol="actual_passage"
    )
    config = AdapterConfig(
        endpoint="http://localhost", mapping_version="actual-passage-v1", line_id=data.line_id
    )
    raw = stage_raw(svc, adapter, raw_payload, config)
    result = None
    try:
        with svc.db.begin_nested():
            result = normalize(svc, raw, raw_payload)
    except (HTTPException, IntegrityError) as exc:
        raw.status = "quarantined"
        raw.errors = [
            {
                "code": exc.detail.get("code", "normalization_failed")
                if isinstance(exc, HTTPException)
                else "normalization_identity_conflict"
            }
        ]
    return {
        "raw_record_id": raw.id,
        "raw_status": raw.status,
        "passage": result,
        "annotations": raw.errors,
        "original_retained": True,
        "authorization_granted": False,
    }


def recover_envelope(svc, envelope):
    payload = envelope["raw"]
    data = ActualPassageInput.model_validate(payload["record"])
    if payload.get("contract") != "actual-passage-v1" or not data.actual_confirmation:
        fail(422, "actual_raw_contract", "不是受控实际经过原始信封")
    source = "actual:" + digest([data.line_id, data.source_id])[:40]
    if (
        envelope["source_id"] != source
        or digest(__import__("json").dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode())
        != envelope["payload_hash"]
    ):
        fail(409, "actual_raw_integrity", "实际经过原始信封身份或哈希不一致")
    adapter = SimpleNamespace(id=source, protocol="actual_passage")
    config = AdapterConfig(
        endpoint="http://localhost", mapping_version="actual-passage-v1", line_id=data.line_id
    )
    raw = stage_raw(svc, adapter, payload, config)
    try:
        with svc.db.begin_nested():
            normalize(svc, raw, payload)
    except (HTTPException, IntegrityError) as exc:
        raw.status = "quarantined"
        raw.errors = [
            {
                "code": exc.detail.get("code", "normalization_failed")
                if isinstance(exc, HTTPException)
                else "normalization_identity_conflict"
            }
        ]
    return raw
