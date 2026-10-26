from .conftest import post
from .test_workflow import case, plan


def test_recipe_independent_review_binding_immutable_snapshot_and_rollback(core):
    _, _, clients, lines = core
    engineer, approver = clients["engineer"], clients["approver"]
    line = next(r for r in lines if r["data"]["process"] == "plastic_spray")
    sop = post(
        engineer,
        "/knowledge",
        {
            "category": "sop",
            "title": "软件测试配方批准窗口",
            "content": "仅用于测试的单位窗口，不是生产参数",
            "process": "plastic_spray",
            "source": {"provenance": "reference"},
            "rules": {"parameters": {"reference_metric": {"min": 1, "max": 3, "unit": "test-unit"}}},
        },
    )
    post(engineer, f"/knowledge/{sop['id']}/review", {"action": "submit", "reason": "提交软件测试窗口"})
    post(approver, f"/knowledge/{sop['id']}/review", {"action": "publish", "reason": "审核测试参数定义"})
    body = {
        "code": "TEST-RECIPE",
        "name": "合成配方版本",
        "line_id": line["id"],
        "process": "plastic_spray",
        "source": "合成软件验证参数",
        "parameters": {"reference_metric": {"value": 2, "unit": "test-unit", "sop_id": sop["id"]}},
    }
    post(
        engineer,
        "/masters",
        {"kind": "recipe", "code": "BYPASS", "name": "伪造已发布配方", "data": {"status": "published"}},
        expected=422,
    )
    first = post(engineer, "/recipes", body)
    post(engineer, f"/recipes/{first['id']}/review", {"action": "submit", "reason": "提交首个配方版本"})
    post(
        engineer,
        f"/recipes/{first['id']}/review",
        {"action": "publish", "reason": "作者自行审核应拒绝"},
        expected=403,
    )
    post(approver, f"/recipes/{first['id']}/review", {"action": "publish", "reason": "独立审核参数与单位"})
    post(engineer, f"/lines/{line['id']}/recipe/{first['id']}", {})
    c = case(engineer, line)
    assert c["context"]["recipe_id"] == first["id"]
    p = plan(engineer, c)
    second = post(
        engineer,
        "/recipes",
        {
            **body,
            "previous_id": first["id"],
            "parameters": {"reference_metric": {"value": 2.5, "unit": "test-unit", "sop_id": sop["id"]}},
        },
    )
    post(engineer, f"/recipes/{second['id']}/review", {"action": "submit", "reason": "提交第二配方版本"})
    post(approver, f"/recipes/{second['id']}/review", {"action": "publish", "reason": "独立审核第二版本"})
    post(engineer, f"/lines/{line['id']}/recipe/{second['id']}", {})
    post(
        approver,
        f"/cases/{c['id']}/approve",
        {
            "version": p["case"]["version"],
            "plan_id": p["plan"]["id"],
            "approved": True,
            "reason": "旧配方审批应失效",
        },
        expected=409,
    )
    archived = engineer.get("/api/cases/" + c["id"]).json()["context"]["recipe_snapshot"]
    assert archived["data"]["parameters"]["reference_metric"]["value"] == 2
    post(
        approver, f"/recipes/{second['id']}/review", {"action": "rollback", "reason": "受控回退先前批准版本"}
    )
    saved = next(r for r in engineer.get("/api/masters?kind=line").json() if r["id"] == line["id"])
    assert saved["data"]["recipe_id"] == first["id"]
    bad = post(
        engineer,
        "/recipes",
        {
            **body,
            "code": "OUTSIDE",
            "parameters": {"reference_metric": {"value": 9, "unit": "test-unit", "sop_id": sop["id"]}},
        },
    )
    post(
        engineer,
        f"/recipes/{bad['id']}/review",
        {"action": "submit", "reason": "越界参数不能进入审核"},
        expected=422,
    )
