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
from zoneinfo import ZoneInfo

import requests

from .account import compare_binding, probe_source_account, strong_binding
from .credential_store import protect_token, unprotect_token
from .discovery import discover_wh6_sources, validate_sources
from .local_store import LocalOutbox
from .models import AccountIdentity, ParseIssue
from .monitor import scan_source
from .parser import business_trading_day
from .policy import CollectionPolicy
from .routing import COLLECTOR_URLS, ensure_collector_url, environment_for_url, resolve_collector_route
from .setup_ui import run_first_setup
from .uploader import CollectorUploader, StagingUploader
from .version import CLIENT_VERSION, UPLOAD_BATCH_SIZE
DEFAULT_COLLECTOR_URL = COLLECTOR_URLS["staging"]
# Compatibility constant retained for local callers; the UI no longer exposes
# a user-selectable Staging address.
DEFAULT_STAGING_URL = DEFAULT_COLLECTOR_URL
REALTIME_SCAN_SECONDS = 2
POSITION_SCAN_SECONDS = 5
HISTORY_SCAN_SECONDS = 10


def default_data_dir() -> Path:
    root = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "WH6成交采集器"


def ensure_staging_url(value: str) -> str:
    """Compatibility guard for legacy callers that must remain Staging-only."""
    normalized = ensure_collector_url(value)
    if environment_for_url(normalized) != "staging":
        raise ValueError("旧版采集器只允许连接 Staging 服务")
    return normalized


@dataclass(init=False)
class CollectorConfig:
    collector_url: str
    source_path: str
    account: AccountIdentity
    device_token: str
    data_dir: str
    poll_seconds: int = REALTIME_SCAN_SECONDS
    client_version: str = CLIENT_VERSION
    allow_weak_source: bool = False
    source_account_fingerprint: Optional[str] = None
    environment: str = "staging"

    def __init__(
        self,
        collector_url: Optional[str] = None,
        source_path: str = "",
        account: Optional[AccountIdentity] = None,
        device_token: str = "",
        data_dir: str = "",
        poll_seconds: int = REALTIME_SCAN_SECONDS,
        client_version: str = CLIENT_VERSION,
        allow_weak_source: bool = False,
        source_account_fingerprint: Optional[str] = None,
        environment: Optional[str] = None,
        *,
        staging_url: Optional[str] = None,
    ) -> None:
        selected_url = collector_url or staging_url or ""
        if account is None:
            raise TypeError("采集器配置缺少账户身份")
        normalized_url = ensure_collector_url(selected_url)
        inferred_environment = environment_for_url(normalized_url)
        selected_environment = str(environment or inferred_environment).strip().lower()
        if selected_environment not in COLLECTOR_URLS and normalized_url not in {"http://localhost", "http://127.0.0.1", "https://localhost", "https://127.0.0.1"}:
            raise ValueError("采集器环境无效")
        if selected_environment != inferred_environment:
            raise ValueError("采集器环境与服务地址不一致")
        self.collector_url = normalized_url
        self.source_path = source_path
        self.account = account
        self.device_token = device_token
        self.data_dir = data_dir
        self.poll_seconds = poll_seconds
        self.client_version = client_version
        self.allow_weak_source = allow_weak_source
        self.source_account_fingerprint = source_account_fingerprint
        self.environment = selected_environment

    @property
    def staging_url(self) -> str:
        """Compatibility alias for pre-scheme-A local callers."""
        return self.collector_url

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        account_payload = self.account.to_payload()
        # Stable account IDs are not needed after binding and must not be copied to config.
        account_payload["stable_id"] = None
        payload = {
            "collector_url": ensure_collector_url(self.collector_url),
            "environment": self.environment,
            "source_path": self.source_path,
            "account": account_payload,
            "device_token": protect_token(self.device_token),
            "data_dir": self.data_dir,
            "poll_seconds": max(REALTIME_SCAN_SECONDS, int(self.poll_seconds)),
            "client_version": CLIENT_VERSION,
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
            collector_url=ensure_collector_url(payload.get("collector_url") or payload["staging_url"]),
            source_path=payload["source_path"],
            account=account,
            device_token=unprotect_token(payload["device_token"]),
            data_dir=payload["data_dir"],
            poll_seconds=max(REALTIME_SCAN_SECONDS, int(payload.get("poll_seconds", REALTIME_SCAN_SECONDS))),
            client_version=CLIENT_VERSION,
            allow_weak_source=bool(payload.get("allow_weak_source", False)),
            source_account_fingerprint=payload.get("source_account_fingerprint"),
            environment=payload.get("environment"),
        )


