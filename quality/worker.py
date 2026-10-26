import json
import logging
from datetime import timedelta

import pika

from . import models as m
from .agent import resume_review
from .db import Database
from .security import Actor, current_time
from .service import Service
from .sync import sync_once

LOG = logging.getLogger("quality.worker")


def dispatch(svc, item):
    if item["kind"] == "workflow.reviewed":
        resume_review(svc.settings, item["payload"]["run_id"], item["payload"])
        run = svc.get(m.Run, item["payload"]["run_id"])
        run.status = "reviewed"
    elif item["kind"] == "case.analysis.requested":
        from .agent import analyze

        case = svc.get(m.Case, item["payload"]["case_id"], True)
        if case.status == "accepted" and case.version == item["payload"]["version"]:
            analyze(svc, case)
        else:
            svc.audit("analysis.stale_event_ignored", case.id, {"event_id": item["id"]})


def publish_once(settings, database):
    if not settings.rabbitmq_url:
        return (
            local_dispatch_once(settings, database)
            if settings.mode != "production"
            else {"status": "not_configured"}
        )
    connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    try:
        channel = connection.channel()
        channel.exchange_declare(exchange="quality", exchange_type="topic", durable=True)
        channel.exchange_declare(exchange="quality.dead", exchange_type="fanout", durable=True)
        channel.queue_declare(
            queue=f"{settings.node_id}.events",
            durable=True,
            arguments={"x-dead-letter-exchange": "quality.dead"},
        )
        channel.queue_bind(
            exchange="quality",
            queue=f"{settings.node_id}.events",
            routing_key=f"{settings.tenant_id}.{settings.node_id}.#",
        )
        channel.queue_declare(queue=f"{settings.node_id}.dead", durable=True)
        channel.queue_bind(exchange="quality.dead", queue=f"{settings.node_id}.dead")
        channel.confirm_delivery()
        sent = 0
        with database.session() as db:
            svc = Service(db, settings, Actor("worker", "admin", settings.sites))
            rows = db.scalars(
                svc.scoped(m.Outbox)
                .where(m.Outbox.status == "pending", m.Outbox.next_at <= current_time())
                .order_by(m.Outbox.created_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                try:
                    envelope = {
                        "id": row.id,
                        "tenant_id": settings.tenant_id,
                        "site_id": row.site_id,
                        "source": settings.node_id,
                        "kind": row.kind,
                        "payload": row.payload,
                    }
                    channel.basic_publish(
                        exchange="quality",
                        routing_key=f"{settings.tenant_id}.{settings.node_id}.{row.kind}",
                        body=json.dumps(envelope).encode(),
                        mandatory=True,
                        properties=pika.BasicProperties(
                            delivery_mode=2, message_id=row.id, content_type="application/json"
                        ),
                    )
                    row.status = "published"
                    sent += 1
                except Exception as exc:
                    row.attempts += 1
                    row.last_error = type(exc).__name__
                    row.status = "dead" if row.attempts >= 5 else "pending"
                    row.next_at = current_time() + timedelta(seconds=min(300, 2**row.attempts))
        # Explicit acknowledgement occurs after the inbox transaction commits.
        for _ in range(100):
            method, properties, body = channel.basic_get(queue=f"{settings.node_id}.events", auto_ack=False)
            if method is None:
                break
            try:
                item = json.loads(body)
                if item["tenant_id"] != settings.tenant_id or item["site_id"] not in ("", settings.site_id):
                    raise ValueError("scope_mismatch")
                with database.session() as db:
                    svc = Service(db, settings, Actor("worker", "admin", settings.sites))
                    prior = db.scalar(
                        svc.scoped(m.Inbox).where(
                            m.Inbox.source_node == settings.node_id + ":broker",
                            m.Inbox.event_id == item["id"],
                        )
                    )
                    if not prior:
                        dispatch(svc, item)
                        svc.add(
                            m.Inbox,
                            event_id=item["id"],
                            source_node=settings.node_id + ":broker",
                            sequence=0,
                            payload=item,
                        )
                channel.basic_ack(method.delivery_tag)
            except Exception:
                # Persist the retry decision before acknowledging the consumed delivery.
                with database.session() as db:
                    svc = Service(db, settings, Actor("worker", "admin", settings.sites))
                    row = db.get(m.Outbox, properties.message_id)
                    if row:
                        row.attempts += 1
                        row.status = "dead" if row.attempts >= 5 else "pending"
                        row.last_error = "consumer_processing_failed"
                        row.next_at = current_time() + timedelta(seconds=min(300, 2**row.attempts))
                        svc.audit(
                            "event.consumer_failed", row.id, {"attempts": row.attempts, "status": row.status}
                        )
                if row and row.status == "pending":
                    channel.basic_ack(method.delivery_tag)
                else:
                    channel.basic_nack(method.delivery_tag, requeue=False)
        return {"sent": sent}
    finally:
        connection.close()


def local_dispatch_once(settings, database):
    """Durable local development queue; production requires the configured broker."""
    with database.session() as db:
        svc = Service(db, settings, Actor("local-worker", "admin", settings.sites))
        ids = list(
            db.scalars(
                svc.scoped(m.Outbox)
                .where(m.Outbox.status == "pending", m.Outbox.next_at <= current_time())
                .order_by(m.Outbox.created_at)
                .limit(100)
                .with_only_columns(m.Outbox.id)
            )
        )
    completed = 0
    for row_id in ids:
        try:
            with database.session() as db:
                svc = Service(db, settings, Actor("local-worker", "admin", settings.sites))
                row = svc.get(m.Outbox, row_id, True)
                if row.status != "pending":
                    continue
                prior = db.scalar(
                    svc.scoped(m.Inbox).where(
                        m.Inbox.event_id == row.id, m.Inbox.source_node == settings.node_id + ":local"
                    )
                )
                if not prior:
                    item = {"id": row.id, "kind": row.kind, "payload": row.payload}
                    dispatch(svc, item)
                    svc.add(
                        m.Inbox,
                        event_id=row.id,
                        source_node=settings.node_id + ":local",
                        sequence=0,
                        payload=item,
                        scope_line_id=row.scope_line_id,
                    )
                row.status = "published"
                completed += 1
        except Exception as exc:
            with database.session() as db:
                svc = Service(db, settings, Actor("local-worker", "admin", settings.sites))
                row = svc.get(m.Outbox, row_id, True)
                row.attempts += 1
                row.last_error = type(exc).__name__
                row.status = "dead" if row.attempts >= 5 else "pending"
                row.next_at = current_time() + timedelta(seconds=min(300, 2**row.attempts))
                svc.audit("event.local_failed", row.id, {"status": row.status, "attempts": row.attempts})
    return {"status": "local_development_queue", "completed": completed}


def run(settings, stopping=None):
    import signal
    import threading

    database = Database(settings)
    if settings.mode == "production":
        database.check_runtime_role()
    logging.basicConfig(level=logging.INFO)
    stopping = stopping or threading.Event()

    def stop(signum, frame):
        stopping.set()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
    from .learning_tasks import serve

    learning_thread = threading.Thread(
        target=serve, args=(settings, stopping), daemon=True, name="learning-dispatch"
    )
    learning_thread.start()
    from .digital_tasks import serve as serve_digital

    digital_thread = threading.Thread(
        target=serve_digital, args=(settings, stopping), daemon=True, name="digital-dispatch"
    )
    digital_thread.start()
    from .business_gateway import serve as serve_business

    business_thread = threading.Thread(
        target=serve_business, args=(settings, stopping), daemon=True, name="business-dispatch"
    )
    business_thread.start()
    while not stopping.is_set():
        from .ingestion import collect_adapter, recover_raw

        with database.session() as db:
            svc = Service(db, settings, Actor("collector", "admin", settings.sites))
            recover_raw(svc)
            for adapter in db.scalars(
                svc.scoped(m.Adapter).where(m.Adapter.enabled.is_(True), m.Adapter.protocol != "visual")
            ).all():
                if stopping.is_set():
                    break
                try:
                    from datetime import datetime
                    from .security import aware

                    last = adapter.health.get("last_attempt") or adapter.health.get("last_success")
                    if (
                        last
                        and aware(datetime.fromisoformat(last))
                        + timedelta(seconds=adapter.config.get("poll_seconds", 5))
                        > current_time()
                    ):
                        continue
                    collect_adapter(svc, adapter.id)
                except Exception as exc:
                    LOG.warning("adapter_unavailable %s %s", adapter.id, type(exc).__name__)
        if stopping.is_set():
            break
        if settings.storage_backend == "s3":
            from .storage import Storage

            store = Storage(settings)
            with database.session() as db:
                svc = Service(db, settings, Actor("evidence-replicator", "admin", settings.sites))
                for obj in db.scalars(
                    svc.scoped(m.Evidence)
                    .where(m.Evidence.replication_status.in_(["local", "pending"]))
                    .order_by(m.Evidence.created_at)
                    .limit(100)
                ).all():
                    if stopping.is_set():
                        break
                    try:
                        store.reconcile(obj.object_key, obj.media_type, obj.sha256)
                        obj.replication_status = "synced"
                        svc.audit("evidence.replicated", obj.id)
                    except ValueError:
                        obj.replication_status = "conflict"
                        svc.audit("evidence.replication_conflict", obj.id)
                    except Exception as exc:
                        LOG.warning("evidence_replication_unavailable %s", type(exc).__name__)
                        break
        try:
            publish_once(settings, database)
        except Exception as exc:
            LOG.warning("broker_unavailable %s", type(exc).__name__)
        if settings.center_url and settings.node_role == "site":
            try:
                sync_once(settings, database)
            except Exception as exc:
                LOG.warning("center_unavailable_local_business_continues %s", type(exc).__name__)
        from .model_journal import write

        write(
            settings.data_dir / "worker-status.json",
            {
                "node_id": settings.node_id,
                "last_completed_tick": current_time().isoformat(),
                "status": "running",
                "schema_version": "0012",
            },
        )
        stopping.wait(5)
    learning_thread.join(timeout=10)
    digital_thread.join(timeout=10)
    business_thread.join(timeout=settings.business_timeout + 2)
    from .model_journal import write

    write(
        settings.data_dir / "worker-status.json",
        {
            "node_id": settings.node_id,
            "status": "stopped",
            "stopped_at": current_time().isoformat(),
            "schema_version": "0012",
        },
    )
    database.engine.dispose()
