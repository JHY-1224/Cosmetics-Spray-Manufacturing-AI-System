from datetime import datetime, UTC
from fastapi.testclient import TestClient
from quality.app import create_app
from quality import models as m
from quality.db import Database
from quality.security import sign
from .conftest import post


def test_quality_corrections_units_and_center_fact_scope(core):
    settings, _, clients, lines = core
    line = next(r for r in lines if r["data"]["process"] == "plastic_spray")
    base = {
        "record_id": "original",
        "line_id": line["id"],
        "object_id": "BOTTLE-1",
        "granularity": "bottle",
        "association": "manual_confirmed",
        "occurred_at": datetime.now(UTC).isoformat(),
        "status": "NG",
        "defects": [{"category": "颗粒", "count": 2}],
        "model_version": "manual",
        "config_version": "1",
        "inspected_count": 2,
        "defective_count": 1,
    }
    original = post(clients["inspector"], "/inspections", base)
    post(
        clients["inspector"],
        "/inspections",
        {
            **base,
            "record_id": "correction",
            "status": "OK",
            "defective_count": 0,
            "defects": [],
            "correction_of": original["id"],
            "correction_reason": "人工复核原登记错误",
        },
    )
    stats = clients["viewer"].get("/api/statistics").json()
    assert (
        stats["inspected_count"] == 2 and stats["defective_count"] == 0 and stats["first_pass_yield"] is None
    )
    post(
        clients["inspector"],
        "/inspections",
        {**base, "record_id": "sample", "object_id": "SAMPLE-1", "granularity": "sample"},
    )
    assert clients["viewer"].get("/api/statistics").json()["inspected_count"] is None
    assert clients["viewer"].get("/api/statistics?granularity=bottle").json()["inspected_count"] == 2
    assert clients["viewer"].get("/api/statistics?start=2026-01-01T00:00:00").status_code == 422
    center_settings = settings.model_copy(
        update={"node_role": "center", "node_id": "center", "package_secret": "test-only-signing-seed"}
    )
    center = TestClient(create_app(center_settings))
    center.cookies.update(clients["admin"].cookies)
    site = post(
        center, "/sites", {"node_id": "remote-site", "site_id": settings.site_id, "name": "质量事实参考站"}
    )
    facts = []
    with Database(settings).session() as db:
        from sqlalchemy import select

        facts = [r for r in db.scalars(select(m.Outbox)).all() if r.kind == "inspection.recorded"]
    events = [
        {
            "id": r.id,
            "sequence": i + 1,
            "kind": r.kind,
            "payload": r.payload,
            "occurred_at": r.created_at.isoformat(),
        }
        for i, r in enumerate(facts)
    ]
    body = {"source": "remote-site", "target": "center", "events": events}
    received = center.post(
        "/api/sync/push",
        headers={"Authorization": "Bearer " + site["token"]},
        json={"body": body, "signature": sign(body, site["token"])},
    )
    assert received.status_code == 200, received.text
    stats = center.get("/api/statistics?granularity=bottle").json()
    assert (
        stats["inspected_count"] == 2
        and stats["defective_count"] == 0
        and stats["by_factory_line"][0]["site_id"] == settings.site_id
    )
    repeat = center.post(
        "/api/sync/push",
        headers={"Authorization": "Bearer " + site["token"]},
        json={"body": body, "signature": sign(body, site["token"])},
    )
    assert len(repeat.json()["duplicates"]) == len(events)
    assert center.get("/api/statistics?granularity=bottle").json()["inspected_count"] == 2