def run_once(
    config: CollectorConfig,
    *,
    upload: Optional[Callable[..., Dict[str, Any]]] = None,
    policy_fetch: Optional[Callable[[], Dict[str, Any]]] = None,
    scan_positions: bool = True,
    scan_history: bool = True,
) -> Dict[str, Any]:
    normalized_url = ensure_collector_url(config.collector_url)
    if str(config.environment or "").strip().lower() != environment_for_url(normalized_url):
        raise ValueError("采集器配置环境与服务地址不一致")
    outbox = LocalOutbox(Path(config.data_dir) / "collector.sqlite3")
    if config.account.account_code != "hongyuan_futures" or not config.account.confirmed or config.account.requires_manual_confirmation:
        return {
            "state": "account_pending",
            "queued": outbox.status()["pending"],
            "accepted": 0,
            "positions_accepted": 0,
        }

    default_sender = CollectorUploader(config.collector_url, config.device_token) if upload is None else None
    if default_sender is not None:
        try:
            default_sender.heartbeat(config.client_version)
        except Exception as exc:
            if getattr(exc, "status_code", None) in {401, 403}:
                return {
                    "state": "device_authorization_required",
                    "message": str(exc),
                    "queued": outbox.status()["pending"],
                    "accepted": 0,
                    "positions_accepted": 0,
                }
    policy_required = upload is None or policy_fetch is not None
    policy: Optional[CollectionPolicy] = None
    policy_error = ""
    if policy_required:
        policy = outbox.load_collection_policy()
        if policy is None:
            fetcher = policy_fetch or default_sender.get_collection_policy
            try:
                policy = CollectionPolicy.from_payload(fetcher())
                outbox.save_collection_policy(policy)
            except Exception as exc:
                policy_error = str(exc)
        if policy is not None and policy.environment != config.environment:
            policy = None
            policy_error = "采集策略环境与设备绑定环境不一致"
        if policy is not None:
            outbox.apply_collection_policy(policy)
    history_policy_paused = policy_required and policy is None

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

    def drain_pending(
        state: str,
        issue_count: int = 0,
        message: str = "",
        position_scan_requested: bool = False,
        allow_history: bool = True,
        allow_realtime: bool = True,
    ) -> Dict[str, Any]:
        # A large historical backlog must never occupy the realtime upload slot.
        claimed = outbox.claim(UPLOAD_BATCH_SIZE, priority="realtime") if allow_realtime else []
        if not claimed and allow_history:
            claimed = outbox.claim(UPLOAD_BATCH_SIZE, priority="history")
        if not claimed:
            result = {
                "state": state,
                "queued": outbox.status()["pending"],
                "accepted": 0,
                "positions_accepted": 0,
                "issues": issue_count,
                "position_scan_requested": position_scan_requested,
            }
            if message:
                result["message"] = message
            return result
        if policy is not None:
            uploadable = []
            blocked = []
            for item in claimed:
                if item.get("item_type", "fill") != "fill":
                    uploadable.append(item)
                    continue
                try:
                    item_payload = json.loads(str(item["payload_json"]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    blocked.append(item)
                    continue
                if policy.allows_upload(str(item_payload.get("trade_date") or "")):
                    uploadable.append(item)
                else:
                    blocked.append(item)
            if blocked:
                outbox.release(
                    [str(item["event_key"]) for item in blocked],
                    "outside_upload_policy",
                    retryable=False,
                )
            claimed = uploadable
        if not claimed:
            return {
                "state": state,
                "queued": outbox.status()["pending"],
                "accepted": 0,
                "positions_accepted": 0,
                "issues": issue_count,
                "position_scan_requested": position_scan_requested,
            }
        sender = upload or default_sender or CollectorUploader(config.collector_url, config.device_token)
        event_keys = [str(item["event_key"]) for item in claimed]
        fill_payloads = [json.loads(str(item["payload_json"])) for item in claimed if item.get("item_type", "fill") == "fill"]
        position_payloads = [json.loads(str(item["payload_json"])) for item in claimed if item.get("item_type") == "position_snapshot"]
        try:
            upload_result = send_payload(sender, fill_payloads, position_payloads)
        except Exception as exc:
            authorization_required = getattr(exc, "status_code", None) in {401, 403}
            outbox.release(event_keys, str(exc), retryable=not authorization_required)
            result = {
                "state": "device_authorization_required" if authorization_required else ("offline_queue" if state == "normal" else state),
                "message": str(exc),
                "queued": outbox.status()["pending"],
                "accepted": 0,
                "positions_accepted": 0,
                "issues": issue_count,
                "position_scan_requested": position_scan_requested,
            }
            return result
        fill_receipts = upload_result.get("fill_results") if isinstance(upload_result, dict) else None
        receipt_counts = {"acked": 0, "covered_by_monthly": 0, "conflict": 0, "quarantined": 0, "invalid": 0}
        if fill_payloads:
            if isinstance(fill_receipts, (list, tuple)):
                receipt_counts = outbox.ack_results(
                    fill_receipts,
                    expected_event_keys=[str(item["event_key"]) for item in claimed if item.get("item_type", "fill") == "fill"],
                )
                if receipt_counts["invalid"]:
                    outbox.add_issue(
                        ParseIssue(
                            "invalid_server_receipt",
                            "Staging 返回的成交回执缺失、重复或状态无效，未确认项目已退回重试",
                            str(outbox.db_path),
                            severity="error",
                        )
                    )
            else:
                outbox.add_issue(
                    ParseIssue(
                        "invalid_server_receipt",
                        "Staging 上传成功但未返回逐条成交回执，待确认项目已退回重试",
                        str(outbox.db_path),
                        severity="error",
                    )
                )
                outbox.release(
                    [str(item["event_key"]) for item in claimed if item.get("item_type", "fill") == "fill"],
                    "invalid_server_receipt",
                )
                receipt_counts["invalid"] = len(fill_payloads)
        position_keys = [str(item["event_key"]) for item in claimed if item.get("item_type") == "position_snapshot"]
        if position_keys:
            position_terminal_count = sum(
                int(upload_result.get(field, 0) or 0)
                for field in ("positions_accepted", "position_duplicates", "position_conflicts", "position_quarantined")
            ) if isinstance(upload_result, dict) else 0
            if position_terminal_count == len(position_keys):
                outbox.ack(position_keys)
            else:
                outbox.add_issue(
                    ParseIssue(
                        "invalid_server_receipt",
                        "Staging 上传成功但未返回完整持仓回执，待确认项目已退回重试",
                        str(outbox.db_path),
                        severity="error",
                    )
                )
                outbox.release(position_keys, "invalid_server_receipt")
                receipt_counts["invalid"] += len(position_keys)
        result = {
            "state": state,
            "queued": outbox.status()["pending"],
            "accepted": int(upload_result.get("accepted", 0)),
            "positions_accepted": int(upload_result.get("positions_accepted", upload_result.get("position_accepted", 0))),
            "position_duplicates": int(upload_result.get("position_duplicates", 0)),
            "position_conflicts": int(upload_result.get("position_conflicts", 0)),
            "duplicates": int(upload_result.get("duplicates", 0)),
            "conflicts": int(upload_result.get("conflicts", 0)),
            "receipt_invalid": int(receipt_counts["invalid"]),
            "issues": issue_count,
            "position_scan_requested": position_scan_requested,
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
    unknown_history_paused = False
    realtime_fill_queued = False
    today = business_trading_day(datetime.now(ZoneInfo("Asia/Shanghai")))
    for source in sources:
        source_date = str(source.trading_date or "").replace("-", "")
        is_realtime = source.kind == "position" or source_date == today.replace("-", "")
        if source.kind == "position" and not scan_positions:
            continue
        if source.kind == "match" and not is_realtime and not scan_history:
            # A file may contain both a current-day row and historical rows;
            # defer the latter to the per-record whitelist below.
            if source_date != today.replace("-", ""):
                continue
        if source.kind == "match" and not is_realtime and policy_required:
            if history_policy_paused or policy is None:
                continue
            if not source_date:
                unknown_history_paused = True
                continue
        checkpoint = outbox.load_checkpoint(str(source.path), kind=source.kind)
        batch = scan_source(source, checkpoint, account=config.account)
        priority = "realtime" if is_realtime else "history"
        filtered_fills = []
        contains_deferred_history = False
        for fill in batch.fills:
            fill_date = str(fill.trade_date or "").strip()
            fill_date_compact = fill_date.replace("-", "")
            fill_is_realtime = fill_date_compact == today.replace("-", "")
            if not fill_is_realtime and not scan_history:
                contains_deferred_history = True
                continue
            if policy_required and not fill_is_realtime:
                if history_policy_paused or policy is None:
                    contains_deferred_history = True
                    continue
                if not policy.allows_upload(fill_date):
                    continue
            filtered_fills.append(fill)
        for fill in filtered_fills:
            fill_priority = "realtime" if str(fill.trade_date).replace("-", "") == today.replace("-", "") else priority
            if outbox.put(fill, priority=fill_priority) and fill_priority == "realtime":
                realtime_fill_queued = True
        if batch.position_snapshot is not None:
            outbox.put_position(batch.position_snapshot, priority="realtime")
        for issue in batch.issues:
            outbox.add_issue(issue)
        # Do not advance a mixed-file checkpoint past deferred history while
        # the policy is unavailable; the next online scan must revisit it.
        if not (history_policy_paused and contains_deferred_history):
            outbox.save_checkpoint(str(source.path), batch.checkpoint, kind=batch.source_kind)
        issue_count += len(batch.issues)
        if any(issue.code == "unknown_format" for issue in batch.issues):
            paused_state = "format_unknown"
        elif paused_state == "normal" and any(issue.code == "path_unavailable" for issue in batch.issues):
            paused_state = "path_unavailable"
    if history_policy_paused or unknown_history_paused:
        paused_state = "policy_unavailable_history_paused"
        if not policy_error:
            policy_error = "历史采集策略不可用或无法确定历史文件日期，已暂停历史扫描"
    return drain_pending(
        paused_state,
        issue_count=issue_count,
        position_scan_requested=realtime_fill_queued,
        message=policy_error,
        allow_history=not (history_policy_paused or unknown_history_paused),
        allow_realtime=not history_policy_paused,
    )
def run_service(config: CollectorConfig, stop_event=None) -> None:
    """Run the polling loop used by the Windows background service.

    A caller-provided event is used by tests and an embedding host.  The
    packaged executable has no event, so it must remain alive until Windows
    stops the process rather than silently doing one extra scan and exiting.
    """
    interval = max(REALTIME_SCAN_SECONDS, int(config.poll_seconds))
    next_position_scan_at = time.monotonic()
    next_history_scan_at = next_position_scan_at
    while True:
        now = time.monotonic()
        scan_positions = now >= next_position_scan_at
        scan_history = now >= next_history_scan_at
        result = run_once(config, scan_positions=scan_positions, scan_history=scan_history)
        now = time.monotonic()
        if result.get("position_scan_requested"):
            next_position_scan_at = now
        elif scan_positions:
            next_position_scan_at = now + POSITION_SCAN_SECONDS
        if scan_history:
            next_history_scan_at = now + HISTORY_SCAN_SECONDS
        wait_seconds = min(
            interval,
            max(0.0, next_position_scan_at - now),
            max(0.0, next_history_scan_at - now),
        )
        if stop_event is None:
            time.sleep(wait_seconds)
            continue
        if stop_event.wait(wait_seconds):
            return


def activate_remote_device(pairing_code: str, device_name: str, fingerprint: str, client_version: str = CLIENT_VERSION) -> Dict[str, Any]:
    """Consume a one-time Web pairing code and return its token once."""
    route = resolve_collector_route(pairing_code)
    response = requests.post(
        route["base_url"] + "/api/trading-collector/device/activate",
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
    payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("environment") or "").strip().lower() != route["environment"]:
        raise ValueError("服务端返回的采集环境与连接码路由不一致")
    payload["collector_url"] = route["base_url"]
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WH6 期货与期权成交、持仓只读采集器")
    parser.add_argument("--config", type=Path, default=default_data_dir() / "config.json")
    parser.add_argument("--configure", action="store_true", help="保存手动选择的 WH6 成交与持仓缓存路径")
    parser.add_argument("--source", type=Path, help="WH6 match.dat/position.dat 或包含它们的 Record 目录")
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
                collector_url=DEFAULT_COLLECTOR_URL,
                source_path=str(selected_path if selected_path.is_dir() else source.path),
                account=strong_binding("宏源期货账户", "pending", "宏源期货账户待确认"),
                device_token="",
                data_dir=str(args.config.parent),
            )
            if args.pairing_code:
                fingerprint = args.fingerprint or "local-device"
                activated = activate_remote_device(args.pairing_code, args.device_name, fingerprint, CLIENT_VERSION)
                config.device_token = activated["token"]
                config.collector_url = activated["collector_url"]
                config.environment = str(activated.get("environment") or "staging").strip().lower()
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
        return 0 if result["state"] in {
            "normal",
            "offline_queue",
            "account_pending",
            "path_unavailable",
            "format_unknown",
            "policy_unavailable_history_paused",
        } else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"state": "configuration_error", "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
