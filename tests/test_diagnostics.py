from datetime import timedelta
from types import SimpleNamespace as NS
import pytest
from pydantic import ValidationError
from quality.agent import RuleInput, score_candidates
from quality.diagnostics import evaluate_condition, evidence_features
from quality.security import current_time
from quality import model_journal


def measurement(name="m1", value=12, **kwargs):
    return NS(
        id=name,
        metric="temperature",
        equipment_id="eq1",
        value=value,
        unit="test-unit",
        quality="Good",
        value_type="measured",
        occurred_at=current_time(),
        **kwargs,
    )


def test_five_dimensions_conditions_counter_stale_setpoint_and_ambiguity():
    anchor = current_time()
    metrics = [measurement()]
    condition = {"metric": "temperature", "operator": "gt", "value": 10, "unit": "test-unit"}
    rules = [
        NS(
            id="k" + str(n),
            title=d,
            category="root_cause",
            rules={
                "dimension": d,
                "defects": ["颗粒"],
                "relative_score": 60,
                "conditions": [condition],
                "next_checks": ["人工验证"],
            },
        )
        for n, d in enumerate(["equipment", "material", "process", "environment", "management"])
    ]
    # Choose an anchor after sampling: future samples never count as historical evidence.
    anchor = current_time()
    candidates = score_candidates(rules, "颗粒", metrics, "plastic_spray", anchor)
    assert len(candidates) == 5 and all(c["status"] == "evidence_supported" for c in candidates)
    rules[0].rules["counter_conditions"] = [condition]
    excluded = score_candidates(rules[:1], "颗粒", metrics, "plastic_spray", anchor)[0]
    assert excluded["status"] == "excluded" and excluded["counter_refs"] == ["m1"]
    metrics[0].value_type = "setpoint"
    assert evaluate_condition(condition, metrics, anchor)["state"] == "value_type_mismatch"
    metrics[0].value_type = "measured"
    metrics[0].occurred_at = anchor - timedelta(hours=2)
    assert evaluate_condition(condition, metrics, anchor)["state"] == "stale"
    metrics[0].occurred_at = anchor
    m2 = measurement("m2")
    m2.equipment_id = "eq2"
    m2.occurred_at = anchor
    assert evaluate_condition(condition, [metrics[0], m2], anchor)["state"] == "ambiguous_equipment"
    metrics[0].unit = "wrong"
    assert evaluate_condition(condition, metrics[:1], anchor)["state"] == "unit_mismatch"
    with pytest.raises(ValidationError):
        RuleInput.model_validate({"conditions": [{**condition, "operator": "outside"}]})


def test_features_use_approved_windows_actual_units_and_immutable_observations():
    anchor = current_time()
    rows = [
        {
            "id": str(n),
            "occurred_at": (anchor + timedelta(minutes=n)).isoformat(),
            "status": "NG",
            "object_id": "load" + str(n),
            "granularity": "load",
            "result": {
                "station_id": "station-A",
                "production_batch_id": "batch-A",
                "inspected_count": 10,
                "defective_count": 1,
            },
        }
        for n in range(2)
    ]
    measurements = [measurement()]
    sop = NS(
        id="sop",
        category="sop",
        rules={"parameters": {"temperature": {"min": 1, "max": 5, "unit": "test-unit"}}},
    )
    features = evidence_features(rows, measurements, [], [sop])
    assert len(features["time_clusters"]) == 1
    assert features["group_concentration"][0]["inspected_count"] == 20
    assert features["parameters"][0]["deviation_refs"] == ["m1"]
    conflicting = NS(
        id="conflict",
        category="sop",
        rules={"parameters": {"temperature": {"min": 20, "max": 30, "unit": "test-unit"}}},
    )
    assert evidence_features(rows, measurements, [], [sop, conflicting])["parameters"][0]["window_conflict"]


def test_durable_model_reservation_survives_business_rollback_and_unknown_result(core):
    settings, _, _, _ = core
    first = model_journal.reserve(settings, "case", "run", "input", 30000, 1, [])
    assert first["new_reservation"]
    unknown = model_journal.reserve(settings, "case", "run", "input", 30000, 1, [])
    assert unknown["status"] == "reserved" and not unknown.get("new_reservation")
    assert (
        model_journal.reserve(settings, "case", "run2", "input2", 30000, 1, [])["status"]
        == "budget_exhausted"
    )
    model_journal.finish(settings, "case", "run", {"model_status": "validated", "model_record": {}})
    assert (
        model_journal.reserve(settings, "case", "run", "input", 30000, 1, [])["outcome"]["model_status"]
        == "validated"
    )
    with pytest.raises(ValueError):
        model_journal.reserve(settings, "case", "run", "different", 1, 0, [])


def test_evidence_task_requires_linked_evidence_and_rejects_stale_version(core):
    from .conftest import post
    from .test_workflow import case, file

    _, _, clients, lines = core
    engineer = clients["engineer"]
    current = case(engineer, lines[0])
    task = current["diagnosis"]["evidence_tasks"][0]
    post(
        engineer,
        f"/cases/{current['id']}/evidence-tasks/{task['id']}",
        {"version": current["version"], "action": "complete", "reason": "没有证据不能完成"},
        expected=422,
    )
    evidence = file(engineer)
    current = post(
        engineer,
        f"/cases/{current['id']}/evidence",
        {
            "version": current["version"],
            "evidence_ids": [evidence],
            "inspection_ids": [],
            "reason": "实际测量记录补证",
        },
    )
    body = {
        "version": current["version"],
        "action": "complete",
        "reason": "核对实际测量记录",
        "evidence_ids": [evidence],
    }
    completed = post(
        engineer, f"/cases/{current['id']}/evidence-tasks/{task['id']}", body, key="task-complete-test-01"
    )
    assert completed["diagnosis"]["evidence_tasks"][0]["status"] == "completed"
    assert (
        post(
            engineer, f"/cases/{current['id']}/evidence-tasks/{task['id']}", body, key="task-complete-test-01"
        )
        == completed
    )
    post(engineer, f"/cases/{current['id']}/evidence-tasks/{task['id']}", body, expected=409)
