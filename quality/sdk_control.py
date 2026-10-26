"""Instance-local socket pinning for the repository's locked protocol SDK versions."""

import asyncio
import socket
from types import MethodType
from urllib.parse import urlparse

from .http_control import PinnedBackend, resolved
from .security import endpoint


def pins(settings, url, schemes, default_port):
    endpoint(url, settings, schemes)
    parsed = urlparse(url)
    host = parsed.hostname.encode("idna").decode("ascii")
    port = parsed.port or default_port
    return PinnedBackend(host, port, resolved(host, port))


def controlled_pools(manager, guard):
    """Retain each SDK's TLS/SNI/certificate and HTTP framing; replace only TCP creation."""
    classes = {}
    for scheme, pool in manager.pool_classes_by_scheme.items():
        def connect(connection):
            return guard.connect_socket(connection.host, connection.port, connection.timeout,
                                        connection.source_address, connection.socket_options)

        connection = type("Controlled" + pool.ConnectionCls.__name__, (pool.ConnectionCls,), {"_new_conn": connect})
        classes[scheme] = type("Controlled" + pool.__name__, (pool,), {"ConnectionCls": connection})
    # Never modify urllib3's shared module-level registry or another client's routing.
    manager.pool_classes_by_scheme = classes


def pin_s3(client, settings):
    parsed = urlparse(settings.s3_endpoint)
    guard = pins(settings, settings.s3_endpoint, ("http", "https"), 443 if parsed.scheme == "https" else 80)
    session = client._endpoint.http_session
    controlled_pools(session._manager, guard)


def pin_mqtt(client, settings, url):
    guard = pins(settings, url, ("mqtt", "mqtts"), 8883 if urlparse(url).scheme == "mqtts" else 1883)

    def connect(self):
        if self._bind_address or self._bind_port:
            raise ValueError("controlled MQTT requires the configured direct connection")
        return guard.connect_socket(self._host, self._port, self._connect_timeout)

    client._create_socket_connection = MethodType(connect, client)


def pin_opc(client, settings, url):
    from asyncua.client.ua_client import UaClientState

    guard = pins(settings, url, ("opc.tcp",), 4840)

    async def connect(self, host, port):
        self._set_state(UaClientState.CONNECTING)
        try:
            async with asyncio.timeout(self._timeout):
                last = None
                for family, kind, protocol, _, sockaddr in guard.checked_rows(host, port):
                    sock = socket.socket(family, kind, protocol)
                    try:
                        sock.setblocking(False)
                        loop = asyncio.get_running_loop()
                        await loop.sock_connect(sock, sockaddr)
                        guard.check_peer(sock, family)
                        await loop.create_connection(self._make_protocol, sock=sock)
                        self._set_state(UaClientState.SOCKET_OPEN)
                        return
                    except OSError as exc:
                        last = exc
                        sock.close()
                    except BaseException:
                        sock.close()
                        raise
                raise OSError("controlled OPC endpoint unavailable") from last
        except BaseException:
            self._set_state(UaClientState.DISCONNECTED)
            raise

    client.uaclient.connect_socket = MethodType(connect, client.uaclient)


def telemetry_session(settings):
    import requests
    from requests.adapters import HTTPAdapter

    url = settings.otlp_endpoint
    guard = pins(settings, url, ("http", "https"), 443 if urlparse(url).scheme == "https" else 80)

    class DirectSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs["allow_redirects"] = False
            return super().request(*args, **kwargs)

    session = DirectSession()
    session.trust_env = False
    for scheme in ("http://", "https://"):
        adapter = HTTPAdapter(max_retries=0)
        controlled_pools(adapter.poolmanager, guard)
        session.mount(scheme, adapter)
    return session
