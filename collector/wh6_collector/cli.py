"""Command-line/service entry point used by the future Windows bundle."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Dict, Optional, Sequence
from urllib.parse import urlparse

import requests

from .account import strong_binding
from .discovery import discover_wh6_sources, validate_source
from .local_store import LocalOutbox
from .models import AccountIdentity
from .monitor import scan_source
from .uploader import StagingUploader


CLIENT_VERSION = "0.1.0"
DEFAULT_STAGING_URL = "https://ltm-web-staging.onrender.com"


def default_data_dir() -> Path:
    root = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "WH6成交采集器"


def ensure_staging_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").lower()
    allowed = {
        "ltm-web-staging.onrender.com",
        "localhost",
        "127.0.0.1",
    }
    if parsed.scheme not in {"https", "http"} or host not in allowed:
        raise ValueError("测试版采集器只允许连接 Supabase Staging 对应的测试 Web 地址")
    if host == "ltm-web-staging.onrender.com" and parsed.scheme != "https":
        raise ValueError("Staging 公网地址必须使用 HTTPS")
    return value.rstrip("/")


@dataclass
class CollectorConfig:
    staging_url: str
    source_path: str
    account: AccountIdentity
    device_token: str
    data_dir: str
    poll_seconds: int = 10
    client_version: str = CLIENT_VERSION

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        account_payload = self.account.to_payload()
        # Stable account IDs are not needed after binding and must not be copied to config.
        account_payload["stable_id"] = None
        payload = {
            "staging_url": ensure_staging_url(self.staging_url),
            "source_path": self.source_path,
            "account": account_payload,
            "device_token": self.device_token,
            "data_dir": self.data_dir,
            "poll_seconds": max(10, int(self.poll_seconds)),
            "client_version": self.client_version,
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    @classmethod
    def load(cls, path: Path) -> "CollectorConfig":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        account = AccountIdentity(**payload["account"])
        return cls(
            staging_url=ensure_staging_url(payload["staging_url"]),
            source_path=payload["source_path"],
            account=account,
            device_token=payload["device_token"],
            data_dir=payload["data_dir"],
            poll_seconds=max(10, int(payload.get("poll_seconds", 10))),
            client_version=payload.get("client_version", CLIENT_VERSION),
        )


def run_once(
    config: CollectorConfig,
    *,
    upload: Optional[Callable[[str, Sequence[Dict[str, Any]]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    ensure_staging_url(config.staging_url)
    outbox = LocalOutbox(Path(config.data_dir) / "collector.sqlite3")
    if not config.account.confirmed or config.account.requires_manual_confirmation:
        return {"state": "account_pending", "queued": outbox.status()["pending"], "accepted": 0}
    try:
        source = validate_source(Path(config.source_path))
    except (OSError, ValueError) as exc:
        return {"state": "path_unavailable", "message": str(exc), "queued": outbox.status()["pending"], "accepted": 0}
    checkpoint = outbox.load_checkpoint(str(source.path))
    batch = scan_source(source, checkpoint, account=config.account)
    for fill in batch.fills:
        outbox.put(fill)
    for issue in batch.issues:
        outbox.add_issue(issue)
    outbox.save_checkpoint(str(source.path), batch.checkpoint)
    claimed = outbox.claim(500)
    if not claimed:
        return {"state": "normal", "queued": outbox.status()["pending"], "accepted": 0, "issues": len(batch.issues)}
    sender = upload or StagingUploader(config.staging_url, config.device_token)
    event_keys = [str(item["event_key"]) for item in claimed]
    payloads = [json.loads(str(item["payload_json"])) for item in claimed]
    try:
        result = sender(config.device_token, payloads)
    except Exception as exc:
        outbox.release(event_keys, str(exc))
        return {"state": "offline_queue", "message": str(exc), "queued": outbox.status()["pending"], "accepted": 0}
    outbox.ack(event_keys)
    return {
        "state": "normal",
        "queued": outbox.status()["pending"],
        "accepted": int(result.get("accepted", 0)),
        "duplicates": int(result.get("duplicates", 0)),
        "conflicts": int(result.get("conflicts", 0)),
        "issues": len(batch.issues),
    }


def run_service(config: CollectorConfig, stop_event=None) -> None:
    while stop_event is None or not stop_event.is_set():
        run_once(config)
        if stop_event is None:
            return
        stop_event.wait(max(10, int(config.poll_seconds)))


def activate_remote_device(staging_url: str, pairing_code: str, device_name: str, fingerprint: str, client_version: str = CLIENT_VERSION) -> Dict[str, Any]:
    """Consume a one-time Web pairing code and return its token once."""
    response = requests.post(
        ensure_staging_url(staging_url) + "/api/trading-collector/device/activate",
        json={
            "pairing_code": pairing_code,
            "device_name": device_name,
            "client_version": client_version,
            "fingerprint": fingerprint,
        },
        timeout=8,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", {})
        except ValueError:
            detail = {}
        raise ValueError(detail.get("message") if isinstance(detail, dict) else str(detail) or "设备连接失败")
    return response.json()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WH6 期权成交只读采集器")
    parser.add_argument("--config", type=Path, default=default_data_dir() / "config.json")
    parser.add_argument("--configure", action="store_true", help="保存手动选择的 WH6 成交文件路径")
    parser.add_argument("--source", type=Path, help="WH6 match.dat 或包含它的 Record 目录")
    parser.add_argument("--staging-url", default=DEFAULT_STAGING_URL)
    parser.add_argument("--pairing-code", default="", help="Web 管理页生成的一次性设备连接码")
    parser.add_argument("--device-name", default="Windows WH6 采集器")
    parser.add_argument("--fingerprint", default="", help="本机设备指纹；不填写时使用本机机器标识摘要")
    parser.add_argument("--once", action="store_true", help="执行一次只读扫描和上传")
    parser.add_argument("--service", action="store_true", help="每 10 秒持续扫描")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.configure:
            if not args.source:
                raise ValueError("--configure 需要 --source 指定 WH6 成交文件或目录")
            source = validate_source(args.source)
            config = CollectorConfig(
                staging_url=ensure_staging_url(args.staging_url),
                source_path=str(source.path),
                account=strong_binding("宏源期货账户", "pending", "宏源期货账户待确认"),
                device_token="",
                data_dir=str(args.config.parent),
            )
            if args.pairing_code:
                fingerprint = args.fingerprint or "local-device"
                activated = activate_remote_device(config.staging_url, args.pairing_code, args.device_name, fingerprint, config.client_version)
                config.device_token = activated["token"]
                config.account = AccountIdentity(
                    account_code="hongyuan_futures",
                    display_name="宏源期货账户",
                    masked_label=activated.get("account_label") or "宏源期货账户（已绑定）",
                    fingerprint="server-bound:%s" % activated.get("account_id"),
                    binding_mode="strong",
                    confirmed=True,
                )
            else:
                # The user must finish the pairing step before --once/--service can read.
                config.account = AccountIdentity(**{**config.account.to_payload(), "confirmed": False, "requires_manual_confirmation": True})
            config.save(args.config)
            return 0
        config = CollectorConfig.load(args.config)
        result = run_once(config)
        print(json.dumps(result, ensure_ascii=False))
        if args.service:
            run_service(config)
        return 0 if result["state"] in {"normal", "offline_queue", "account_pending", "path_unavailable"} else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"state": "configuration_error", "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
