import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from quality import models as m
from quality.db import Database
from quality.security import Actor
from quality.service import Service
from quality.worker import dispatch
from .conftest import post
from .test_visual import PNG


def test_visual_anomaly_storm_queues_one_analysis_and_replay_has_no_side_effect(core):
    settings, app, clients, _ = core
    line = post(
        clients["engineer"],
        "/masters",
        {
            "kind": "line",
            "code": "AUTO-LINE",
            "name": "合成事件触发线",
            "data": {"process": "plastic_spray", "anomaly_threshold": {"min_rate": 0.1}},
        },
    )
    term = post(
        clients["engineer"],
        "/knowledge",
        {
            "category": "defect",
            "title": "颗粒",
            "content": "参考软件验证术语",
            "process": "plastic_spray",
            "source": {"provenance": "reference"},
        },
    )
    post(
        clients["engineer"], f"/knowledge/{term['id']}/review", {"action": "submit", "reason": "参考术语审核"}
    )
    post(
        clients["approver"],
        f"/knowledge/{term['id']}/review",
        {"action": "publish", "reason": "确认参考分类"},
    )
    adapter = post(
        clients["admin"],
        "/adapters",
        {
            "name": "自动事件参考源",
            "protocol": "visual",
            "enabled": True,
            "provenance": "reference",
            "config": {"line_id": line["id"]},
        },
    )
    source = TestClient(app)
    url = "/api/ingest/visual/" + adapter["id"]
    headers = {"Authorization": "Bearer " + adapter["token"], "Idempotency-Key": str(uuid.uuid4())}
    uploaded = source.post(
        url + "/files", files={"file": ("frame.png", PNG, "image/png")}, headers=headers
    ).json()
    body = {
        "line_id": line["id"],
        "object_id": "LOAD-AUTO",
        "granularity": "load",
        "association": "load_record",
        "occurred_at": datetime.now(UTC).isoformat(),
        "status": "NG",
        "defects": [{"category": "颗粒", "count": 1}],
        "image_ids": [uploaded["id"]],
        "model_version": "qualified-reference",
        "config_version": "1",
        "inspected_count": 10,
        "defective_count": 1,
        "process": "plastic_spray",
    }
    for index in range(3):
        headers["Idempotency-Key"] = str(uuid.uuid4())
        response = source.post(url, json={**body, "record_id": str(index)}, headers=headers)
        assert response.status_code == 200, response.text
    cases = [c for c in clients["engineer"].get("/api/cases").json() if c["line_id"] == line["id"]]
    assert len(cases) == 1
    db = Database(settings)
    with db.session() as session:
        svc = Service(session, settings, Actor("worker", "admin", [settings.site_id]))
        events = session.scalars(select(m.Outbox).where(m.Outbox.kind == "case.analysis.requested")).all()
        assert len(events) == 1
        item = {"id": events[0].id, "kind": events[0].kind, "payload": events[0].payload}
        dispatch(svc, item)
    with db.session() as session:
        svc = Service(session, settings, Actor("worker", "admin", [settings.site_id]))
        dispatch(svc, item)
        assert len(session.scalars(select(m.Run)).all()) == 1
    assert clients["engineer"].get("/api/cases/" + cases[0]["id"]).json()["status"] == "needs_evidence"
