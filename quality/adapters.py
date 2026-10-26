from .http_control import client as controlled_client
import asyncio
import json
import os
import ssl
import threading
import base64
from datetime import UTC, datetime, timedelta

import paho.mqtt.client as mqtt
from asyncua import Client
from pydantic import Field
from typing import Literal

from .schemas import Strict
from .security import endpoint, fail, digest


class AdapterConfig(Strict):
    endpoint: str
    secret_env: str = ""
    username: str = ""
    topic: str = "quality/measurements"
    nodes: list[str] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=5, ge=1, le=60)
    page_size: int = Field(default=100, ge=1, le=1000)
    cursor: str = ""
    max_pages: int = Field(default=10, ge=1, le=100)
    certificate: str = ""
    private_key: str = ""
    server_certificate: str = ""
    security_policy: str = ""
    tls: bool = True
    mode: str = "read"
    record_type: Literal["measurement", "master"] = "measurement"
    master_kind: str = "work_order"
    client_id: str = ""
    poll_seconds: int = Field(default=5, ge=1, le=86400)
    mapping_version: str = Field(default="legacy-v1", min_length=1, max_length=100)
    line_id: str = ""
    source_instance_id: str = Field(default="", max_length=100)
    max_future_seconds: int = Field(default=300, ge=0, le=86400)


def secret(config):
    name = config.secret_env
    if name and not name.startswith("QUALITY_CONNECTOR_"):
        fail(422, "secret_reference", "连接凭据只能引用QUALITY_CONNECTOR_前缀的服务端环境变量")
    return os.environ.get(name, "") if name else ""


def mapped(record, config):
    result = {}
    for target, source in config.fields.items():
        current = record
        for key in source.split("."):
            if not isinstance(current, dict) or key not in current:
                raise ValueError(f"missing_field:{source}")
            current = current[key]
        result[target] = current
    return result or record


def normalize(record, config):
    data = mapped(record, config)
    required = {"record_id", "equipment_id", "metric", "value", "unit", "occurred_at", "value_type"}
    if not required <= data.keys():
        raise ValueError("missing_fields:" + ",".join(sorted(required - data.keys())))
    when = datetime.fromisoformat(str(data["occurred_at"]).replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("timestamp_requires_timezone")
    if when > datetime.now(UTC) + timedelta(seconds=config.max_future_seconds):
        raise ValueError('source_clock_ahead')
    if isinstance(data['value'], bool):
        raise ValueError('boolean_is_not_measurement')
    value, unit = float(data["value"]), data["unit"]
    if unit == "kPa":
        value, unit = value * 1000, "Pa"
    elif unit == "mbar":
        value, unit = value * 100, "Pa"
    elif unit == "K":
        value, unit = value - 273.15, "degC"
    expected=config.units.get(data['metric'])
    if expected and unit != {'kPa':'Pa','mbar':'Pa','K':'degC'}.get(expected,expected):
        raise ValueError('unit_mismatch')
    if data["value_type"] not in ("setpoint", "measured"):
        raise ValueError("unknown_value_type")
    import math

    if not math.isfinite(value):
        raise ValueError("non_finite_value")
    return {
        **data,
        "occurred_at": when.astimezone(UTC),
        "value": value,
        "unit": unit,
        "quality": data.get("quality", "Uncertain"),
        "original_value": data["value"],
        "original_unit": data["unit"],
        "original_timestamp": str(data["occurred_at"]),
        "source_session_id": str(data.get("source_session_id", "")),
        "mapping_version": config.mapping_version,
    }


def http_pull(config, settings):
    endpoint(config.endpoint, settings)
    headers = {"Authorization": f"Bearer {secret(config)}"} if config.secret_env else {}
    records, cursor, seen = [], config.cursor, set()
    with controlled_client(settings, config.endpoint, config.timeout_seconds) as client:
        for _ in range(config.max_pages):
            response = client.get(
                config.endpoint, headers=headers, params={"cursor": cursor, "limit": config.page_size}
            )
            response.raise_for_status()
            if len(response.content) > 5 * 1024**2:
                raise ValueError("response_too_large")
            body = response.json()
            if not isinstance(body.get("items"), list):
                raise ValueError("items_required")
            records.extend(body["items"])
            next_cursor = str(body.get("next_cursor") or "")
            if not next_cursor:
                return records, cursor
            if next_cursor in seen or next_cursor == cursor:
                raise ValueError("cursor_loop")
            seen.add(next_cursor)
            cursor = next_cursor
    return records, cursor


def mqtt_read(config, settings, with_spool=False):
    from urllib.parse import urlparse

    endpoint(config.endpoint, settings, ("mqtt", "mqtts"))
    parsed = urlparse(config.endpoint)
    if settings.mode == "production" and parsed.scheme != "mqtts":
        fail(422, "tls_required", "生产MQTT必须使用TLS")
    spool = (
        settings.data_dir
        / "adapter-spool"
        / digest([settings.node_id, config.endpoint, config.topic, config.client_id])[:24]
    )
    spool.mkdir(parents=True, exist_ok=True)

    def pending():
        records, paths = [], []
        files = [p for p in sorted(spool.glob("*.json")) if not p.with_suffix(".done").exists()][:500]
        for path in files:
            envelope = json.loads(path.read_text())
            try:
                records.append(json.loads(base64.b64decode(envelope["payload"])))
            except (ValueError, UnicodeError):
                records.append({"parse_error": "invalid_json"})
            paths.append(str(path))
        return {"records": records, "spool_paths": paths}

    backlog = pending()
    if backlog["records"]:
        return backlog if with_spool else backlog["records"]
    rows, ready = [], threading.Event()
    errors = []
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.client_id
        or "quality-" + digest([settings.node_id, config.endpoint, config.topic])[:20],
        clean_session=False,
        manual_ack=True,
    )
    if config.username:
        client.username_pw_set(config.username, secret(config))
    if parsed.scheme == "mqtts":
        client.tls_set_context(ssl.create_default_context())

    def on_connect(c, userdata, flags, reason, properties):
        if reason.is_failure:
            errors.append("authentication_or_connect_failed")
            ready.set()
        else:
            c.subscribe(config.topic, qos=1)

    def on_message(c, userdata, msg):
        try:
            if len(msg.payload) > 1024**2:
                raise ValueError("message_too_large")
            used = sum(p.stat().st_size for p in spool.rglob("*") if p.is_file())
            if used + len(msg.payload) > settings.cache_max_bytes:
                raise OSError("adapter_spool_full")
            path = spool / (digest(msg.topic.encode() + msg.payload) + ".json")
            if not path.exists():
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
                    json.dump(
                        {
                            "topic": msg.topic,
                            "payload": base64.b64encode(msg.payload).decode(),
                            "received_at": datetime.now(UTC).isoformat(),
                        },
                        out,
                    )
                    out.flush()
                    os.fsync(out.fileno())
            # Transfer ownership only after raw data has reached durable local storage.
            if msg.qos:
                c.ack(msg.mid, msg.qos)
            rows.append(path)
        except Exception as exc:
            errors.append(type(exc).__name__)
        ready.set()

    client.on_connect, client.on_message = on_connect, on_message
    client.reconnect_delay_set(1, 30)
    from .sdk_control import pin_mqtt

    pin_mqtt(client, settings, config.endpoint)
    client.connect(parsed.hostname, parsed.port or (8883 if parsed.scheme == "mqtts" else 1883), 30)
    client.loop_start()
    try:
        if not ready.wait(config.timeout_seconds):
            raise TimeoutError("mqtt_no_data")
        if errors:
            raise ValueError(errors[0])
        result = pending()
        return result if with_spool else result["records"]
    finally:
        client.disconnect()
        client.loop_stop()


