import socket
import time

import pytest

from app.trading_agent import public_transport


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=()):
        self.status = status
        self.headers = headers or {"Content-Type": "text/plain"}
        self.chunks = list(chunks)
        self.read_calls = 0

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, amount):
        self.read_calls += 1
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        return None


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.sock = FakeSocket()
        self.requests = []
        self.closed = False

    def request(self, method, path, headers=None):
        self.requests.append((method, path, headers or {}))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def public_dns(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def test_safe_reader_pins_resolved_ip_and_preserves_tls_hostname(monkeypatch):
    seen = {}
    response = FakeResponse(chunks=[b"public"])
    connection = FakeConnection(response)

    monkeypatch.setattr(socket, "getaddrinfo", public_dns)

    def factory(parsed, address, timeout):
        seen.update({"hostname": parsed.hostname, "address": address, "timeout": timeout})
        return connection

    monkeypatch.setattr(public_transport, "_make_connection", factory)
    result = public_transport.safe_read_public(
        "https://example.com/story?x=1",
        deadline=time.monotonic() + 5,
    )

    assert result.body_bytes == b"public"
    assert seen["hostname"] == "example.com"
    assert seen["address"] == ("93.184.216.34", 443)
    assert connection.requests[0][0:2] == ("GET", "/story?x=1")
    assert connection.requests[0][2]["Host"] == "example.com"
    assert connection.requests[0][2]["Accept-Encoding"] == "identity"


def test_https_connector_uses_pinned_ip_and_original_hostname(monkeypatch):
    seen = {}
    raw_socket = FakeSocket()

    class Context:
        def wrap_socket(self, raw, server_hostname):
            seen.update({"raw": raw, "server_hostname": server_hostname})
            return raw

    monkeypatch.setattr(public_transport.socket, "create_connection",
                        lambda address, timeout: seen.update({"address": address, "timeout": timeout}) or raw_socket)
    parsed = public_transport.validate_url("https://Example.com/story")
    connection = public_transport._PinnedHTTPSConnection(parsed, ("93.184.216.34", 443), 2, Context())

    connection.connect()

    assert seen["address"] == ("93.184.216.34", 443)
    assert seen["server_hostname"] == "example.com"
    assert seen["raw"] is raw_socket


def test_private_redirect_is_rejected_before_second_connection(monkeypatch):
    calls = []

    def resolver(host, port, **kwargs):
        calls.append(host)
        if host == "public.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    first = FakeResponse(status=302, headers={"Location": "https://private.example/next"})
    connection = FakeConnection(first)
    connections = []
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setattr(public_transport, "_make_connection", lambda *args: connections.append(connection) or connection)

    with pytest.raises(public_transport.UnsafeSource):
        public_transport.safe_read_public("https://public.example/start", deadline=time.monotonic() + 5)

    assert calls == ["public.example", "private.example"]
    assert len(connections) == 1


def test_safe_reader_streams_and_truncates_at_one_megabyte(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    response = FakeResponse(chunks=[b"a" * 600_000, b"b" * 600_000, b"c"])
    connection = FakeConnection(response)
    monkeypatch.setattr(public_transport, "_make_connection", lambda *args: connection)

    result = public_transport.safe_read_public(
        "http://example.com/large",
        deadline=time.monotonic() + 5,
    )

    assert result.truncated is True
    assert len(result.body_bytes) == 1_000_000
    assert response.read_calls == 2


def test_safe_reader_does_not_decode_compressed_body(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    response = FakeResponse(headers={"Content-Type": "text/plain", "Content-Encoding": "gzip"}, chunks=[b"compressed"])
    connection = FakeConnection(response)
    monkeypatch.setattr(public_transport, "_make_connection", lambda *args: connection)

    result = public_transport.safe_read_public("https://example.com/compressed", deadline=time.monotonic() + 5)

    assert result.content_type == "application/octet-stream"
    assert result.body_bytes == b""
    assert response.read_calls == 0


def test_safe_reader_translates_stream_timeout_to_transport_error(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)

    class SlowResponse(FakeResponse):
        def read(self, amount):
            raise socket.timeout()

    response = SlowResponse()
    connection = FakeConnection(response)
    monkeypatch.setattr(public_transport, "_make_connection", lambda *args: connection)

    with pytest.raises(public_transport.PublicTransportError):
        public_transport.safe_read_public("https://example.com/slow", deadline=time.monotonic() + 1)
