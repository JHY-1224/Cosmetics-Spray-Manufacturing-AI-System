from datetime import UTC, datetime

from quality import models as m
from quality.db import Database
from .conftest import post
from .test_workflow import case, file, plan


def test_route_optional_step_publish_bind_and_rework_history(core):
    _, _, clients, lines = core
    engineer, approver = clients["engineer"], clients["approver"]
    ref = next(r for r in engineer.get("/api/routes").json() if r["process"] == "plastic_spray")
    optional = next(s for s in ref["steps"] if s["optional"])
    assert optional["id"] not in {s["id"] for s in ref["effective_steps"]}
    steps = [{**s, "enabled": True} if s["id"] == optional["id"] else s for s in ref["steps"]]
    route = post(
        engineer,
        "/routes",
        {
            "name": "现场确认路线版本",
            "process": "plastic_spray",
            "steps": steps,
            "disposition": {
                **ref["disposition"],
                "approved": True,
                "return_step_id": steps[5]["id"],
                "max_cycles": 1,
            },
            "previous_id": ref["id"],
        },
    )
    post(engineer, f"/routes/{route['id']}/review", {"action": "submit", "reason": "现场确认参考顺序"})
    post(approver, f"/routes/{route['id']}/review", {"action": "publish", "reason": "工艺专家核对通过"})
    line = next(row for row in lines if row["data"]["process"] == "plastic_spray")
    post(engineer, f"/lines/{line['id']}/bind/{route['id']}", {})
    evidence = file(engineer)
    disposition = post(
        engineer,
        "/dispositions",
        {
            "line_id": line["id"],
            "object_id": "LOAD-rework",
            "route_id": route["id"],
            "action": "rework",
            "reason": "合成异常需按批准工序返工",
            "evidence_ids": [evidence],
        },
    )
    disposition = post(
        clients["quality"],
        f"/dispositions/{disposition['id']}/review",
        {"version": disposition["version"], "approved": True, "reason": "核验对象、工序和次数后批准"},
    )
    for sequence, step in enumerate([route["steps"][5], route["steps"][8], route["steps"][5]], start=1):
        stamp = datetime.now(UTC).isoformat()
        post(
            clients["inspector"],
            "/passages",
            {
                "object_id": "LOAD-rework",
                "granularity": "load",
                "route_id": route["id"],
                "step_id": step["id"],
                "sequence": sequence,
                "entered_at": stamp,
                "exited_at": stamp,
                "data": {
                    "cycle": 2 if sequence == 3 else 1,
                    **({"disposition_id": disposition["id"]} if sequence == 3 else {}),
                },
            },
        )
    trace = engineer.get("/api/trace/LOAD-rework").json()
    assert len(trace["passages"]) == 3 and trace["passages"][0]["step_id"] != trace["passages"][1]["step_id"]
    assert trace["passages"][0]["step_id"] == trace["passages"][2]["step_id"]
    latest = next(r for r in engineer.get("/api/routes").json() if r["id"] == route["id"])
    assert len(latest["effective_steps"]) == len(ref["effective_steps"]) + 1
    second = post(
        engineer,
        "/dispositions",
        {
            "line_id": line["id"],
            "object_id": "LOAD-rework",
            "route_id": route["id"],
            "action": "rework",
            "reason": "再次返工应达到次数上限",
            "evidence_ids": [evidence],
        },
    )
    post(
        clients["quality"],
        f"/dispositions/{second['id']}/review",
        {"version": second["version"], "approved": True, "reason": "验证上限阻断"},
        expected=409,
    )


