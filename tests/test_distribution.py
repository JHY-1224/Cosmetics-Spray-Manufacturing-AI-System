import copy
import threading
import time
from datetime import timedelta

import pytest
import uvicorn
from fastapi.testclient import TestClient

from quality import models as m
from quality.app import create_app
from quality.cli import initialize, migrate
from quality.db import Database
from quality.security import current_time, token
from quality.sync import public_key, sync_once
from .conftest import PASSWORD, post
from .test_protocols import free_port


def test_real_center_site_rollout_review_ack_retire_rollback_and_replay(core, tmp_path):
    settings, _, clients, _ = core
    knowledge = post(
        clients["engineer"],
        "/knowledge",
        {
            "category": "sop",
            "title": "受控分发参考SOP",
            "content": "测试版本，不用于生产",
            "process": "plastic_spray",
            "source": {"provenance": "reference"},
        },
    )
    post(
        clients["engineer"],
        f"/knowledge/{knowledge['id']}/review",
        {"action": "submit", "reason": "提交独立审核"},
    )
    post(
        clients["approver"],
        f"/knowledge/{knowledge['id']}/review",
        {"action": "publish", "reason": "通过参考规则审核"},
    )
    configured = settings.model_copy(
        update={"node_role": "center", "node_id": "center", "package_secret": token()}
    )
    center_app = create_app(configured)
    center = TestClient(center_app)
    center.cookies.update(clients["admin"].cookies)
    registration = post(
        center, "/sites", {"node_id": "target-node", "site_id": settings.site_id, "name": "独立分发目标站"}
    )
    port = free_port()
    target_settings = settings.model_copy(
        update={
            "data_dir": tmp_path / "target",
            "database_url": f"sqlite:///{tmp_path / 'target.db'}",
            "node_id": "target-node",
            "center_url": f"http://127.0.0.1:{port}",
            "sync_token": registration["token"],
            "package_public_key": public_key(configured),
        }
    )
    migrate(target_settings)
    initialize(target_settings, "site-admin", PASSWORD)
    target = TestClient(create_app(target_settings))
    post(target, "/auth/login", {"username": "site-admin", "password": PASSWORD})
    server = uvicorn.Server(uvicorn.Config(center_app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.01)

    def rollout(action):
        return post(
            center,
            "/distributions",
            {
                "kind": "knowledge",
                "record_id": knowledge["id"],
                "target_nodes": ["target-node"],
                "action": action,
                "reason": "独立测试分发依据",
            },
        )[0]

    def synchronize():
        return sync_once(target_settings, Database(target_settings))

    try:
        queued = rollout("publish")
        synchronize()
        rows = target.get("/api/distributions").json()
        assert rows[0]["status"] == "received"
        assert target.get("/api/knowledge").json() == []  # Reception cannot activate it.
        preview = post(target, f"/distributions/{queued['id']}/preview")
        assert preview["verified"] and not preview["conflict"] and not preview["auto_bind"]
        applied = post(
            target,
            f"/distributions/{queued['id']}/decide",
            {"decision": "apply", "reason": "本站独立核对内容"},
        )
        assert applied["status"] == "applied"
        synchronize()
        assert center.get("/api/distributions").json()[0]["status"] == "applied"
        synchronize()
        assert len(target.get("/api/knowledge").json()) == 1
        post(target, "/packages/apply", queued["package"], expected=409)
        retirement = rollout("retire")
        synchronize()
        assert target.get("/api/knowledge").json()[0]["status"] == "published"
        post(
            target,
            f"/distributions/{retirement['id']}/decide",
            {"decision": "apply", "reason": "本站批准停用旧版本"},
        )
        assert target.get("/api/knowledge").json()[0]["status"] == "retired"
        rollback = rollout("rollback")
        synchronize()
        post(
            target,
            f"/distributions/{rollback['id']}/decide",
            {"decision": "apply", "reason": "核对历史批准依据后回退"},
        )
        synchronize()
        assert target.get("/api/knowledge").json()[0]["status"] == "published"
        post(
            target,
            f"/distributions/{queued['id']}/decide",
            {"decision": "apply", "reason": "旧请求不能再次推进"},
            expected=409,
        )
        forged = copy.deepcopy(rollback["package"])
        forged["body"]["record"]["content"] = "篡改内容"
        post(target, "/distributions/import/preview", forged, expected=422)
        expired = rollout("publish")
        # A stale local request cannot become effective even while center is unreachable.
        synchronize()
        with Database(target_settings).session() as db:
            db.get(m.Distribution, expired["id"]).expires_at = current_time() - timedelta(seconds=1)
        result = post(
            target,
            f"/distributions/{expired['id']}/decide",
            {"decision": "apply", "reason": "超期请求必须失效"},
        )
        assert result["status"] == "expired"
        post(
            clients["viewer"],
            "/distributions",
            {
                "kind": "knowledge",
                "record_id": knowledge["id"],
                "target_nodes": ["target-node"],
                "reason": "越权分发测试",
            },
            expected=403,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=3)
    with pytest.raises(Exception):
        synchronize()


def test_distribution_conflict_does_not_overwrite_local_version(core, tmp_path):
    from quality.distribution import build_package
    from quality.security import Actor
    from quality.service import Service

    settings, _, clients, _ = core
    k = post(
        clients["engineer"],
        "/knowledge",
        {
            "category": "sop",
            "title": "相同ID保护规则",
            "content": "原始内容",
            "process": "plastic_spray",
            "source": {},
        },
    )
    post(clients["engineer"], f"/knowledge/{k['id']}/review", {"action": "submit", "reason": "独立审核提交"})
    post(clients["approver"], f"/knowledge/{k['id']}/review", {"action": "publish", "reason": "独立审核发布"})
    center = settings.model_copy(update={"node_role": "center", "package_secret": token()})
    with Database(settings).session() as db:
        svc = Service(db, settings, Actor("test", "admin", [settings.site_id]))
        row = svc.get(m.Knowledge, k["id"])
        package = build_package(
            center,
            row,
            "knowledge",
            {
                "id": m.uid(),
                "target_node": settings.node_id,
                "site_id": settings.site_id,
                "action": "publish",
                "generation": 1,
                "expires_at": (current_time() + timedelta(hours=1)).isoformat(),
                "reason": "冲突保护测试",
                "expected_hash": "",
            },
        )
        row.content = "本站不同内容"
    configured = settings.model_copy(update={"package_public_key": public_key(center)})
    target = TestClient(create_app(configured))
    target.cookies.update(clients["admin"].cookies)
    assert post(target, "/distributions/import/preview", package)["conflict"]
    received = post(target, "/distributions/import", package)
    post(
        target,
        f"/distributions/{received['id']}/decide",
        {"decision": "apply", "reason": "不允许覆盖本地冲突"},
        expected=409,
    )
    assert target.get("/api/knowledge").json()[0]["content"] == "本站不同内容"
