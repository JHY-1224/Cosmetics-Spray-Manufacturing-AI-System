"""Pinned HTTP transport: validate DNS, connect to a checked numeric sockaddr, preserve TLS host checks."""

import ipaddress
import socket
import ssl
import time
from urllib.parse import urlparse
import httpcore
import httpx
from httpcore._backends.sync import SyncBackend, SyncStream
from .security import endpoint


def address(value):
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    return ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip


def resolved(host, port):
    rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not rows or len(rows) > 32:
        raise httpcore.ConnectError("controlled DNS resolution unavailable")
    for _, _, _, _, sockaddr in rows:
        ip = address(sockaddr[0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise httpcore.ConnectError("controlled DNS address denied")
    return rows


class PinnedBackend(SyncBackend):
    def __init__(self, host, port, rows):
        self.host, self.port = host, port
        self.pins = {(family, str(address(sockaddr[0])), sockaddr[1]) for family, _, _, _, sockaddr in rows}

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return SyncStream(self.connect_socket(host, port, timeout, local_address, socket_options))

    def checked_rows(self, host, port, local_address=None):
        if host != self.host or port != self.port or local_address:
            raise httpcore.ConnectError("connection target differs from the approved endpoint")
        rows = resolved(host, port)
        if any(
            (family, str(address(sockaddr[0])), sockaddr[1]) not in self.pins
            for family, _, _, _, sockaddr in rows
        ):
            raise httpcore.ConnectError("DNS changed after endpoint approval; revalidate configuration")
        return rows

    def check_peer(self, sock, family):
        peer = sock.getpeername()
        if (family, str(address(peer[0])), peer[1]) not in self.pins:
            raise OSError("connected peer differs from approved address")

    def connect_socket(self, host, port, timeout=None, local_address=None, socket_options=None):
        rows = self.checked_rows(host, port, local_address)
        deadline = time.monotonic() + (timeout or 30)
        last = None
        for family, socktype, proto, _, sockaddr in rows:
            sock = socket.socket(family, socktype, proto)
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout()
                sock.settimeout(remaining)
                for option in socket_options or []:
                    sock.setsockopt(*option)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # No DNS is performed here: sockaddr is a numeric address from the checked resolution.
                sock.connect(sockaddr)
                self.check_peer(sock, family)
                return sock
            except OSError as exc:
                last = exc
                sock.close()
        if isinstance(last, socket.timeout):
            raise httpcore.ConnectTimeout("controlled connection timed out") from None
        raise httpcore.ConnectError("controlled endpoint unavailable") from None


def client(settings, url, timeout=15):
    endpoint(url, settings)
    parsed = urlparse(url)
    host = parsed.hostname.encode("idna").decode("ascii")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    backend = PinnedBackend(host, port, resolved(host, port))
    transport = httpx.HTTPTransport(trust_env=False, retries=0)
    # The project's locked httpx 0.28/httpcore 1.0 adapter owns this pool. Host/SNI still use the URL host.
    transport._pool.close()
    transport._pool = httpcore.ConnectionPool(
        ssl_context=ssl.create_default_context(),
        max_connections=8,
        max_keepalive_connections=4,
        keepalive_expiry=10,
        retries=0,
        network_backend=backend,
    )
    return httpx.Client(timeout=timeout, transport=transport, trust_env=False, follow_redirects=False)