def test_sop_bounds_approval_author_and_evidence_invalidation(core):
    _, _, clients, lines = core
    engineer, approver = clients["engineer"], clients["approver"]
    k = post(
        engineer,
        "/knowledge",
        {
            "category": "sop",
            "title": "测试专用测量参考规程",
            "content": "此合成规程只验证软件参数边界，不是生产工艺。",
            "process": lines[0]["data"]["process"],
            "source": {"provenance": "reference"},
            "rules": {"parameters": {"test_metric": {"min": 1, "max": 3, "unit": "test-unit"}}},
        },
    )
    post(engineer, f"/knowledge/{k['id']}/review", {"action": "submit", "reason": "合成测试条目提交"})
    post(approver, f"/knowledge/{k['id']}/review", {"action": "publish", "reason": "仅用于参考环境边界测试"})
    c = case(engineer, lines[0])
    r = plan(engineer, c)
    c = post(
        approver,
        f"/cases/{c['id']}/approve",
        {
            "version": r["case"]["version"],
            "plan_id": r["plan"]["id"],
            "approved": True,
            "reason": "授权人工检查",
        },
    )
    ev = file(engineer)
    c = post(
        engineer,
        f"/cases/{c['id']}/evidence",
        {"version": c["version"], "evidence_ids": [ev], "reason": "收到迟到证据重新审核"},
    )
    assert c["status"] == "needs_evidence"
    assert engineer.get("/api/cases/" + c["id"]).json()["plans"][0]["status"] == "superseded"


def test_hanger_load_and_initial_ng_preserved(core):
    _, _, clients, lines = core
    engineer = clients["engineer"]
    h = post(engineer, "/masters", {"kind": "hanger", "name": "重复使用挂具", "code": "H1"})
    b1 = post(engineer, "/masters", {"kind": "production_batch", "name": "批次甲", "code": "B1"})
    b2 = post(engineer, "/masters", {"kind": "production_batch", "name": "批次乙", "code": "B2"})
    loads = [
        post(
            engineer,
            "/masters",
            {
                "kind": "load",
                "name": "挂次" + b["code"],
                "code": "L" + b["code"],
                "data": {"hanger_id": h["id"], "production_batch_id": b["id"]},
            },
        )
        for b in (b1, b2)
    ]
    for i, (load, batch, result) in enumerate(
        [(loads[0], b1, "NG"), (loads[0], b1, "OK"), (loads[1], b2, "NG")]
    ):
        post(
            clients["inspector"],
            "/inspections",
            {
                "record_id": str(i),
                "line_id": lines[0]["id"],
                "object_id": load["id"],
                "granularity": "load",
                "association": "load_record",
                "occurred_at": datetime.now(UTC).isoformat(),
                "status": result,
                "defects": [],
                "model_version": "manual",
                "config_version": "1",
                "defective_count": 1 if result == "NG" else 0,
                "process": lines[0]["data"]["process"],
                "hanger_id": h["id"],
                "load_id": load["id"],
                "production_batch_id": batch["id"],
            },
        )
    records = engineer.get("/api/trace/" + loads[0]["id"]).json()["inspections"]
    assert [r["status"] for r in records] == ["NG", "OK"]
    assert loads[0]["id"] != loads[1]["id"]


def test_database_isolation_at_application_boundary(core):
    settings, _, clients, lines = core
    db = Database(settings)
    with db.session() as session:
        hidden = m.Case(
            tenant_id="foreign-customer",
            site_id=settings.site_id,
            title="秘密案件",
            line_id=lines[0]["id"],
            process="plastic_spray",
            author_id="foreign",
            context={},
        )
        session.add(hidden)
        session.flush()
        id = hidden.id
    assert clients["admin"].get("/api/cases/" + id).status_code == 404
    assert all(r["tenant_id"] == settings.tenant_id for r in clients["admin"].get("/api/cases").json())


def test_unknown_schema_missing_time_production_validation(core):
    from quality.config import Settings
    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError):
        Settings(_env_file=None, mode="production")
    clients = core[2]
    response = clients["engineer"].post(
        "/api/cases", json={"tenant_id": "forged"}, headers={"Idempotency-Key": "invalid-input-key"}
    )
    assert response.status_code == 422 and "forged" not in response.text
    assert any(e["loc"] == ["body", "tenant_id"] for e in response.json()["detail"]["fields"])
