"""Environment routing for one portable collector executable."""

from __future__ import annotations

import re
from typing import Dict
from urllib.parse import urlparse


COLLECTOR_URLS = {
    "staging": "https://ltm-web-staging.onrender.com",
    "production": "https://ltm-web-gt13.onrender.com",
}
ROUTE_PREFIXES = {"S": "staging", "P": "production"}
PAIRING_CODE_RE = re.compile(r"^LTM1-(?P<route>[SP])-(?P<secret>[A-Za-z0-9_-]{12,})$")


def resolve_collector_route(pairing_code: str) -> Dict[str, str]:
    """Resolve only the internal endpoint hint carried by a pairing code.

    The route marker is deliberately not an authorization signal.  The
    selected server still verifies the complete one-time code, account and
    expiry before issuing a device token.
    """

    match = PAIRING_CODE_RE.fullmatch(str(pairing_code or "").strip())
    if not match:
        raise ValueError("设备连接码格式无效")
    environment = ROUTE_PREFIXES[match.group("route")]
    return {
        "environment": environment,
        "base_url": COLLECTOR_URLS[environment],
    }


def environment_for_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    try:
        parsed = urlparse(text)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("采集器服务地址无效") from exc
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or (port is not None and host not in {"localhost", "127.0.0.1"}):
        raise ValueError("采集器服务地址不得包含用户信息或公网自定义端口")
    for environment, url in COLLECTOR_URLS.items():
        expected = urlparse(url)
        if parsed.scheme == expected.scheme and host == (expected.hostname or ""):
            return environment
    if parsed.scheme in {"http", "https"} and host in {"localhost", "127.0.0.1"}:
        return "staging"
    raise ValueError("采集器服务地址不在受支持的环境范围内")


def ensure_collector_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    environment_for_url(text)
    parsed = urlparse(text)
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("采集器服务地址不得包含路径或查询参数")
    if parsed.hostname not in {"localhost", "127.0.0.1"} and parsed.scheme != "https":
        raise ValueError("公网采集器服务地址必须使用 HTTPS")
    return text
