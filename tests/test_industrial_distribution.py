import copy
from fastapi.testclient import TestClient
from quality.app import create_app
from quality.backup import backup, restore
from quality.security import token
from quality.sync import public_key
from .conftest import post
from .test_industrial_formulations import setup_formula, approve


def test_signed_industrial_formulation_dependencies_and_explicit_local_activation(core, tmp_path):
    settings = core[0]
    clients, line, file, _, _, body = setup_formula(core)
    first = approve(clients, body)
    backup(settings, tmp_path / "before-revision")
    revised = approve(clients, {**body, "previous_id": first["id"], "technical_version": "test-only-v2"})
    center_settings = settings.model_copy(
        update={"node_role": "center", "node_id": "center", "package_secret": token()}
    )
    center = TestClient(create_app(center_settings))
    center.cookies.update(clients["admin"].cookies)
    post(
        center,
        "/sites",
        {"node_id": "industrial-target", "site_id": settings.site_id, "name": "隔离工业配置目标"},
    )
    rollout = post(
        center,
        "/distributions",
        {
            "kind": "formulation",
            "record_id": revised["id"],
            "target_nodes": ["industrial-target"],
            "reason": "软件验证材料配方签名分发",
        },
    )[0]
    package = rollout["package"]
    assert package["body"]["industrial_config"]["components"]
    assert len(package["body"]["industrial_config"]["dependencies"]) == 3  # two materials and original proof
    target_settings = settings.model_copy(
        update={
            "data_dir": tmp_path / "target",
            "database_url": f"sqlite:///{tmp_path / 'target' / 'business.db'}",
        }
    )
    restore(target_settings, tmp_path / "before-revision")
    target_settings = target_settings.model_copy(
        update={"node_id": "industrial-target", "package_public_key": public_key(center_settings)}
    )
    target = TestClient(create_app(target_settings))
    target.cookies.update(clients["approver"].cookies)
    assert target.get("/api/industrial/formulations/" + revised["id"]).status_code == 404
    preview = post(target, "/distributions/import/preview", package)
    assert not preview["dependency_errors"] and preview["requires_local_review"]
    staged = post(target, "/distributions/import", package)
    assert target.get("/api/industrial/formulations/" + revised["id"]).status_code == 404
    result = post(
        target,
        "/distributions/" + staged["id"] + "/decide",
        {"decision": "apply", "reason": "另一位本地审核人核对原件和所有材料依赖"},
    )
    assert result["status"] == "applied"
    installed = target.get("/api/industrial/formulations/" + revised["id"]).json()
    assert installed["status"] == "published" and len(installed["components"]) == 2
    assert target.get("/api/industrial/formulations/" + first["id"]).json()["status"] == "retired"
    tampered = copy.deepcopy(package)
    tampered["body"]["industrial_config"]["components"][0]["parts"] = "999"
    post(target, "/distributions/import/preview", tampered, expected=422)
    missing = copy.deepcopy(package)
    missing["body"]["industrial_config"]["dependencies"] = []
    post(target, "/distributions/import/preview", missing, expected=422)
