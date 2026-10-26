"""Cookie authentication needs CSRF even when Origin is absent; bearer authentication is independent."""

from fastapi.testclient import TestClient
from .conftest import post


def test_cookie_post_requires_bound_double_submit_and_checks_origin(core):
    _, app, clients, _ = core
    admin = clients["admin"]
    cookie = admin.cookies["quality_session"]
    csrf = admin.cookies["quality_csrf"]
    raw = TestClient(app)
    raw.cookies.set("quality_session", cookie)
    raw.cookies.set("quality_csrf", csrf)
    assert raw.post("/api/auth/logout", json={}).json()["detail"]["code"] == "csrf_denied"
    assert raw.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": "forged"}).status_code == 403
    denied = raw.post(
        "/api/auth/logout", json={}, headers={"X-CSRF-Token": csrf, "Origin": "https://untrusted.invalid"}
    )
    assert denied.json()["detail"]["code"] == "origin_denied"
    # A valid cookie session can acquire the new cookie through authenticated /me after an upgrade.
    raw.cookies.clear()
    raw.cookies.set("quality_session", cookie)
    assert raw.get("/api/auth/me").status_code == 200
    assert raw.cookies["quality_csrf"] == csrf
    assert raw.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert admin.get("/api/auth/me").status_code == 401


def test_explicit_bearer_does_not_fall_back_to_cookie(core):
    _, app, clients, _ = core
    admin = clients["admin"]
    assert admin.get("/api/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
    raw = TestClient(app, headers={"Authorization": "Bearer " + admin.cookies["quality_session"]})
    assert raw.get("/api/auth/me").status_code == 200
    assert post(raw, "/auth/logout")["revoked"]
    assert raw.get("/api/auth/me").status_code == 401
