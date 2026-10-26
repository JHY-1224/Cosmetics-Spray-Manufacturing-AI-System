import uuid

import pytest
from fastapi.testclient import TestClient

from quality.app import create_app
from quality.cli import initialize, migrate, seed_demo
from quality.config import Settings

PASSWORD = "Test-Only_Strong!7259"


def post(client, path, body=None, key=None, expected=200):
    csrf = client.cookies.get("quality_csrf")
    if csrf:
        client.headers["X-CSRF-Token"] = csrf
    response = client.post(
        "/api" + path, json=body or {}, headers={"Idempotency-Key": key or str(uuid.uuid4())}
    )
    assert response.status_code == expected, response.text
    if path == "/auth/login" and response.status_code == 200:
        client.headers["X-CSRF-Token"] = client.cookies["quality_csrf"]
    return response.json()


@pytest.fixture
def environment(tmp_path):
    settings = Settings(
        _env_file=None,
        mode="integration",
        tenant_id="test-customer",
        site_id="factory-a",
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'business.db'}",
    )
    migrate(settings)
    initialize(settings, "admin", PASSWORD)
    seed_demo(settings)
    app = create_app(settings)
    admin = TestClient(app)
    post(admin, "/auth/login", {"username": "admin", "password": PASSWORD})
    clients = {"admin": admin}
    import json

    passwords = json.loads((tmp_path / "demo-credentials.json").read_text())
    for role, password in passwords.items():
        client = TestClient(app)
        post(client, "/auth/login", {"username": role, "password": password})
        clients[role] = client
    return settings, app, clients


@pytest.fixture
def core(environment):
    settings, app, clients = environment
    lines = clients["engineer"].get("/api/masters?kind=line").json()
    return settings, app, clients, lines
