#!/usr/bin/env python3
"""Read complete sales types from the trade-system report and update Staging only.

The report is read locally because the Render Staging service may not share the
company-network route.  The Staging API receives only source detail IDs, complete
business-category values and optimistic expected values; no Excel fields are used.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.spot_ledger import is_complete_source_sales_type  # noqa: E402
from app.spot_ledger_sync import (  # noqa: E402
    ProfiledSalesContractSource,
    build_candidate_source_profile,
)


STAGING_BASE_URL = "https://ltm-web-staging.onrender.com"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def validate_staging_base_url(value: str) -> str:
    parsed = urlparse(value.strip())
    expected = urlparse(STAGING_BASE_URL)
    if (
        parsed.scheme != "https"
        or parsed.netloc != expected.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("只允许使用 https://ltm-web-staging.onrender.com，禁止其他环境或带查询参数地址")
    return STAGING_BASE_URL


def _source_headers() -> dict[str, str]:
    raw = (os.getenv("SPOT_LEDGER_SOURCE_HEADERS") or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("SPOT_LEDGER_SOURCE_HEADERS 必须是 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ValueError("SPOT_LEDGER_SOURCE_HEADERS 必须是 JSON 对象")
    return {str(key): str(item) for key, item in value.items() if item is not None}


def build_local_report_source() -> ProfiledSalesContractSource:
    username = (os.getenv("SPOT_LEDGER_SOURCE_USERNAME") or "").strip()
    password = os.getenv("SPOT_LEDGER_SOURCE_PASSWORD") or ""
    if username and password:
        return ProfiledSalesContractSource.from_env()
    profile_path = (os.getenv("SPOT_LEDGER_SOURCE_PROFILE") or "").strip()
    if profile_path:
        profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    else:
        profile = build_candidate_source_profile(
            datetime.now(SHANGHAI_TZ).date(),
            page_size=500,
        )
    return ProfiledSalesContractSource(
        profile,
        auth_provider=_source_headers,
    )


def read_source_sales_type_rows(source: ProfiledSalesContractSource | None = None) -> list[dict[str, Any]]:
    scan = (source or build_local_report_source()).fetch_full_scan()
    if not scan.complete:
        raise RuntimeError("贸易系统销售类型报表扫描不完整，已停止写入")
    return [
        {
            "source_detail_id": str(record.get("source_detail_id") or "").strip(),
            "D": str(record.get("D") or "").strip(),
        }
        for record in scan.records
    ]


def build_sales_type_backfill_plan(
    source_rows: Iterable[dict[str, Any]],
    target_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    source = list(source_rows)
    targets = list(target_records)
    target_by_detail: dict[str, dict[str, Any]] = {}
    ambiguous_targets: set[str] = set()
    for record in targets:
        detail_id = str(record.get("source_detail_id") or "").strip()
        if not detail_id:
            continue
        if detail_id in target_by_detail:
            ambiguous_targets.add(detail_id)
        target_by_detail[detail_id] = record

    result: dict[str, Any] = {
        "source_rows": len(source),
        "target_rows": len(targets),
        "invalid": 0,
        "ambiguous": len(ambiguous_targets),
        "unmatched": 0,
        "matched": 0,
        "unchanged": 0,
        "to_update": 0,
        "plans": [],
    }
    seen: set[str] = set()
    for row in source:
        detail_id = str(row.get("source_detail_id") or "").strip()
        business_category = str(row.get("D") or "").strip()
        if (
            not detail_id
            or detail_id in seen
            or detail_id in ambiguous_targets
            or not is_complete_source_sales_type(business_category)
        ):
            result["invalid"] += 1
            continue
        seen.add(detail_id)
        target = target_by_detail.get(detail_id)
        if target is None:
            result["unmatched"] += 1
            continue
        result["matched"] += 1
        current = "" if target.get("D") is None else str(target.get("D")).strip()
        if current == business_category:
            result["unchanged"] += 1
            continue
        result["to_update"] += 1
        result["plans"].append(
            {
                "record_id": str(target.get("record_id") or "").strip(),
                "source_detail_id": detail_id,
                "business_category": business_category,
                "expected_value": current,
            }
        )
    return result


class StagingLedgerClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 30,
        session: requests.Session | None = None,
    ):
        self.base_url = validate_staging_base_url(base_url)
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = session or requests.Session()
        self.token = ""

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            timeout=self.timeout,
            **kwargs,
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"Staging API HTTP {response.status_code}: {method} {path}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Staging API returned invalid JSON: {method} {path}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Staging API returned an invalid object: {method} {path}")
        return payload

    def login(self) -> None:
        result = self._request(
            "POST",
            "/api/auth/login",
            json={"username": self.username, "password": self.password},
        )
        self.token = str(result.get("token") or "")
        if not self.token:
            raise RuntimeError("Staging login returned no token")

    def get_source_sales_type_snapshot(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/api/spot-ledger/source-sales-type-snapshot")
        return [item for item in result.get("records", []) if isinstance(item, dict)]

    def backfill_sales_types(self, rows: list[dict[str, Any]], *, apply: bool) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/spot-ledger/source-sales-type-backfill",
            json={"rows": rows, "apply": apply},
        )


def apply_sales_type_backfill_plan(
    client: StagingLedgerClient,
    result: dict[str, Any],
    change_log: Path,
) -> dict[str, Any]:
    plans = [item for item in result.get("plans", []) if isinstance(item, dict)]
    change_log.parent.mkdir(parents=True, exist_ok=True)
    change_log.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8")
    if not plans:
        return {"updated": 0, "change_log": str(change_log)}
    response = client.backfill_sales_types(plans, apply=True)
    return {"change_log": str(change_log), **response}


def safe_summary(result: dict[str, Any]) -> str:
    hidden = {"plans", "password", "token", "username", "expected_value"}
    public = {key: value for key, value in result.items() if key not in hidden}
    return json.dumps(public, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description="从贸易系统完整销售类型回填现货台账 Staging。")
    parser.add_argument("--base-url", default=STAGING_BASE_URL)
    parser.add_argument("--apply", action="store_true", help="通过测试版接口写入完整销售类型")
    parser.add_argument("--change-log", type=Path, default=Path("/tmp/spot-ledger-sales-type-change-log.json"))
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    username = os.getenv("STAGING_LEDGER_USERNAME", "")
    password = os.getenv("STAGING_LEDGER_PASSWORD", "")
    if not username or not password:
        print(json.dumps({"ok": False, "error": "需要设置 STAGING_LEDGER_USERNAME 和 STAGING_LEDGER_PASSWORD（不通过命令行传递）"}, ensure_ascii=False))
        return 2
    try:
        client = StagingLedgerClient(args.base_url, username, password, timeout=args.timeout)
        client.login()
        source_rows = read_source_sales_type_rows()
        target_records = client.get_source_sales_type_snapshot()
        plan = build_sales_type_backfill_plan(source_rows, target_records)
        preview = client.backfill_sales_types(plan["plans"], apply=False)
        if preview.get("to_update") != plan["to_update"] or preview.get("conflicts"):
            raise RuntimeError("测试版预览与本地匹配结果不一致，已停止写入")
        print(json.dumps({"ok": True, "plan": json.loads(safe_summary(plan)), "staging_preview": preview}, ensure_ascii=False, separators=(",", ":")))
        if not args.apply:
            return 0
        applied = apply_sales_type_backfill_plan(client, plan, args.change_log)
        print(json.dumps({"ok": applied.get("conflicts", 0) == 0, "apply": applied}, ensure_ascii=False, separators=(",", ":")))
        return 0 if applied.get("conflicts", 0) == 0 else 1
    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
