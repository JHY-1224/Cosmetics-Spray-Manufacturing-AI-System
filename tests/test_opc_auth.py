import pytest
from asyncua import Server, ua
from asyncua.server.user_managers import User, UserRole
from quality.adapters import AdapterConfig, opc_read
from quality.config import Settings
from .test_protocols import free_port


@pytest.mark.asyncio
async def test_opcua_real_username_auth_rejects_bad_password(monkeypatch):
    class Accounts:
        def get_user(self, iserver, username=None, password=None, certificate=None):
            return (
                User(role=UserRole.User, name=username)
                if username == "reader" and password == "reference-only-credential"
                else None
            )

    server = Server(user_manager=Accounts())
    await server.init()
    port = free_port()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{port}/authenticated")
    server.set_identity_tokens([ua.UserNameIdentityToken])
    idx = await server.register_namespace("urn:quality:authenticated-reference")
    node = await server.nodes.objects.add_variable(idx, "ReferenceReading", 3.5)
    config = AdapterConfig(
        endpoint=f"opc.tcp://127.0.0.1:{port}/authenticated",
        username="reader",
        secret_env="QUALITY_CONNECTOR_OPC_TEST",
        nodes=[node.nodeid.to_string()],
    )
    async with server:
        monkeypatch.setenv("QUALITY_CONNECTOR_OPC_TEST", "reference-only-credential")
        rows = await opc_read(config, Settings())
        assert rows[0]["value"] == 3.5 and rows[0]["quality"] == "Good"
        monkeypatch.setenv("QUALITY_CONNECTOR_OPC_TEST", "rejected-credential")
        with pytest.raises(Exception):
            await opc_read(config, Settings())
