from __future__ import annotations

import socket
from typing import Any


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _host_from_address(address: Any) -> str | None:
    if isinstance(address, tuple) and address:
        return str(address[0])
    if isinstance(address, str):
        return address
    return None


def install_network_sentinel(monkeypatch) -> None:
    original_create_connection = socket.create_connection
    original_socket_connect = socket.socket.connect

    def guarded_create_connection(address, *args, **kwargs):
        host = _host_from_address(address)
        if host in LOCAL_HOSTS:
            return original_create_connection(address, *args, **kwargs)
        raise AssertionError(f"Pre-M7 network sentinel blocked outbound create_connection: {address}")

    def guarded_socket_connect(self, address):
        host = _host_from_address(address)
        if host in LOCAL_HOSTS or host is None:
            return original_socket_connect(self, address)
        raise AssertionError(f"Pre-M7 network sentinel blocked outbound socket connect: {address}")

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_socket_connect)

    try:
        import requests

        def fail_requests(*args, **kwargs):
            raise AssertionError("Pre-M7 network sentinel blocked requests outbound HTTP")

        monkeypatch.setattr(requests.sessions.Session, "request", fail_requests)
    except ModuleNotFoundError:
        pass

    try:
        import urllib.request

        def fail_urlopen(*args, **kwargs):
            raise AssertionError("Pre-M7 network sentinel blocked urllib outbound HTTP")

        monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    except Exception:
        pass

    try:
        import httpx

        def fail_httpx_transport(*args, **kwargs):
            raise AssertionError("Pre-M7 network sentinel blocked httpx outbound HTTP")

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fail_httpx_transport)
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fail_httpx_transport)
    except Exception:
        pass


def assert_network_sentinel_blocks(monkeypatch) -> None:
    install_network_sentinel(monkeypatch)
    try:
        socket.create_connection(("example.com", 80), timeout=0.01)
    except AssertionError as exc:
        assert "network sentinel" in str(exc)
        return
    raise AssertionError("network sentinel did not block outbound network")
