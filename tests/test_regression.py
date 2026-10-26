from .conftest import post


def test_expert_rule_historical_regression_is_scoped_and_does_not_mutate_case(core):
    _, _, clients, lines = core
    engineer = clients["engineer"]
    line = next(r for r in lines if r['data']['process'] == 'plastic_spray')
    case = post(
        engineer,
        "/cases",
        {
            "title": "规则回归核验案件",
            "line_id": line["id"],
            "process": "plastic_spray",
            "defect": "颗粒",
        },
    )
    case = post(engineer, f"/cases/{case['id']}/diagnose", {"version": case["version"]})
    rule = post(
        engineer,
        "/knowledge",
        {
            "category": "root_cause",
            "title": "颗粒环境核验规则",
            "content": "参考规则，必须现场验证",
            "process": "plastic_spray",
            "source": {"provenance": "reference"},
            "rules": {
                "dimension": "environment",
                "defects": ["颗粒"],
                "cause": "洁净度候选关联",
                "required_metrics": ["particle_count"],
                "next_checks": ["现场测量并提供反证"],
                "relative_score": 20,
            },
        },
    )
    data = {"case_ids": [case["id"]]}
    report = post(engineer, f"/knowledge/{rule['id']}/evaluate", data, key="regression-report-001")
    assert report["sample_count"] == 1 and report["changed_count"] == 1
    candidate = report["results"][0]["after"][0]
    assert candidate["status"] == "unverified" and candidate["missing_evidence"] == ["particle_count"]
    assert post(engineer, f"/knowledge/{rule['id']}/evaluate", data, key="regression-report-001") == report
    current = engineer.get("/api/cases/" + case["id"]).json()
    assert current["version"] == case["version"] and current["diagnosis"] == case["diagnosis"]
    post(clients["viewer"], f"/knowledge/{rule['id']}/evaluate", data, expected=403)
