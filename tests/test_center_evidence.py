import base64
import json
import threading
import time
from datetime import timedelta
import uvicorn
from fastapi.testclient import TestClient
from quality.app import create_app
from quality.db import Database
from quality import models as m
from quality.security import current_time, sign, token
from quality.sync import public_key, private_key
from .conftest import post
from .test_protocols import free_port
from .test_workflow import file


def test_center_real_http_evidence_scope_signature_expiry_and_hash(core):
    settings, _, clients, lines = core
    center_settings = settings.model_copy(
        update={"node_role": "center", "node_id": "center", "package_secret": token()}
    )
    site_settings = settings.model_copy(update={"package_public_key": public_key(center_settings)})
    evidence = file(clients["engineer"])
    case = post(
        clients["engineer"],
        "/cases",
        {
            "title": "中心证据读取联调",
            "line_id": lines[0]["id"],
            "process": lines[0]["data"]["process"],
            "defect": "颗粒",
            "evidence_ids": [evidence],
        },
    )
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(site_settings), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.01)
    try:
        center = TestClient(create_app(center_settings))
        center.cookies.update(clients["admin"].cookies)
        registration = post(
            center,
            "/sites",
            {
                "node_id": settings.node_id,
                "site_id": settings.site_id,
                "name": "原始证据参考站",
                "base_url": f"http://127.0.0.1:{port}",
            },
        )
        with Database(settings).session() as db:
            from sqlalchemy import select

            event = db.scalar(select(m.Outbox).where(m.Outbox.kind == "case.changed"))
            body = {
                "source": settings.node_id,
                "target": "center",
                "events": [
                    {
                        "id": event.id,
                        "sequence": 1,
                        "kind": event.kind,
                        "payload": event.payload,
                        "occurred_at": event.created_at.isoformat(),
                    }
                ],
            }
        response = center.post(
            "/api/sync/push",
            headers={"Authorization": "Bearer " + registration["token"]},
            json={"body": body, "signature": sign(body, registration["token"])},
        )
        assert response.status_code == 200, response.text
        snapshot = center.get("/api/site-snapshots").json()[0]
        assert snapshot["data"]["id"] == case["id"]
        assert snapshot["data"]["context"]["evidence_ids"] == [evidence]
        path = f"/api/site-snapshots/{snapshot['id']}/files/{evidence}"
        fetched = center.get(path)
        assert fetched.status_code == 200 and fetched.content == b"manual measurement evidence"
        assert center.get(path + "-unknown").status_code == 404
        site = TestClient(create_app(site_settings))
        assert site.post("/api/center/evidence", json={"body": {}, "signature": "forged"}).status_code == 403
        payload = {
            "purpose": "evidence_read_v1",
            "tenant_id": settings.tenant_id,
            "site_id": settings.site_id,
            "node_id": settings.node_id,
            "line_id": lines[0]["id"],
            "file_id": evidence,
            "actor_id": "center-reader",
            "expires_at": (current_time() - timedelta(seconds=1)).isoformat(),
        }

        def signed(value):
            return {
                "body": value,
                "signature": base64.b64encode(
                    private_key(center_settings).sign(
                        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                    )
                ).decode(),
            }

        assert site.post("/api/center/evidence", json=signed(payload)).status_code == 403
        payload.update(
            line_id=lines[1]["id"], expires_at=(current_time() + timedelta(seconds=30)).isoformat()
        )
        assert site.post("/api/center/evidence", json=signed(payload)).status_code == 404
    finally:
        server.should_exit = True
        thread.join(timeout=3)
