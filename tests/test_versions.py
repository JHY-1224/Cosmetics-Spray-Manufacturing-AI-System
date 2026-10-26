from .conftest import post


def test_knowledge_publication_conflict_and_validated_rollback(core):
    _, _, clients, lines = core
    engineer, reviewer = clients["engineer"], clients["approver"]
    body = {
        "category": "sop",
        "title": "人工检查版本一",
        "content": "第一版已确认的人工核验说明",
        "process": lines[0]["data"]["process"],
        "source": {"description": "合成版本治理测试"},
    }
    v1 = post(engineer, "/knowledge", body)
    post(engineer, f"/knowledge/{v1['id']}/review", {"action": "submit", "reason": "提交版本一审核"})
    post(reviewer, f"/knowledge/{v1['id']}/review", {"action": "publish", "reason": "确认版本一"})
    v2 = post(
        engineer,
        "/knowledge",
        {**body, "content": "第二版经过独立专家审核的核验说明", "previous_id": v1["id"]},
    )
    post(engineer, f"/knowledge/{v2['id']}/review", {"action": "submit", "reason": "提交版本二审核"})
    post(reviewer, f"/knowledge/{v2['id']}/review", {"action": "publish", "reason": "确认版本二"})
    rows = {r["id"]: r for r in engineer.get("/api/knowledge").json()}
    assert rows[v1["id"]]["status"] == "retired" and rows[v2["id"]]["status"] == "published"
    diff = engineer.get(f"/api/diff/knowledge/{v1['id']}/{v2['id']}").json()
    assert any(r["field"] == "content" for r in diff["changes"])
    post(
        reviewer,
        f"/knowledge/{v2['id']}/review",
        {"action": "rollback", "reason": "确认回退经审核的第一版本"},
    )
    rows = {r["id"]: r for r in engineer.get("/api/knowledge").json()}
    assert rows[v1["id"]]["status"] == "published" and rows[v2["id"]]["status"] == "rolled_back"
    assert rows[v1["id"]]["content"] == body["content"]