async def opc_read(config, settings):
    endpoint(config.endpoint, settings, ("opc.tcp",))
    client = Client(config.endpoint, timeout=config.timeout_seconds)
    from .sdk_control import pin_opc

    pin_opc(client, settings, config.endpoint)
    if config.username:
        client.set_user(config.username)
        client.set_password(secret(config))
    if config.security_policy:
        if not all((config.certificate, config.private_key, config.server_certificate)):
            raise ValueError("OPC UA安全策略需要客户端证书/私钥及可信服务端证书")
        # Only explicitly mounted certificate paths, never from model input.
        from pathlib import Path

        paths = [
            Path(p).resolve() for p in (config.certificate, config.private_key, config.server_certificate)
        ]
        if any(not p.is_relative_to((settings.data_dir / "certificates").resolve()) for p in paths):
            raise ValueError("certificate_path_outside_managed_directory")
        await client.set_security_string(f"Basic256Sha256,SignAndEncrypt,{paths[0]},{paths[1]},{paths[2]}")
    elif settings.mode == "production":
        raise ValueError("production_OPC_UA_requires_SignAndEncrypt")
    rows = []
    async with client:
        for node_id in config.nodes:
            node = client.get_node(node_id)
            value = await node.read_data_value(raise_on_bad_status=False)
            stamp = value.SourceTimestamp or value.ServerTimestamp
            rows.append(
                {
                    "node_id": node_id,
                    "value": value.Value.Value if value.Value else None,
                    "quality": "Good" if value.StatusCode.is_good() else str(value.StatusCode),
                    "status_code": value.StatusCode.value,
                    "occurred_at": stamp.isoformat() if stamp else None,
                    "server_timestamp": value.ServerTimestamp.isoformat() if value.ServerTimestamp else None,
                }
            )
    return rows


def read_adapter(protocol, config, settings):
    parsed = AdapterConfig.model_validate(config)
    if parsed.mode != "read":
        fail(422, "read_only", "适配器仅允许只读模式")
    if protocol == "http":
        records, cursor = http_pull(parsed, settings)
        return {"records": records, "cursor": cursor}
    if protocol == "mqtt":
        return mqtt_read(parsed, settings, with_spool=True)
    if protocol == "opcua":
        return {"records": asyncio.run(opc_read(parsed, settings))}
    fail(422, "protocol_unsupported", "未支持该协议")
