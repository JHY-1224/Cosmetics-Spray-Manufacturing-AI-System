import asyncio
import json
import socket
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn
from asyncua import Server
from fastapi import FastAPI, Header, HTTPException

from quality.adapters import AdapterConfig, http_pull, mqtt_read, normalize, opc_read
from quality.config import Settings


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_reference(monkeypatch):
    app = FastAPI()

    @app.get("/records")
    def records(cursor: str = "", limit: int = 100, authorization: str = Header(default="")):
        if authorization != "Bearer reference-test-secret":
            raise HTTPException(401)
        i = int(cursor or 0)
        return {
            "items": [
                {
                    "record_id": str(i),
                    "equipment_id": "equipment-1",
                    "metric": "pressure",
                    "value": 12,
                    "unit": "kPa",
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "value_type": "measured",
                }
            ],
            "next_cursor": str(i + 1) if i < 2 else None,
        }

    @app.get("/slow")
    def slow():
        time.sleep(1.5)
        return {"items": []}

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    monkeypatch.setenv("QUALITY_CONNECTOR_REFERENCE", "reference-test-secret")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=3)


def test_actual_http_auth_pagination_units_and_timeout(http_reference):
    config = AdapterConfig(endpoint=http_reference + "/records", secret_env="QUALITY_CONNECTOR_REFERENCE")
    rows, cursor = http_pull(config, Settings())
    assert len(rows) == 3
    assert normalize(rows[0], config)["value"] == 12000
    assert normalize(rows[0], config)["unit"] == "Pa"
    with pytest.raises(httpx.HTTPStatusError):
        http_pull(AdapterConfig(endpoint=http_reference + "/records"), Settings())
    with pytest.raises(httpx.ReadTimeout):
        http_pull(AdapterConfig(endpoint=http_reference + "/slow", timeout_seconds=1), Settings())


@pytest.mark.asyncio
async def test_actual_opcua_read_quality_timestamps_and_bad_node():
    port = free_port()
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{port}/quality")
    idx = await server.register_namespace("urn:quality:reference")
    node = await server.nodes.objects.add_variable(idx, "ChamberPressure", 12.4)
    config = AdapterConfig(endpoint=f"opc.tcp://127.0.0.1:{port}/quality", nodes=[node.nodeid.to_string()])
    async with server:
        rows = await opc_read(config, Settings())
        assert rows[0]["value"] == 12.4 and rows[0]["quality"] == "Good" and rows[0]["occurred_at"]
        bad = await opc_read(config.model_copy(update={"nodes": ["ns=9;s=missing"]}), Settings())
        assert bad[0]["quality"] != "Good" and bad[0]["value"] is None
    with pytest.raises(Exception):
        await opc_read(config.model_copy(update={"timeout_seconds": 1}), Settings())


@pytest.mark.asyncio
async def test_actual_mqtt_subscription_and_reconnect(tmp_path):
    from amqtt.broker import Broker
    import paho.mqtt.client as mqtt

    port = free_port()
    broker = Broker(
        {
            "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
            "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
        }
    )
    await broker.start()
    config = AdapterConfig(endpoint=f"mqtt://127.0.0.1:{port}", topic="quality/test", timeout_seconds=3)

    def publish():
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect("127.0.0.1", port)
        client.loop_start()
        try:
            for _ in range(6):
                import uuid

                client.publish(
                    "quality/test",
                    json.dumps(
                        {
                            "record_id": str(uuid.uuid4()),
                            "equipment_id": "temp-sensor",
                            "occurred_at": datetime.now(UTC).isoformat(),
                            "metric": "temperature",
                            "value": 22,
                            "unit": "degC",
                            "value_type": "measured",
                            "quality": "Good",
                        }
                    ),
                    qos=1,
                ).wait_for_publish(timeout=2)
                time.sleep(0.15)
        finally:
            client.disconnect()
            client.loop_stop()

    try:
        for _ in range(2):
            configured = Settings(_env_file=None, data_dir=tmp_path)
            task = asyncio.create_task(asyncio.to_thread(mqtt_read, config, configured, True))
            await asyncio.sleep(0.15)
            pub = asyncio.create_task(asyncio.to_thread(publish))
            result = await task
            assert result["records"][0]["value"] == 22
            from pathlib import Path

            assert all(Path(p).exists() for p in result["spool_paths"])
            for p in result["spool_paths"]:
                Path(p).with_suffix(".done").touch()
            await pub
    finally:
        await broker.shutdown()


def test_reject_ssrf_code_injection_and_bad_timestamp():
    from quality.security import endpoint
    from quality.agent import validate_rules
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        endpoint("http://169.254.169.254/latest/meta-data", Settings())
    with pytest.raises(HTTPException):
        validate_rules({"python": '__import__("os").system("anything")'})
    with pytest.raises(ValueError):
        normalize(
            {
                "record_id": "x",
                "equipment_id": "x",
                "metric": "x",
                "value": 1,
                "unit": "Pa",
                "occurred_at": "2026-01-01",
                "value_type": "measured",
            },
            AdapterConfig(endpoint="http://localhost"),
        )
