"""Read-only dependency probes and scoped software-business readiness, separate from liveness."""

import json
from datetime import datetime
import boto3
from botocore.config import Config
import pika
from sqlalchemy import text
from . import models as m
from .security import aware, current_time, endpoint
from .versioning import SCHEMA_HEAD


def dependencies(svc):
    svc.actor.require("admin", svc.settings.site_id)
    settings, states = svc.settings, {}
    try:
        revision = svc.db.scalar(text("SELECT version_num FROM alembic_version"))
        states["database"] = {
            "status": "ready" if revision == SCHEMA_HEAD else "migration_required",
            "schema": revision,
        }
    except Exception as exc:
        states["database"] = {"status": "unavailable", "error": type(exc).__name__}
    if settings.storage_backend == "s3":
        client = None
        try:
            endpoint(settings.s3_endpoint, settings)
            client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name="us-east-1",
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=2,
                    read_timeout=2,
                    retries={"total_max_attempts": 1},
                    s3={"addressing_style": "path"},
                ),
            )
            client.head_bucket(Bucket=settings.s3_bucket)
            states["object_storage"] = {"status": "ready", "probe": "authenticated_head_bucket"}
        except Exception as exc:
            states["object_storage"] = {"status": "unavailable", "error": type(exc).__name__}
        finally:
            if client:
                client.close()
    else:
        states["object_storage"] = {
            "status": "ready" if (settings.data_dir / "objects").is_dir() else "unavailable",
            "probe": "local_directory; object_integrity_verified_on_access",
        }
    if settings.rabbitmq_url:
        connection = None
        try:
            endpoint(settings.rabbitmq_url, settings, schemes=("amqp", "amqps"))
            parameters = pika.URLParameters(settings.rabbitmq_url)
            parameters.socket_timeout = parameters.stack_timeout = parameters.blocked_connection_timeout = 2
            parameters.connection_attempts = 1
            connection = pika.BlockingConnection(parameters)
            states["message_broker"] = {
                "status": "ready",
                "probe": "authenticated_connection; publishing_verified_separately",
            }
        except Exception as exc:
            states["message_broker"] = {"status": "unavailable", "error": type(exc).__name__}
        finally:
            if connection and connection.is_open:
                connection.close()
    else:
        states["message_broker"] = {
            "status": "local_outbox" if settings.mode != "production" else "unavailable"
        }
    try:
        worker = json.loads((settings.data_dir / "worker-status.json").read_text())
        age = (current_time() - aware(datetime.fromisoformat(worker["last_completed_tick"]))).total_seconds()
        healthy = (
            worker["status"] == "running"
            and worker["node_id"] == settings.node_id
            and worker["schema_version"] == SCHEMA_HEAD
            and 0 <= age <= 30
        )
        states["worker"] = {
            "status": "ready" if healthy else "stale",
            "heartbeat_age_seconds": max(0, round(age, 3)),
        }
    except (OSError, KeyError, ValueError, TypeError):
        states["worker"] = {"status": "not_verified"}
    return {
        "dependencies": states,
        "ready": all(v["status"] in {"ready", "local_outbox"} for v in states.values()),
        "checked_at": current_time().isoformat(),
        "scope": "software_runtime_only",
        "field_acceptance": "not_performed",
    }


def business(svc):
    result = dependencies(svc)
    from .business_gateway import frozen

    blockers = []
    for row in svc.db.scalars(svc.scoped(m.Adapter).where(m.Adapter.enabled.is_(True))):
        try:
            fresh = (
                0
                <= (
                    current_time() - aware(datetime.fromisoformat(row.health["last_success"]))
                ).total_seconds()
                <= max(60, row.config.get("poll_seconds", 5) * 3)
            )
        except (ValueError, KeyError, TypeError):
            fresh = False
        if not fresh or row.health.get("status") in {"unavailable", "data_quality_error"}:
            blockers.append({"code": "source_not_fresh" if not fresh else "source_error", "id": row.id})
    if svc.settings.business_send_enabled and frozen(svc.settings):
        blockers.append({"code": "external_send_frozen", "id": ""})
    result.update(
        {
            "ready": result["ready"] and not blockers,
            "blockers": blockers,
            "model": "configured_requires_separate_validation"
            if svc.settings.model_url
            else "disabled_deterministic_workflows_available",
            "business_send": "enabled_requires_per_action_authority"
            if svc.settings.business_send_enabled
            else "disabled_by_default",
            "source_coverage": "only_configured_sources_checked; full_population_and_process_acceptance_require_independent_evidence",
        }
    )
    return result
