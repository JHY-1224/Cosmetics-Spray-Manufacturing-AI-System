from datetime import timedelta
import json
from fastapi.testclient import TestClient
from quality import models as m
from quality.anomalies import trigger
from quality.app import create_app
from quality.backup import backup, restore
from quality.cli import migrate
from quality.db import Database
from quality.operations import archive
from quality.security import Actor, current_time, digest
from quality.service import Service
from quality.storage import Storage
from quality.worker import local_dispatch_once
from .conftest import post
from .test_storage import s3_reference as _s3_fixture
from .test_workflow import file, case


def test_rolling_policy_requires_samples_preserves_units_and_local_worker(core, tmp_path):
    settings, _, clients, lines = core
    line = lines[0]
    policy = {
        "enabled": True,
        "min_rate": 0.1,
        "min_samples": 20,
        "min_ng_records": 2,
        "window_seconds": 600,
        "dedup_seconds": 3600,
    }
    post(
        clients["engineer"],
        f"/lines/{line['id']}/anomaly-policy",
        {"version": line["version"], "policy": policy},
    )
    post(
        clients["engineer"],
        f"/lines/{line['id']}/anomaly-policy",
        {"version": line["version"], "policy": policy},
        expected=409,
    )
    now = current_time()
    with Database(settings).session() as db:
        svc = Service(db, settings, Actor("test", "admin", [settings.site_id]))
        line_row = svc.get(m.Master, line["id"])

        def observation(key, grain="load"):
            return svc.add(
                m.Inspection,
                source_id="manual",
                record_id=key,
                line_id=line["id"],
                object_id=key,
                granularity=grain,
                association="manual_confirmed",
                occurred_at=now,
                status="NG",
                provenance="reference",
                result={
                    "process": "plastic_spray",
                    "inspected_count": 10,
                    "defective_count": 1,
                    "defects": [{"category": "颗粒", "count": 1}],
                },
            )

        first = observation("one")
        assert trigger(svc, line_row, first) is None
        observation("bottle", "bottle")
        assert trigger(svc, line_row, first) is None
        second = observation("two")
        result = trigger(svc, line_row, second)
        assert result["inspected_count"] == 20 and result["ng_records"] == 2
        first.result = {**first.result, "correction_of": second.id}
        assert trigger(svc, line_row, first) is None
    c = post(
        clients["engineer"],
        "/cases",
        {
            "title": "本地队列故障恢复",
            "line_id": line["id"],
            "process": line["data"]["process"],
            "defect": "颗粒",
        },
    )
    with Database(settings).session() as db:
        svc = Service(db, settings, Actor("test", "admin", [settings.site_id]))
        svc.event(
            "case.analysis.requested", {"case_id": c["id"], "version": c["version"], "line_id": line["id"]}
        )
    local_dispatch_once(settings, Database(settings))
    local_dispatch_once(settings, Database(settings))
    assert clients["engineer"].get("/api/cases/" + c["id"]).json()["rounds"] == 1
    archive_file = file(clients["engineer"])
    with Database(settings).session() as db:
        db.get(m.Evidence, archive_file).created_at = now - timedelta(days=100)
    result = archive(settings, tmp_path.parent / (tmp_path.name + "-archive"))
    assert result["archived"] == 1 and not result["source_deleted"]
    assert clients["engineer"].get("/api/files/" + archive_file).status_code == 200
    assert clients["engineer"].get("/api/operations/alerts").status_code == 200


def test_entire_s3_backup_restore_to_independent_service_and_fresh_database(core, tmp_path_factory, tmp_path):
    settings, _, clients, lines = core
    original = s3_reference_function(tmp_path_factory)
    _, url, access, secret, _ = next(original)
    source = settings.model_copy(
        update={
            "storage_backend": "s3",
            "s3_endpoint": url,
            "s3_access_key": access,
            "s3_secret_key": secret,
            "s3_bucket": "quality-test",
        }
    )
    # Real HTTP upload populates source S3, its local cache and immutable database references.
    client = TestClient(create_app(source))
    client.cookies.update(clients["engineer"].cookies)
    evidence = file(client)
    c = case(client, lines[0])
    journal = source.data_dir / "model-journal" / "recovery-test"
    journal.mkdir(parents=True)
    (journal / "call.json").write_text(json.dumps({"status": "reserved", "reserved_tokens": 123}))
    spool = source.data_dir / "adapter-spool" / "recovery-test"
    spool.mkdir(parents=True)
    (spool / "raw.json").write_text('{"records":[{"metric":"test"}]}')
    saved = backup(source, tmp_path / "complete-backup")
    assert saved["objects"] == 1
    generator = s3_reference_function(tmp_path_factory)
    independent = next(generator)
    try:
        _, new_url, new_access, new_secret, new_client = independent
        fresh = source.model_copy(
            update={
                "data_dir": tmp_path / "fresh-node",
                "database_url": f"sqlite:///{tmp_path / 'fresh.db'}",
                "s3_endpoint": new_url,
                "s3_access_key": new_access,
                "s3_secret_key": new_secret,
            }
        )
        assert restore(fresh, tmp_path / "complete-backup")["restored"]
        migrate(fresh)
        target = TestClient(create_app(fresh))
        target.cookies.update(client.cookies)
        assert (
            target.get("/api/cases/" + c["id"]).json()["context"]
            == client.get("/api/cases/" + c["id"]).json()["context"]
        )
        assert target.get("/api/files/" + evidence).content == b"manual measurement evidence"
        assert (
            json.loads((fresh.data_dir / "model-journal/recovery-test/call.json").read_text())["status"]
            == "reserved"
        )
        assert (fresh.data_dir / "adapter-spool/recovery-test/raw.json").exists()
        row = client.get("/api/files").json()[0]
        # Independent reader has no cache: bytes are actually restored in the second service.
        reader = Storage(fresh.model_copy(update={"data_dir": tmp_path / "empty-reader"}))
        assert reader.get(row["object_key"], row["sha256"]) == b"manual measurement evidence"
        new_client.put_object(
            Bucket="quality-test",
            Key="immutable-key",
            Body=b"original",
            Metadata={"sha256": digest(b"original")},
        )
        assert reader.put("immutable-key", b"different", "text/plain") == "conflict"
        assert new_client.get_object(Bucket="quality-test", Key="immutable-key")["Body"].read() == b"original"
    finally:
        generator.close()
        original.close()


# Access the same verified native reference-service factory for a second isolated process.
s3_reference_function = _s3_fixture.__wrapped__
