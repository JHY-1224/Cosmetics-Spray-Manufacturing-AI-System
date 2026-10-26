import socket
import httpcore
import pytest
from quality.http_control import PinnedBackend, address


def test_dns_rebinding_is_rejected_before_a_socket_is_opened(monkeypatch):
    old = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8123))]
    backend = PinnedBackend("approved.internal", 8123, old)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 8123))],
    )
    monkeypatch.setattr(
        socket, "socket", lambda *a, **kw: pytest.fail("must not open a socket after DNS changed")
    )
    with pytest.raises(httpcore.ConnectError, match="DNS changed"):
        backend.connect_tcp("approved.internal", 8123, timeout=1)
    with pytest.raises(httpcore.ConnectError, match="target differs"):
        backend.connect_tcp("other.internal", 8123, timeout=1)


def test_ipv4_mapped_link_local_is_not_a_bypass(monkeypatch):
    from quality.http_control import resolved

    assert str(address("::ffff:169.254.169.254")) == "169.254.169.254"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:169.254.169.254", 80, 0, 0))],
    )
    with pytest.raises(httpcore.ConnectError, match="denied"):
        resolved("explicit.internal", 80)
