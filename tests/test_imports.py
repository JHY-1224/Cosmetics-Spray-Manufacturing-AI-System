import json
from datetime import UTC, datetime

from .conftest import post
from .test_workflow import file


def test_structured_import_schema_atomicity_and_replay(core):
    _, _, clients, lines = core
    client = clients["engineer"]
    payload = [{"kind": "material_batch", "code": "IMPORTED-1", "name": "导入物料批次", "data": {}}]
    response = client.post(
        "/api/import/structured/masters/preview",
        files={"file": ("masters.json", json.dumps(payload).encode())},
    )
    assert response.status_code == 200 and response.json()["commit_allowed"]
    first = post(client, "/import/structured/masters/commit", payload, key="import-deterministic-key")
    assert (
        post(client, "/import/structured/masters/commit", payload, key="import-deterministic-key")["records"][
            0
        ]["id"]
        == first["records"][0]["id"]
    )
    count = len(client.get("/api/masters").json())
    invalid = [
        {"kind": "material_batch", "code": "ROLLBACK-1", "name": "不应提交"},
        {"kind": "invalid", "code": "BAD", "name": "错误类型"},
    ]
    post(client, "/import/structured/masters/commit", invalid, expected=422)
    assert len(client.get("/api/masters").json()) == count
    evidence = file(client)
    lab = {
        "line_id": lines[0]["id"],
        "object_id": "SAMPLE-TEST",
        "metric": "adhesion",
        "method": "合成附着力试验",
        "value": "合成等级",
        "unit": "参考等级体系",
        "occurred_at": datetime.now(UTC).isoformat(),
        "evidence_ids": [evidence],
        "granularity": "sample",
    }
    result = post(client, "/import/structured/lab-results/commit", [lab])
    assert result["records"][0]["metric"] == "adhesion"
    post(clients["viewer"], "/import/structured/lab-results/commit", [lab], expected=403)
