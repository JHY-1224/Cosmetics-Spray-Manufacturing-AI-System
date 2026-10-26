import copy
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from quality import models as m
from quality.app import create_app
from quality.db import Database
from quality.identity import apply_authorizations, renew_local_lease, validate_identity
from quality.security import Actor, PASSWORDS, current_time, token
from quality.service import Service
from .conftest import PASSWORD, post


def test_center_identity_lease_revocation_and_conflicts(environment):
    settings, _, clients = environment
    dbs = Database(settings)
    item = {
        "id": m.uid(),
        "username": "central-engineer",
        "role": "engineer",
        "revision": 1,
        "active": True,
        "allowed_sites": [settings.site_id],
        "allowed_lines": ["line-scope"],
        "password_hash": PASSWORDS.hash(PASSWORD),
    }
    snapshot = {
        "issued_at": current_time().isoformat(),
        "expires_at": (current_time() + timedelta(hours=24)).isoformat(),
        "complete": True,
        "users": [item],
    }

    def sync(value):
        with dbs.session() as db:
            svc = Service(db, settings, Actor("sync-worker", "admin", [settings.site_id]))
            return apply_authorizations(svc, value)

    assert sync(snapshot)["imported"] == 1
    actor = TestClient(create_app(settings))
    post(actor, "/auth/login", {"username": item["username"], "password": PASSWORD})
    assert actor.get("/api/auth/me").status_code == 200
    assert sync(snapshot)["imported"] == 0
    changed = copy.deepcopy(snapshot)
    changed["users"][0]["role"] = "admin"
    assert sync(changed)["conflicts"][0]["reason"] == "same_revision_conflict"
    assert actor.get("/api/auth/me").status_code == 401
    # Conflicting same-version authority fails closed; a newer center revision is required.
    snapshot["users"][0]["revision"] = 2
    sync(snapshot)
    post(actor, "/auth/login", {"username": item["username"], "password": PASSWORD})
    with dbs.session() as db:
        db.get(m.User, item["id"]).lease_expires_at = current_time() - timedelta(seconds=1)
    assert actor.get("/api/auth/me").status_code == 401
    sync(snapshot)
    post(actor, "/auth/login", {"username": item["username"], "password": PASSWORD})
    revoked = copy.deepcopy(snapshot)
    revoked["users"][0].update(active=False, revision=3)
    sync(revoked)
    assert actor.get("/api/auth/me").status_code == 401
    post(actor, "/auth/login", {"username": item["username"], "password": PASSWORD}, expected=401)
    assert sync(snapshot)["conflicts"][0]["reason"] == "stale_authorization"
    collision = copy.deepcopy(snapshot)
    collision["users"][0].update(id=m.uid(), username="admin")
    assert sync(collision)["conflicts"][0]["reason"] == "local_identity_collision"
    assert clients["admin"].get("/api/auth/me").status_code == 200
    with dbs.session() as db:
        assert db.scalar(select(m.User).where(m.User.username == "admin")).auth_origin == "local"


def test_local_recovery_requires_key_original_password_and_retains_scope(environment):
    settings, _, _ = environment
    settings = settings.model_copy(
        update={"center_url": "http://localhost:9999", "local_recovery_key": token()}
    )
    dbs = Database(settings)
    with dbs.session() as db:
        user = db.scalar(select(m.User).where(m.User.username == "admin"))
        user.lease_expires_at = current_time() - timedelta(hours=1)
        with pytest.raises(HTTPException):
            validate_identity(user, settings)
        for password, key, hours in [
            (PASSWORD, "invalid", 1),
            ("invalid", settings.local_recovery_key, 1),
            (PASSWORD, settings.local_recovery_key, 5),
        ]:
            with pytest.raises(HTTPException):
                renew_local_lease(
                    db, settings, "admin", password, key, hours, "中心暂时失联，由本地负责人应急核验"
                )
        before = (user.role, list(user.allowed_sites), list(user.allowed_lines))
        result = renew_local_lease(
            db,
            settings,
            "admin",
            PASSWORD,
            settings.local_recovery_key,
            1,
            "中心暂时失联，由本地负责人应急核验",
        )
        assert result["scope_unchanged"] and before == (user.role, user.allowed_sites, user.allowed_lines)
        validate_identity(user, settings)
        assert db.scalar(select(m.Audit).where(m.Audit.action == "identity.emergency_lease"))
