from fastapi.testclient import TestClient

from .conftest import PASSWORD, post
from .test_workflow import case, file


def test_line_account_cannot_read_other_cases_files_or_runs(core):
    _, app, clients, lines = core
    admin = clients["admin"]
    c1 = case(clients["engineer"], lines[0])
    c2 = case(clients["engineer"], lines[1])
    ev = file(clients["engineer"])
    post(
        clients["engineer"],
        f"/cases/{c2['id']}/evidence",
        {"version": c2["version"], "evidence_ids": [ev], "reason": "绑定第二产线现场证据"},
    )
    post(
        admin,
        "/users",
        {
            "username": "line-reader",
            "password": PASSWORD,
            "role": "viewer",
            "allowed_sites": ["factory-a"],
            "allowed_lines": [lines[0]["id"]],
        },
    )
    viewer = TestClient(app)
    post(viewer, "/auth/login", {"username": "line-reader", "password": PASSWORD})
    assert viewer.get("/api/cases/" + c1["id"]).status_code == 200
    assert viewer.get("/api/cases/" + c2["id"]).status_code == 404
    assert viewer.get("/api/files/" + ev).status_code == 404
    assert len(viewer.get("/api/masters?kind=line").json()) == 1
    assert all(r["case_id"] == c1["id"] for r in viewer.get("/api/operations").json()["runs"])
    export = viewer.get("/api/export/cases")
    assert export.status_code == 200
    assert c1["id"] in export.text and c2["id"] not in export.text
    assert "integration" in export.text and "attachment" in export.headers["content-disposition"]


def test_timezone_always_explicit_in_serialized_records(core):
    _, _, clients, lines = core
    c = case(clients["engineer"], lines[0])
    loaded = clients["engineer"].get("/api/cases/" + c["id"]).json()
    assert loaded["created_at"].endswith("+00:00")


def test_line_grants_cannot_create_or_review_factory_shared_knowledge(core):
    from fastapi.testclient import TestClient
    from .conftest import PASSWORD

    _, app, clients, lines = core
    scoped = []
    for username, role in [("scoped-expert", "engineer"), ("scoped-reviewer", "approver")]:
        post(
            clients["admin"],
            "/users",
            {
                "username": username,
                "password": PASSWORD,
                "role": role,
                "allowed_sites": [lines[0]["site_id"]],
                "allowed_lines": [lines[0]["id"]],
            },
        )
        client = TestClient(app)
        post(client, "/auth/login", {"username": username, "password": PASSWORD})
        scoped.append(client)
    body = {
        "title": "范围内的人工知识",
        "category": "case",
        "content": "软件合成内容，不能由产线权限扩大共享",
        "process": lines[0]["data"]["process"],
        "source": {"description": "隔离测试"},
    }
    post(scoped[0], "/knowledge", body, expected=403)
    post(scoped[0], "/knowledge", {**body, "scope": {"line_ids": [entry["id"] for entry in lines]}}, expected=403)
    row = post(scoped[0], "/knowledge", {**body, "scope": {"line_ids": [lines[0]["id"]]}})
    post(scoped[0], f"/knowledge/{row['id']}/review", {"action": "submit", "reason": "仅当前授权产线"})
    assert (
        post(
            scoped[1], f"/knowledge/{row['id']}/review", {"action": "publish", "reason": "独立审核授权范围"}
        )["status"]
        == "published"
    )
    shared = post(clients["engineer"], "/knowledge", body)
    post(
        clients["engineer"],
        f"/knowledge/{shared['id']}/review",
        {"action": "submit", "reason": "需要工厂范围审核"},
    )
    post(
        scoped[1],
        f"/knowledge/{shared['id']}/review",
        {"action": "publish", "reason": "产线权限不能扩展"},
        expected=403,
    )
