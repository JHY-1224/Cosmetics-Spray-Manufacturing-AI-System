import base64
import uuid
from datetime import UTC, datetime, timedelta, timezone

from .conftest import post

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZFcAAAAASUVORK5CYII="
)


def test_visual_source_upload_binding_idempotency_and_states(core):
    _, _, clients, lines = core
    admin = clients["admin"]
    term = post(
        clients["engineer"],
        "/knowledge",
        {
            "category": "defect",
            "title": "颗粒",
            "content": "参考视觉契约使用的缺陷分类定义",
            "process": lines[0]["data"]["process"],
            "source": {"provenance": "reference"},
            "rules": {"aliases": ["颗粒尘点"]},
        },
    )
    post(
        clients["engineer"],
        f"/knowledge/{term['id']}/review",
        {"action": "submit", "reason": "参考术语提交分类审核"},
    )
    post(
        clients["approver"],
        f"/knowledge/{term['id']}/review",
        {"action": "publish", "reason": "确认参考分类与别名"},
    )
    adapter = post(
        admin,
        "/adapters",
        {
            "name": "参考视觉系统",
            "protocol": "visual",
            "config": {"line_id": lines[0]["id"]},
            "enabled": True,
            "provenance": "reference",
        },
    )
    path = "/api/ingest/visual/" + adapter["id"]
    headers = {"Authorization": "Bearer " + adapter["token"], "Idempotency-Key": str(uuid.uuid4())}
    # A source token is not a user session and cannot browse business data.
    from fastapi.testclient import TestClient

    source = TestClient(core[1])
    assert source.get("/api/cases", headers=headers).status_code == 401
    uploaded = source.post(
        path + "/files", files={"file": ("reference.png", PNG, "image/png")}, headers=headers
    )
    assert uploaded.status_code == 200, uploaded.text
    body = {
        "record_id": "frame-1",
        "line_id": lines[0]["id"],
        "object_id": "LOAD-123",
        "granularity": "load",
        "association": "load_record",
        "occurred_at": datetime.now(UTC).isoformat(),
        "status": "NG",
        "defects": [
            {
                "category": "颗粒",
                "count": 2,
                "bbox": [0, 0, 1, 1],
                "coordinate_system": "pixels_xyxy",
                "image_width": 1,
                "image_height": 1,
            }
        ],
        "image_ids": [uploaded.json()["id"]],
        "model_version": "qualified-external-model-v1",
        "config_version": "reference-v1",
        "inspected_count": 10,
        "defective_count": 1,
        "process": lines[0]["data"]["process"],
    }
    headers["Idempotency-Key"] = str(uuid.uuid4())
    first = source.post(path, json=body, headers=headers)
    assert first.status_code == 200 and first.json()["status"] == "NG"
    headers["Idempotency-Key"] = str(uuid.uuid4())
    assert source.post(path, json=body, headers=headers).json()["id"] == first.json()["id"]
    headers["Idempotency-Key"] = str(uuid.uuid4())
    assert source.post(path, json={**body, "line_id": lines[1]["id"]}, headers=headers).status_code == 403
    for status, changes in [
        ("unassociated", {"object_id": None}),
        ("missing_image", {"image_ids": []}),
        ("stale", {"occurred_at": (datetime.now(UTC) - timedelta(days=8)).isoformat()}),
        ("unknown_defect", {"defects": [{"category": "未知新缺陷", "count": 1}]}),
    ]:
        headers["Idempotency-Key"] = str(uuid.uuid4())
        res = source.post(path, json={**body, "record_id": status, **changes}, headers=headers)
        assert res.status_code == 200 and res.json()["status"] == status, res.text
    assert (
        source.post(
            path, json=body, headers={"Authorization": "Bearer wrong", "Idempotency-Key": str(uuid.uuid4())}
        ).status_code
        == 401
    )


def test_visual_raw_receipt_preserves_original_timezone_text(core):
    _, app, clients, lines=core
    from fastapi.testclient import TestClient
    adapter=post(clients['admin'],'/adapters',{'name':'原始时间保留夹具','protocol':'visual','enabled':True,
                  'provenance':'reference','config':{'line_id':lines[0]['id']}})
    stamp=datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).isoformat()
    body={'record_id':'timezone-raw','line_id':lines[0]['id'],'object_id':'SYNTHETIC-TIME-OBJECT',
          'granularity':'bottle','association':'manual_confirmed','occurred_at':stamp,'status':'OK',
          'model_version':'reference','config_version':'test-v1','inspected_count':1,'defective_count':0,
          'process':lines[0]['data']['process']}
    response=TestClient(app).post('/api/ingest/visual/'+adapter['id'],json=body,
        headers={'Authorization':'Bearer '+adapter['token'],'Idempotency-Key':'raw-original-timezone'})
    assert response.status_code==200
    received=response.json()
    sample=clients['admin'].get('/api/raw-records/'+received['reception']['raw_record_id']+'/sample').json()
    assert sample['sample']['occurred_at']==stamp and stamp.endswith('+08:00')
    assert received['result']['occurred_at'].endswith('Z') or received['result']['occurred_at'].endswith('+00:00')
