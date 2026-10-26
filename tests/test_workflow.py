from datetime import UTC, datetime

import pytest

from .conftest import post


def file(client):
    import uuid

    r = client.post(
        "/api/files",
        files={"file": ("evidence.txt", b"manual measurement evidence", "text/plain")},
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-CSRF-Token": client.cookies.get("quality_csrf", "")},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def case(client, line):
    c = post(
        client,
        "/cases",
        {
            "title": "表面颗粒异常",
            "line_id": line["id"],
            "process": line["data"]["process"],
            "defect": "颗粒",
        },
    )
    return post(client, f"/cases/{c['id']}/diagnose", {"version": c["version"]})


def plan(client, c, samples=10):
    return post(
        client,
        f"/cases/{c['id']}/plans",
        {
            "version": c["version"],
            "actions": [{"type": "inspect", "description": "人工核实前处理记录与环境清洁情况"}],
            "retest": {
                "method": "按已确认质量标准目视与仪器复检",
                "target_defects": ["颗粒"],
                "sample_unit": "bottle",
                "min_samples": samples,
                "max_defect_rate": 0,
                "stabilization_minutes": 0,
                "observation_minutes": 0,
            },
        },
    )


@pytest.mark.parametrize("process", ["plastic_spray", "glass_vacuum"])
def test_full_workflow_real_state_constraints(core, process):
    settings, app, clients, lines = core
    engineer, approver, executor, quality = [
        clients[r] for r in ("engineer", "approver", "executor", "quality")
    ]
    c = case(engineer, next(line for line in lines if line["data"]["process"] == process))
    assert c["diagnosis"]["model_status"] == "disabled"
    p = plan(engineer, c)
    c, p = p["case"], p["plan"]
    c = post(
        approver,
        f"/cases/{c['id']}/approve",
        {"version": c["version"], "plan_id": p["id"], "approved": True, "reason": "核查批准人工核验步骤"},
    )
    assert c["status"] == "approved"
    evidence = file(executor)
    stamp = datetime.now(UTC).isoformat()
    c = post(
        executor,
        f"/cases/{c['id']}/execute",
        {
            "version": c["version"],
            "plan_id": p["id"],
            "executed_at": stamp,
            "object_ids": ["LOAD-A"],
            "actual_actions": "已人工核验清洁记录并完成现场清理",
            "deviations": "无",
            "evidence_ids": [evidence],
            "success": True,
        },
    )
    assert c["status"] == "awaiting_retest"
    stamp = datetime.now(UTC).isoformat()
    body = {
        "version": c["version"],
        "plan_id": p["id"],
        "started_at": stamp,
        "finished_at": stamp,
        "sample_count": 1,
        "defective_count": 0,
        "sample_unit": "bottle",
        "evidence_ids": [evidence],
        "conditions_met": True,
        "other_constraints_met": True,
        "notes": "逐件检验记录可追溯",
    }
    c = post(quality, f"/cases/{c['id']}/retest", body)
    assert c["status"] == "inconclusive"
    post(
        quality,
        f"/cases/{c['id']}/close",
        {
            "version": c["version"],
            "root_cause_confirmation": "unverified",
            "quality_disposition": "pending",
            "reason": "不能因一张图片结案",
        },
        expected=409,
    )
    c = post(quality, f"/cases/{c['id']}/retest", {**body, "version": c["version"], "sample_count": 10})
    assert c["status"] == "retest_passed"
    c = post(
        quality,
        f"/cases/{c['id']}/close",
        {
            "version": c["version"],
            "root_cause_confirmation": "unverified",
            "quality_disposition": "await_release",
            "reason": "按复测计划验证达标，原因尚未单独确认",
        },
    )
    assert c["status"] == "closed" and c["outcome"]["released"] is False
    assert c["outcome"]["root_cause_confirmation"] == "unverified"
    detail = engineer.get("/api/cases/" + c["id"]).json()
    assert len(detail["receipts"]) == 1 and len(detail["retests"]) == 2
    assert len(detail["timeline"]) >= 7
    knowledge = engineer.get("/api/knowledge").json()
    assert knowledge[-1]["status"] == "draft"


def test_plan_revision_invalidates_approval(core):
    _, _, clients, lines = core
    c = case(clients["engineer"], lines[0])
    r = plan(clients["engineer"], c)
    c = post(
        clients["approver"],
        f"/cases/{c['id']}/approve",
        {
            "version": r["case"]["version"],
            "plan_id": r["plan"]["id"],
            "approved": True,
            "reason": "允许现场人工检查",
        },
    )
    new = plan(clients["engineer"], c)
    evidence = file(clients["executor"])
    post(
        clients["executor"],
        f"/cases/{c['id']}/execute",
        {
            "version": new["case"]["version"],
            "plan_id": r["plan"]["id"],
            "executed_at": datetime.now(UTC).isoformat(),
            "object_ids": ["LOAD-X"],
            "actual_actions": "人工处理完成",
            "deviations": "无",
            "evidence_ids": [evidence],
            "success": True,
        },
        expected=409,
    )


def test_idempotency_and_optimistic_version(core):
    _, _, clients, lines = core
    body = {
        "title": "幂等异常测试",
        "line_id": lines[0]["id"],
        "process": lines[0]["data"]["process"],
        "defect": "尘点",
    }
    a = post(clients["engineer"], "/cases", body, key="same-case-key")
    b = post(clients["engineer"], "/cases", body, key="same-case-key")
    assert a["id"] == b["id"]
    post(clients["engineer"], "/cases", {**body, "title": "改变内容"}, key="same-case-key", expected=409)
    post(clients["engineer"], f"/cases/{a['id']}/diagnose", {"version": 1}, expected=409)


def test_read_only_permissions_and_forged_receipt(core):
    _, app, clients, lines = core
    c = case(clients["engineer"], lines[0])
    post(clients["viewer"], f"/cases/{c['id']}/diagnose", {"version": c["version"]}, expected=403)
    assert clients["viewer"].get("/metrics").status_code == 403
    paths = app.openapi()["paths"]
    assert not any(
        any(word in path for word in ("/plc/write", "/devices/start", "/devices/stop", "/eject"))
        for path in paths
    )
    post(
        clients["engineer"],
        f"/cases/{c['id']}/plans",
        {
            "version": c["version"],
            "actions": [
                {"type": "inspect", "description": "设置未经批准的温度", "parameters": {"temperature": 900}}
            ],
            "retest": {
                "method": "复检",
                "target_defects": ["颗粒"],
                "sample_unit": "bottle",
                "min_samples": 10,
                "max_defect_rate": 0,
                "stabilization_minutes": 0,
                "observation_minutes": 0,
            },
        },
        expected=422,
    )


def test_file_scope_and_magic(core):
    _, _, clients, _ = core
    import uuid

    r = clients["engineer"].post(
        "/api/files",
        files={"file": ("bad.png", b"not an image")},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 422
    id = file(clients["engineer"])
    assert clients["viewer"].get("/api/files/" + id).status_code == 200
    assert clients["viewer"].get("/api/files/../../etc/passwd").status_code in (404, 405)


def test_retry_budget_and_restart(core):
    settings, _, clients, lines = core
    from quality.app import create_app
    from fastapi.testclient import TestClient

    c = case(clients["engineer"], lines[0])
    for _ in range(settings.max_investigations - 1):
        c = post(clients["engineer"], f"/cases/{c['id']}/diagnose", {"version": c["version"]})
    post(clients["engineer"], f"/cases/{c['id']}/diagnose", {"version": c["version"]}, expected=409)
    restarted = TestClient(create_app(settings))
    restarted.cookies.update(clients["engineer"].cookies)
    loaded = restarted.get("/api/cases/" + c["id"]).json()
    assert loaded["version"] == c["version"] and len(loaded["runs"]) == settings.max_investigations


def test_expired_ai_investigation_keeps_human_workflow_available(core):
    from datetime import timedelta
    from quality import models as m
    from quality.db import Database
    from quality.security import current_time

    settings, _, clients, lines = core
    c = case(clients["engineer"], lines[0])
    with Database(settings).session() as db:
        db.get(m.Case, c["id"]).created_at = current_time() - timedelta(hours=settings.max_case_hours + 1)
    post(clients["engineer"], f"/cases/{c['id']}/diagnose", {"version": c["version"]}, expected=409)
    result = plan(clients["engineer"], c)
    assert result["case"]["status"] == "awaiting_approval"
