import copy

from fastapi.testclient import TestClient

from quality.app import create_app
from quality.cli import initialize, migrate
from quality.security import digest, token
from quality.sync import public_key
from .conftest import PASSWORD, post


def test_signed_package_scope_tamper_and_idempotent_apply(core, tmp_path):
    settings, _, clients, lines = core
    k = post(
        clients["engineer"],
        "/knowledge",
        {
            "category": "sop",
            "title": "发布包测试说明",
            "content": "受控签名的合成知识条目",
            "process": "plastic_spray",
            "source": {"description": "软件测试参考"},
        },
    )
    post(
        clients["engineer"], f"/knowledge/{k['id']}/review", {"action": "submit", "reason": "提交发布包审核"}
    )
    post(
        clients["approver"],
        f"/knowledge/{k['id']}/review",
        {"action": "publish", "reason": "仅用于签名传输测试"},
    )
    center = settings.model_copy(
        update={"node_role": "center", "node_id": "center", "package_secret": token()}
    )
    center_client = TestClient(create_app(center))
    center_client.cookies.update(clients["approver"].cookies)
    response = center_client.get("/api/packages/knowledge/" + k["id"])
    assert response.status_code == 200, response.text
    package = response.json()
    fresh = settings.model_copy(
        update={
            "database_url": f"sqlite:///{tmp_path / 'target.db'}",
            "data_dir": tmp_path / "target",
            "package_public_key": public_key(center),
        }
    )
    migrate(fresh)
    initialize(fresh, "admin", PASSWORD)
    target = TestClient(create_app(fresh))
    post(target, "/auth/login", {"username": "admin", "password": PASSWORD})
    assert post(target, "/packages/preview", package)["verified"]
    first = post(target, "/packages/apply", package)
    assert first["id"] == k["id"]
    assert post(target, "/packages/apply", package)["already_applied"]
    incompatible=copy.deepcopy(package)
    incompatible['body']['compatibility']={'contract':'quality-config-v1','minimum_schema':'9999'}
    import base64
    import json
    from quality.sync import private_key
    encoded=json.dumps(incompatible['body'],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    incompatible['sha256']=digest(encoded)
    incompatible['signature']=base64.b64encode(private_key(center).sign(encoded)).decode()
    post(target,'/packages/preview',incompatible,expected=409)
    bad = copy.deepcopy(package)
    bad["body"]["record"]["content"] = "被篡改内容"
    post(target, "/packages/preview", bad, expected=422)
    bad["sha256"] = digest(
        __import__("json")
        .dumps(bad["body"], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        .encode()
    )
    post(target, "/packages/preview", bad, expected=403)
    other = fresh.model_copy(update={"site_id": "factory-b"})
    client = TestClient(create_app(other))
    # The local account remains bound to A; changing node configuration cannot grant B.
    assert client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 403
