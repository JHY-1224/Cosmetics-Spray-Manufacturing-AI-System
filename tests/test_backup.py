from fastapi.testclient import TestClient

from quality.app import create_app
from quality.backup import backup, restore, verify
from quality.cli import migrate
from .test_workflow import case, file


def test_backup_new_environment_restore_references_and_upgrade(core, tmp_path):
    settings, _, clients, lines = core
    evidence = file(clients["engineer"])
    c = case(clients["engineer"], lines[0])
    destination = tmp_path / "backup"
    report = backup(settings, destination)
    assert report["objects"] == 1
    manifest = verify(destination)
    assert manifest["objects"][0]["id"] == evidence
    fresh = settings.model_copy(
        update={"data_dir": tmp_path / "restored-data", "database_url": f"sqlite:///{tmp_path / 'new.db'}"}
    )
    restore(fresh, destination)
    migrate(fresh)
    client = TestClient(create_app(fresh))
    client.cookies.update(clients["engineer"].cookies)
    assert client.get("/api/cases/" + c["id"]).json()["diagnosis"]["run_id"] == c["diagnosis"]["run_id"]
    assert client.get("/api/files/" + evidence).content == b"manual measurement evidence"
    import pytest

    with pytest.raises(ValueError, match="空数据库"):
        restore(fresh, destination)
