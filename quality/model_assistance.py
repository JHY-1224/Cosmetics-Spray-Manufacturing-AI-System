"""Expose validated diagnostic provenance and reviewable actions, never execution authority."""

from fastapi import HTTPException
from pydantic import ValidationError

from . import models as m
from .schemas import ModelOutput
from .security import digest


def assistance(svc, case):
    result = {"status": case.diagnosis.get("model_status", "not_run"), "run_id": "",
              "actions": [], "summary": "", "draft_available": False}
    run_id = case.diagnosis.get("run_id")
    if not run_id:
        return result
    run = svc.get(m.Run, run_id)
    if run.case_id != case.id:
        return {**result, "status": "stale", "reason": "run_case_mismatch"}
    record = run.data.get("model_record", {})
    result.update(run_id=run.id, **{key: record.get(key) for key in (
        "model", "returned_model", "prompt_version", "external", "max_output_tokens", "http_attempted", "response_received",
        "http_status", "finish_reason", "duration_ms", "usage", "reserved_tokens", "reserved_cost",
        "estimated_cost", "pricing_configured", "allowed_data_categories", "validation", "error_type",
    )})
    if result["status"] != "validated" or record.get("validation") != "passed":
        return result
    if run.data.get("context_hash") != digest(case.context) or any(not flag.get("resolved") for flag in case.review_flags):
        return {**result, "status": "stale", "reason": "case_evidence_changed"}
    try:
        output = ModelOutput.model_validate(record.get("output"))
        refs = set(run.data.get("evidence_refs", []))
        if any(set(c.support_refs + c.counter_refs) - refs or c.status == "human_confirmed" for c in output.candidates):
            return {**result, "status": "stale", "reason": "untrusted_output"}
        svc.valid_actions(output.actions, case.process, case.context.get("product_id", ""), case.line_id, case.context)
    except (HTTPException, ValidationError):
        return {**result, "status": "stale", "reason": "actions_require_revalidation"}
    # Human ownership/identity is assigned in the plan editor, never by a language model.
    actions = [{**action.model_dump(), "action_id": "", "owner_role": "", "owner_id": ""}
               for action in output.actions]
    return {**result, "actions": actions, "summary": output.summary, "draft_available": bool(actions)}
