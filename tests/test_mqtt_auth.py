import asyncio

import pytest
from amqtt.broker import Broker

from quality.adapters import AdapterConfig, mqtt_read
from quality.config import Settings
from quality.security import PASSWORDS
from .test_protocols import free_port


@pytest.mark.asyncio
async def test_actual_mqtt_rejects_bad_authentication(tmp_path, monkeypatch):
    password_file = tmp_path / "broker-passwords"
    password_file.write_text("reader:" + PASSWORDS.hash("Authorized-Reference-Password!") + "\n")
    port = free_port()
    broker = Broker(
        {
            "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
            "plugins": {"amqtt.plugins.authentication.FileAuthPlugin": {"password_file": str(password_file)}},
        }
    )
    await broker.start()
    monkeypatch.setenv("QUALITY_CONNECTOR_BAD_AUTH", "wrong-reference-password")
    config = AdapterConfig(
        endpoint=f"mqtt://127.0.0.1:{port}",
        username="reader",
        secret_env="QUALITY_CONNECTOR_BAD_AUTH",
        timeout_seconds=2,
    )
    try:
        with pytest.raises((ValueError, TimeoutError)):
            await asyncio.to_thread(mqtt_read, config, Settings(_env_file=None, data_dir=tmp_path))
    finally:
        await broker.shutdown()
