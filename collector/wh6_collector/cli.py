"""Command-line/service entry point used by the future Windows bundle."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import inspect
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Dict, Optional, Sequence
from urllib.parse import urlparse

import requests

from .account import compare_binding, probe_source_account, strong_binding
from .credential_store import protect_token, unprotect_token
from .discovery import discover_wh6_sources, validate_sources
from .local_store import LocalOutbox
from .models import AccountIdentity
from .monitor import scan_source
from .parser import business_trading_day
from .setup_ui import run_first_setup
from .uploader import StagingUploader


CLIENT_VERSION = "0.1.0"
DEFAULT_STAGING_URL = "https://ltm-web-staging.onrender.com"
REALTIME_SCAN_SECONDS = 2
POSITION_SCAN_SECONDS = 5
HISTORY_SCAN_SECONDS = 10


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
    poll_seconds: int = REALTIME_SCAN_SECONDS
    client_version: str = CLIENT_VERSION
    allow_weak_source: bool = False
    source_account_fingerprint: Optional[str] = None

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
            "device_token": protect_token(self.device_token),
            "data_dir": self.data_dir,
            "poll_seconds": max(REALTIME_SCAN_SECONDS, int(self.poll_seconds)),
            "client_version": self.client_version,
            "allow_weak_source": bool(self.allow_weak_source),
            "source_account_fingerprint": self.source_account_fingerprint,
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
            device_token=unprotect_token(payload["device_token"]),
            data_dir=payload["data_dir"],
            poll_seconds=max(REALTIME_SCAN_SECONDS, int(payload.get("poll_seconds", REALTIME_SCAN_SECONDS))),
            client_version=payload.get("client_version", CLIENT_VERSION),
            allow_weak_source=bool(payload.get("allow_weak_source", False)),
            source_account_fingerprint=payload.get("source_account_fingerprint"),
        )


def run_once(
    config: CollectorConfig,
    *,
    upload: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    ensure_staging_url(config.staging_url)
    outbox = LocalOutbox(Path(config.data_dir) / "collector.sqlite3")
    if config.account.account_code != "hongyuan_futures" or not config.account.confirmed or config.account.requires_manual_confirmation:
        return {
            "state": "account_pending",
            "queued": outbox.status()["pending"],
            "accepted": 0,
            "positions_accepted": 0,
        }

    def send_payload(sender, fills, position_snapshots):
        if hasattr(sender, "send"):
            return sender.send(config.device_token, fills, position_snapshots)
        try:
            parameters = inspect.signature(sender).parameters.values()
            positional = [
                parameter
                for parameter in parameters
                if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
            ]
            accepts_varargs = any(parameter.kind == parameter.VAR_POSITIONAL for parameter in parameters)
        except (TypeError, ValueError):
            positional = []
            accepts_varargs = True
        if accepts_varargs or len(positional) >= 3:
            return sender(config.device_token, fills, position_snapshots)
        return sender(config.device_token, fills)

    def drain_pending(state: str, issue_count: int = 0, message: str = "") -> Dict[str, Any]:
        # A large historical backlog must never occupy the realtime upload slot.
        claimed = outbox.claim(500, priority="realtime")
        if not claimed:
            claimed = outbox.claim(500, priority="history")
        if not claimed:
            result = {
                "state": state,
                "queued": outbox.status()["pending"],
                "accepted": 0,
                "positions_accepted": 0,
                "issues": issue_count,
            }
            if message:
                result["message"] = message
            return result
        sender = upload or StagingUploader(config.staging_url, config.device_token)
        event_keys = [str(item["event_key"]) for item in claimed]
        fill_payloads = [json.loads(str(item["payload_json"])) for item in claimed if item.get("item_type", "fill") == "fill"]
        position_payloads = [json.loads(str(item["payload_json"])) for item in claimed if item.get("item_type") == "position_snapshot"]
        try:
            upload_result = send_payload(sender, fill_payloads, position_payloads)
        except Exception as exc:
            outbox.release(event_keys, str(exc))
            result = {
                "state": "offline_queue" if state == "normal" else state,
                "message": str(exc),
                "queued": outbox.status()["pending"],
                "accepted": 0,
                "positions_accepted": 0,
                "issues": issue_count,
            }
            return result
        outbox.ack(event_keys)
        result = {
            "state": state,
            "queued": outbox.status()["pending"],
            "accepted": int(upload_result.get("accepted", 0)),
            "positions_accepted": int(upload_result.get("positions_accepted", upload_result.get("position_accepted", 0))),
            "position_duplicates": int(upload_result.get("position_duplicates", 0)),
            "position_conflicts": int(upload_result.get("position_conflicts", 0)),
            "duplicates": int(upload_result.get("duplicates", 0)),
            "conflicts": int(upload_result.get("conflicts", 0)),
            "issues": issue_count,
        }
        if message:
            result["message"] = message
        return result

    try:
        sources = validate_sources(Path(config.source_path))
    except (OSError, ValueError) as exc:
        return {"state": "path_unavailable", "message": str(exc), "queued": outbox.status()["pending"], "accepted": 0}
    observed_accounts = [probe_source_account(source.path) for source in sources]
    for observed_account in observed_accounts:
        if config.source_account_fingerprint:
            if observed_account.fingerprint:
                binding_state = "match" if observed_account.fingerprint == config.source_account_fingerprint else "mismatch"
            else:
                # Once a strong local source identity was recorded, disappearance of
                # that identity is not silently downgraded to weak binding.
                binding_state = "unknown"
        else:
            binding_state = compare_binding(config.account, observed_account)
        if binding_state == "mismatch":
            return drain_pending("account_changed", message="检测到 WH6 来源账户变化，已暂停新数据")
        if binding_state == "unknown" and (config.source_account_fingerprint or not config.allow_weak_source):
            return drain_pending("account_pending", message="当前 WH6 版本未提供可验证账户标识，请人工确认后继续")

    issue_count = 0
    paused_state = "normal"
    today = business_trading_day(datetime.now().astimezone())
    for source in sources:
        checkpoint = outbox.load_checkpoint(str(source.path), kind=source.kind)
        batch = scan_source(source, checkpoint, account=config.account)
        source_date = str(source.trading_date or "").replace("-", "")
        is_realtime = source.kind == "position" or source_date == today.replace("-", "")
        priority = "realtime" if is_realtime else "history"
        for fill in batch.fills:
            outbox.put(fill, priority=priority)
        if batch.position_snapshot is not None:
            outbox.put_position(batch.position_snapshot, priority="realtime")
        for issue in batch.issues:
            outbox.add_issue(issue)
        outbox.save_checkpoint(str(source.path), batch.checkpoint, kind=batch.source_kind)
        issue_count += len(batch.issues)
        if any(issue.code == "unknown_format" for issue in batch.issues):
            paused_state = "format_unknown"
        elif paused_state == "normal" and any(issue.code == "path_unavailable" for issue in batch.issues):
            paused_state = "path_unavailable"
    return drain_pending(paused_state, issue_count=issue_count)


def run_service(config: CollectorConfig, stop_event=None) -> None:
    """Run the polling loop used by the Windows background service.

    A caller-provided event is used by tests and an embedding host.  The
    packaged executable has no event, so it must remain alive until Windows
    stops the process rather than silently doing one extra scan and exiting.
    """
    interval = max(REALTIME_SCAN_SECONDS, int(config.poll_seconds))
    while True:
        run_once(config)
        if stop_event is None:
            time.sleep(interval)
            continue
        if stop_event.wait(interval):
            return


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
    parser = argparse.ArgumentParser(description="WH6 期货与期权成交、持仓只读采集器")
    parser.add_argument("--config", type=Path, default=default_data_dir() / "config.json")
    parser.add_argument("--configure", action="store_true", help="保存手动选择的 WH6 成交与持仓缓存路径")
    parser.add_argument("--source", type=Path, help="WH6 match.dat/position.dat 或包含它们的 Record 目录")
    parser.add_argument("--staging-url", default=DEFAULT_STAGING_URL)
    parser.add_argument("--pairing-code", default="", help="Web 管理页生成的一次性设备连接码")
    parser.add_argument("--device-name", default="Windows WH6 采集器")
    parser.add_argument("--fingerprint", default="", help="本机设备指纹；不填写时使用本机机器标识摘要")
    parser.add_argument("--confirm-weak-source", action="store_true", help="明确确认无法读取稳定账户标识的来源")
    parser.add_argument("--once", action="store_true", help="执行一次只读扫描和上传")
    parser.add_argument("--service", action="store_true", help="每 2 秒检查实时成交，持仓内容每 5 秒变化检查")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.configure and not args.once and not args.service:
            if not args.config.exists():
                setup_result = run_first_setup(args.config)
                if setup_result != 0:
                    return setup_result
            config = CollectorConfig.load(args.config)
            run_service(config)
            return 0
        if args.configure:
            if not args.source and not args.pairing_code:
                return run_first_setup(args.config)
            if args.source:
                selected_path = args.source.expanduser()
                sources = validate_sources(selected_path)
            else:
                discovered = discover_wh6_sources()
                if not discovered:
                    raise ValueError("未自动找到 WH6 成交缓存，请使用 --source 手动选择 Record 目录")
                record_roots = {}
                for item in discovered:
                    record_parent = next((parent for parent in item.path.parents if parent.name.lower() == "record"), item.path.parent)
                    record_roots.setdefault(str(record_parent), record_parent)
                if len(record_roots) != 1:
                    raise ValueError("自动发现到多个 WH6 Record 目录，请使用 --source 明确选择目标账户目录")
                selected_path = next(iter(record_roots.values()))
                sources = validate_sources(selected_path)
            source = sources[0]
            config = CollectorConfig(
                staging_url=ensure_staging_url(args.staging_url),
                source_path=str(selected_path if selected_path.is_dir() else source.path),
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
                observed_account = probe_source_account(source.path)
                config.source_account_fingerprint = observed_account.fingerprint
                config.allow_weak_source = bool(args.confirm_weak_source)
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
        return 0 if result["state"] in {"normal", "offline_queue", "account_pending", "path_unavailable", "format_unknown"} else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"state": "configuration_error", "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
