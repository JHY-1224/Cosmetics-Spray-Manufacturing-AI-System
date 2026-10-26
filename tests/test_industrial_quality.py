"""Quality outcome and sampling integrity through authenticated real APIs."""

from datetime import timedelta
from quality.security import current_time
from .conftest import post


def original(clients, role="engineer"):
    r = clients[role].post(
        "/api/files",
        files={"file": ("method-proof.txt", b"Synthetic approved method evidence", "text/plain")},
        headers={"Idempotency-Key": "proof-" + str(__import__("uuid").uuid4())},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def publish(clients, line, file, spec, code="STD"):
    row = post(
        clients["engineer"],
        "/industrial/definitions",
        {
            "code": code,
            "name": "软件验证 " + code,
            "scope": {"line_id": line["id"], "process": line["data"]["process"]},
            "valid_until": (current_time() + timedelta(days=30)).isoformat(),
            "evidence_ids": [file],
            "spec": spec,
        },
    )
    assert (
        post(
            clients["engineer"],
            f"/industrial/governance/definition/{row['id']}",
            {"version": row["version"], "action": "submit", "reason": "提交实际软件测试依据"},
        )["status"]
        == "pending_review"
    )
    row = clients["engineer"].get("/api/industrial/definitions/" + row["id"]).json()
    denied = post(
        clients["engineer"],
        f"/industrial/governance/definition/{row['id']}",
        {"version": row["version"], "action": "publish", "reason": "不允许作者自批"},
        expected=403,
    )
    assert denied["detail"]["code"] == "forbidden"
    return post(
        clients["approver"],
        f"/industrial/governance/definition/{row['id']}",
        {"version": row["version"], "action": "publish", "reason": "另一审核人核对批准方法和用途"},
    )


def test_five_quality_outcomes_units_calibration_and_originals(core):
    settings, app, clients, lines = core
    line = lines[0]
    file = original(clients)
    method = publish(
        clients,
        line,
        file,
        {
            "kind": "method",
            "item": "film_thickness",
            "method_version": "qualified-fixture-v1",
            "result_type": "numeric",
            "unit": "um",
            "granularity": "sample",
        },
        "METHOD",
    )
    standard = publish(
        clients,
        line,
        file,
        {
            "kind": "quality_standard",
            "item": "film_thickness",
            "customer_reference": "software-test-only",
            "method_id": method["id"],
            "unit": "um",
            "minimum": "10",
            "maximum": "20",
        },
        "STANDARD",
    )
    now = current_time().isoformat()
    base = {
        "standard_id": standard["id"],
        "method_id": method["id"],
        "line_id": line["id"],
        "object_id": "sample-only",
        "granularity": "sample",
        "product_id": "product-synthetic",
        "process": line["data"]["process"],
        "step_id": "explicit-test-step",
        "source_kind": "manual",
        "value": "15",
        "original_value": "15 um instrument display",
        "unit": "um",
        "occurred_at": now,
        "available_at": now,
        "quality": "manual_verified",
        "association": "manual_confirmed",
        "evidence_ids": [file],
    }
    for patch, expected in [
        ({}, "pass"),
        ({"value": "25"}, "fail"),
        ({"value": None, "quality": "missing"}, "not_tested"),
        ({"unit": "mm"}, "invalid"),
        ({"value": "10", "uncertainty": "0.1"}, "review"),
        ({"value": "15", "uncertainty": "10"}, "review"),
        ({"value": "15.1234567891"}, "invalid"),
    ]:
        row = post(clients["quality"], "/industrial/quality-assessments", {**base, **patch})
        assert row["outcome"] == expected, row
    missing = post(
        clients["quality"], "/industrial/quality-assessments", {**base, "standard_id": "not-provided"}
    )
    assert missing["outcome"] == "review"
    bad_method = publish(
        clients,
        line,
        file,
        {
            "kind": "method",
            "item": "film_thickness",
            "method_version": "calibrated-fixture-v1",
            "result_type": "numeric",
            "unit": "um",
            "granularity": "sample",
            "calibration_required": True,
        },
        "METHOD-CAL",
    )
    bad_standard = publish(
        clients,
        line,
        file,
        {
            "kind": "quality_standard",
            "item": "film_thickness",
            "customer_reference": "test",
            "method_id": bad_method["id"],
            "unit": "um",
            "minimum": "10",
            "maximum": "20",
        },
        "STD-CAL",
    )
    row = post(
        clients["quality"],
        "/industrial/quality-assessments",
        {**base, "standard_id": bad_standard["id"], "method_id": bad_method["id"]},
    )
    assert row["outcome"] in {"review", "invalid"}
    assert row["snapshot"]["release"] == "not_evaluated"
    assert (
        clients["quality"]
        .get("/api/industrial/quality-assessments/" + missing["id"])
        .json()["original_value"]
        == "15 um instrument display"
    )


def test_quality_plan_does_not_claim_unmeasured_items_or_single_sample_batch(core):
    settings, app, clients, lines = core
    line = lines[0]
    file = original(clients)
    method = publish(
        clients,
        line,
        file,
        {
            "kind": "method",
            "item": "adhesion",
            "method_version": "adhesion-test",
            "result_type": "enumerated",
            "unit": "grade",
            "granularity": "sample",
        },
        "ADH-METHOD",
    )
    standard = publish(
        clients,
        line,
        file,
        {
            "kind": "quality_standard",
            "item": "adhesion",
            "customer_reference": "synthetic-only",
            "method_id": method["id"],
            "unit": "grade",
            "allowed_values": ["pass"],
        },
        "ADH-STD",
    )
    plan = publish(
        clients,
        line,
        file,
        {
            "kind": "quality_plan",
            "standard_ids": [standard["id"]],
            "granularity": "batch",
            "sampling": "sampled_lot",
            "required_sample_count": 2,
            "sampling_rule_reference": "批准2个不同样品，测试夹具",
        },
        "PLAN",
    )
    cohort = post(
        clients["quality"],
        "/industrial/production-cohorts",
        {
            "line_id": line["id"],
            "external_id": "batch-software",
            "granularity": "batch",
            "product_id": "product-synthetic",
            "expected_quantity": "100",
            "closed": True,
            "quantity_basis": "明确合成的100件群体，不是客户实际数据",
            "evidence_ids": [file],
        },
    )
    ids = []
    for number in range(2):
        now = current_time().isoformat()
        r = post(
            clients["quality"],
            "/industrial/quality-assessments",
            {
                "standard_id": standard["id"],
                "method_id": method["id"],
                "plan_id": plan["id"],
                "cohort_id": cohort["id"],
                "line_id": line["id"],
                "object_id": "sample-" + str(number),
                "granularity": "sample",
                "product_id": "product-synthetic",
                "process": line["data"]["process"],
                "step_id": "test-step",
                "source_kind": "manual",
                "value": "pass",
                "unit": "grade",
                "occurred_at": now,
                "available_at": now,
                "quality": "manual_verified",
                "association": "manual_confirmed",
                "evidence_ids": [file],
            },
        )
        ids.append(r["id"])
    payload = {
        "plan_id": plan["id"],
        "line_id": line["id"],
        "object_id": "batch-software",
        "cohort_id": cohort["id"],
        "assessment_ids": ids[:1],
    }
    partial = post(clients["quality"], "/industrial/quality-plans/assess", payload)
    assert partial["outcome"] == "not_tested"
    done = post(clients["quality"], "/industrial/quality-plans/assess", {**payload, "assessment_ids": ids})
    assert done["outcome"] == "pass"
    assert done["snapshot"]["single_piece_all_tested"] is False
    changed = post(
        clients["quality"],
        "/industrial/quality-assessments",
        {
            **r["snapshot"]["input"],
            "value": "fail",
            "supersedes_id": ids[0],
            "object_id": "sample-0",
            "correction_reason": "实际核对后追加更正；保留原判定",
        },
    )
    assert changed["outcome"] == "fail"
    post(
        clients["quality"],
        "/industrial/quality-plans/assess",
        {**payload, "assessment_ids": ids},
        expected=409,
    )


def test_partial_projects_on_different_samples_cannot_be_combined_into_a_complete_plan(core):
    _, _, clients, lines = core
    line, file = lines[0], original(clients)
    method = publish(clients, line, file, {
        'kind': 'method', 'item': 'film', 'method_version': 'fixture-method',
        'result_type': 'numeric', 'unit': 'um', 'granularity': 'sample',
    }, 'COMPLETE-METHOD')
    standards = [publish(clients, line, file, {
        'kind': 'quality_standard', 'item': 'film', 'customer_reference': 'two-required-items-fixture',
        'method_id': method['id'], 'unit': 'um', 'minimum': '10', 'maximum': '20',
    }, 'COMPLETE-STD-' + str(i)) for i in range(2)]
    plan = publish(clients, line, file, {
        'kind': 'quality_plan', 'standard_ids': [r['id'] for r in standards], 'granularity': 'batch',
        'sampling': 'sampled_lot', 'required_sample_count': 1,
        'sampling_rule_reference': '每个所选样本完整覆盖两个必需标准',
    }, 'COMPLETE-PLAN')
    cohort = post(clients['quality'], '/industrial/production-cohorts', {
        'line_id': line['id'], 'external_id': 'complete-cohort', 'granularity': 'batch',
        'product_id': 'product-test', 'expected_quantity': '100', 'closed': True,
        'quantity_basis': '完整对象来源为软件合成批次夹具', 'evidence_ids': [file],
    })
    def assess(object_id, standard):
        now = current_time().isoformat()
        return post(clients['quality'], '/industrial/quality-assessments', {
            'standard_id': standard['id'], 'method_id': method['id'], 'plan_id': plan['id'],
            'cohort_id': cohort['id'], 'line_id': line['id'], 'object_id': object_id,
            'granularity': 'sample', 'product_id': 'product-test', 'process': line['data']['process'],
            'step_id': 'fixture-step', 'source_kind': 'manual', 'value': '15', 'unit': 'um',
            'occurred_at': now, 'available_at': now, 'quality': 'manual_verified',
            'association': 'manual_confirmed', 'evidence_ids': [file],
        })['id']
    ids = [assess('sample-A', standards[0]), assess('sample-B', standards[1])]
    payload = {'plan_id': plan['id'], 'line_id': line['id'], 'object_id': cohort['external_id'],
               'cohort_id': cohort['id'], 'assessment_ids': ids}
    partial = post(clients['quality'], '/industrial/quality-plans/assess', payload)
    assert partial['outcome'] == 'not_tested'
    assert partial['snapshot']['complete_object_count'] == 0
    assert partial['snapshot']['missing_standards_by_object'] == {
        'sample-A': [standards[1]['id']], 'sample-B': [standards[0]['id']],
    }
    ids += [assess('sample-A', standards[1]), assess('sample-B', standards[0])]
    complete = post(clients['quality'], '/industrial/quality-plans/assess', {**payload, 'assessment_ids': ids})
    assert complete['outcome'] == 'pass' and complete['snapshot']['complete_object_count'] == 2
    assert complete['snapshot']['single_piece_all_tested'] is False
    assert clients['quality'].get('/api/industrial/quality-assessments/' + partial['id']).json()['outcome'] == 'not_tested'


def test_full_inspection_cannot_adopt_sampling_failure_allowance():
    from pydantic import ValidationError
    from quality.enterprise_schemas import QualityPlanSpec
    import pytest

    with pytest.raises(ValidationError, match='全检计划'):
        QualityPlanSpec(standard_ids=['test-standard'], granularity='batch', sampling='all_objects',
                        required_sample_count=100, maximum_failed_samples=4,
                        sampling_rule_reference='合成验证：全检不能掩盖4个NG')
