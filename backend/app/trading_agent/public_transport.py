"""Pinned, bounded transport for reading registered public sources."""
from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
import ipaddress
import math
import socket
import ssl
import time
from urllib.parse import urljoin, urlparse


MAX_BYTES = 1_000_000
MAX_REDIRECTS = 3
READ_TIMEOUT_SECONDS = 8
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class UnsafeSource(ValueError):
    """The source URL or resolved address is outside the public-read boundary."""


class PublicTransportError(RuntimeError):
    """The source could not be read within the bounded transport contract."""


@dataclass(frozen=True)
class PublicReadResponse:
    url: str
    status_code: int
    content_type: str
    body_bytes: bytes
    truncated: bool = False


def validate_url(url: str):
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise UnsafeSource("来源地址不安全")
    if any(ord(char) < 32 for char in url):
        raise UnsafeSource("来源地址不安全")
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise UnsafeSource("来源地址不安全") from None
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise UnsafeSource("来源地址不安全")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeSource("来源地址不安全")
    if any(char.isspace() for char in hostname):
        raise UnsafeSource("来源地址不安全")
    if port not in (None, 80, 443):
        raise UnsafeSource("来源端口不安全")
    return parsed


def _public_ip(value: str):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if not address.is_global:
        return None
    return address


def _resolve_public(parsed) -> tuple[str, int]:
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeSource("来源地址无法解析") from exc
    if not addresses:
        raise UnsafeSource("来源地址无法解析")
    selected = None
    for item in addresses:
        try:
            sockaddr = item[4]
            address = _public_ip(sockaddr[0])
        except (IndexError, TypeError):
            address = None
        if address is None:
            raise UnsafeSource("来源地址指向内网或保留地址")
        if selected is None:
            selected = (str(address), port)
    if selected is None:
        raise UnsafeSource("来源地址无法解析")
    return selected


class _PinnedHTTPConnection(HTTPConnection):
    def __init__(self, parsed, address, timeout):
        super().__init__(parsed.hostname, parsed.port or 80, timeout=timeout)
        self._pinned_address = address

    def connect(self):
        self.sock = socket.create_connection(self._pinned_address, self.timeout)


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, parsed, address, timeout, context):
        super().__init__(parsed.hostname, parsed.port or 443, timeout=timeout, context=context)
        self._pinned_address = address

    def connect(self):
        raw = socket.create_connection(self._pinned_address, self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _make_connection(parsed, address, timeout):
    if parsed.scheme == "https":
        return _PinnedHTTPSConnection(parsed, address, timeout, ssl.create_default_context())
    return _PinnedHTTPConnection(parsed, address, timeout)


def _remaining(deadline: float) -> float:
    try:
        value = float(deadline) - time.monotonic()
    except (TypeError, ValueError):
        raise ValueError("deadline must be an absolute monotonic timestamp") from None
    if not math.isfinite(value) or value <= 0:
        raise PublicTransportError("公开来源读取超时")
    return value


def _set_socket_timeout(connection, response, timeout):
    sockets = [getattr(connection, "sock", None)]
    raw = getattr(getattr(response, "fp", None), "raw", None)
    sockets.append(getattr(raw, "_sock", None))
    seen = set()
    for sock in sockets:
        if sock is None or id(sock) in seen or not hasattr(sock, "settimeout"):
            continue
        seen.add(id(sock))
        sock.settimeout(timeout)


def _header(response, name: str, default=None):
    getter = getattr(response, "getheader", None)
    if callable(getter):
        value = getter(name, None)
        if value is not None:
            return value
    headers = getattr(response, "headers", None)
    if headers is not None:
        if hasattr(headers, "get_content_type") and name.lower() == "content-type":
            value = headers.get("Content-Type")
        elif hasattr(headers, "get"):
            value = headers.get(name)
            if value is None:
                value = headers.get(name.lower())
        else:
            value = None
        if value is not None:
            return value
    return default


def _request_target(parsed) -> str:
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return target


def _read_body(connection, response, deadline: float, max_bytes: int) -> tuple[bytes, bool]:
    body = bytearray()
    while len(body) <= max_bytes:
        timeout = min(READ_TIMEOUT_SECONDS, _remaining(deadline))
        _set_socket_timeout(connection, response, timeout)
        amount = min(64 * 1024, max_bytes + 1 - len(body))
        try:
            chunk = response.read(amount)
        except (socket.timeout, TimeoutError, OSError) as exc:
            raise PublicTransportError("公开来源读取超时或连接中断") from exc
        if not chunk:
            return bytes(body), False
        try:
            body.extend(bytes(chunk))
        except (TypeError, ValueError) as exc:
            raise PublicTransportError("公开来源返回格式无效") from exc
        if len(body) > max_bytes:
            return bytes(body[:max_bytes]), True
    return bytes(body[:max_bytes]), len(body) > max_bytes


def safe_read_public(
    url: str,
    *,
    deadline: float,
    max_bytes: int = MAX_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> PublicReadResponse:
    """Read one public URL through a validated IP-pinned, bounded connection."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if type(max_redirects) is not int or max_redirects < 0:
        raise ValueError("max_redirects must be non-negative")
    current_url = url
    for redirect_count in range(max_redirects + 1):
        parsed = validate_url(current_url)
        address = _resolve_public(parsed)
        timeout = min(READ_TIMEOUT_SECONDS, _remaining(deadline))
        connection = _make_connection(parsed, address, timeout)
        response = None
        try:
            connection.request(
                "GET",
                _request_target(parsed),
                headers={
                    "Accept": "text/html,text/plain,application/xhtml+xml",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "Host": parsed.hostname,
                },
            )
            _remaining(deadline)
            response = connection.getresponse()
            _remaining(deadline)
            status = int(response.status)
            if status in REDIRECT_STATUSES:
                location = _header(response, "Location")
                if not location:
                    raise PublicTransportError("公开来源重定向缺少目标")
                if redirect_count >= max_redirects:
                    raise PublicTransportError("公开来源重定向次数超限")
                current_url = urljoin(current_url, str(location))
                continue
            content_type = str(_header(response, "Content-Type", "") or "").lower()
            content_encoding = str(_header(response, "Content-Encoding", "") or "").lower()
            if any(item.strip() not in {"", "identity"} for item in content_encoding.split(",")):
                return PublicReadResponse(
                    url=current_url,
                    status_code=status,
                    content_type="application/octet-stream",
                    body_bytes=b"",
                    truncated=False,
                )
            body, truncated = _read_body(connection, response, deadline, max_bytes)
            return PublicReadResponse(
                url=current_url,
                status_code=status,
                content_type=content_type,
                body_bytes=body,
                truncated=truncated,
            )
        except UnsafeSource:
            raise
        except PublicTransportError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise PublicTransportError("公开来源暂时不可读取") from exc
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()
            if hasattr(connection, "close"):
                connection.close()
    raise PublicTransportError("公开来源重定向次数超限")


__all__ = [
    "MAX_BYTES",
    "MAX_REDIRECTS",
    "PublicReadResponse",
    "PublicTransportError",
    "UnsafeSource",
    "safe_read_public",
    "validate_url",
]
