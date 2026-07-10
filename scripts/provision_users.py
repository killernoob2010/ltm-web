#!/usr/bin/env python3
"""Dry-run-first provisioning for the initial internal user roster."""

import argparse
import getpass
import json
import os
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from openpyxl import load_workbook


HEADERS = {
    "姓名": "name",
    "部门": "department",
    "用户类型": "role",
    "登录账号（可空）": "username",
}


def load_roster(path: Path) -> list[dict[str, str]]:
    sheet = load_workbook(path, read_only=True, data_only=True).active
    values = sheet.iter_rows(values_only=True)
    headers = [str(value or "").strip() for value in next(values, [])]
    missing = [header for header in HEADERS if header not in headers]
    if missing:
        raise ValueError(f"名单缺少列: {', '.join(missing)}")
    indexes = {HEADERS[header]: headers.index(header) for header in HEADERS}
    rows = []
    for row_no, values_row in enumerate(values, start=2):
        if not any(value not in (None, "") for value in values_row):
            continue
        item = {
            key: str(values_row[index] or "").strip()
            for key, index in indexes.items()
        }
        if not item["name"] or not item["department"] or not item["role"]:
            raise ValueError(f"第 {row_no} 行姓名、部门和用户类型不能为空")
        rows.append(item)
    if not rows:
        raise ValueError("名单中没有用户")
    return rows


def preflight_roster(
    rows: list[dict[str, str]],
    preview: Callable[[dict], dict],
) -> list[dict[str, object]]:
    results = []
    conflicts = []
    usernames = set()
    for row in rows:
        payload = {**row, "permissions": []}
        item = preview(payload)
        username = item["username"]
        if not item.get("username_available", False) or username in usernames:
            conflicts.append(username)
        usernames.add(username)
        results.append({
            "name": row["name"],
            "username": username,
            "temporary_password": item["temporary_password"],
            "department": row["department"],
            "role": row["role"],
            "permission_count": sum(
                level != "none" for level in item.get("final_permissions", {}).values()
            ),
        })
    if conflicts:
        raise ValueError(f"登录账号冲突: {', '.join(sorted(set(conflicts)))}")
    return results


class ApiClient:
    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, path: str, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except json.JSONDecodeError:
                pass
            raise ValueError(f"API {exc.code}: {detail}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="预检并创建首批内部用户（默认仅 dry-run）")
    parser.add_argument("--base-url", required=True, help="Staging 或经确认的 Production URL")
    parser.add_argument("--file", required=True, type=Path, help="Excel 用户名单")
    parser.add_argument("--apply", action="store_true", help="通过预检后真正创建用户")
    args = parser.parse_args()

    username = os.getenv("LTM_ADMIN_USERNAME") or input("管理员登录账号: ").strip()
    password = os.getenv("LTM_ADMIN_PASSWORD") or getpass.getpass("管理员密码: ")
    client = ApiClient(args.base_url)
    login = client.request("/api/auth/login", {"username": username, "password": password})
    client.token = login["token"]

    rows = load_roster(args.file)
    previews = preflight_roster(rows, lambda payload: client.request("/api/users/preview", payload))
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "users": previews}, ensure_ascii=False, indent=2))
    if not args.apply:
        return

    for row, preview in zip(rows, previews):
        client.request("/api/users", {
            **row,
            "username": preview["username"],
            "permissions": [],
        })
    print(f"已创建 {len(rows)} 个用户")


if __name__ == "__main__":
    main()
