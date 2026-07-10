import sys
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_roster_preflight_reads_required_columns_and_stops_before_apply_on_conflict(tmp_path):
    from scripts import provision_users

    path = tmp_path / "users.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["姓名", "部门", "用户类型", "登录账号（可空）"])
    sheet.append(["张三", "贸易处", "用户", "zhangsan"])
    sheet.append(["李雷", "期货组", "领导", ""])
    book.save(path)

    rows = provision_users.load_roster(path)
    calls = []

    def preview(payload):
        calls.append(payload)
        username = payload["username"] or "lilei"
        return {
            "name": payload["name"],
            "username": username,
            "temporary_password": f"{username}123",
            "username_available": username != "zhangsan",
            "final_permissions": {"info_summary": "operate"},
        }

    try:
        provision_users.preflight_roster(rows, preview)
    except ValueError as exc:
        assert "zhangsan" in str(exc)
    else:
        raise AssertionError("conflicting roster must fail before any create call")

    assert len(calls) == 2
    assert rows[1]["role"] == "领导"


def test_roster_preflight_returns_preview_without_writing(tmp_path):
    from scripts import provision_users

    path = tmp_path / "users.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["姓名", "部门", "用户类型", "登录账号（可空）"])
    sheet.append(["王芳", "财企处", "用户", "wangfang"])
    book.save(path)

    rows = provision_users.load_roster(path)
    result = provision_users.preflight_roster(
        rows,
        lambda payload: {
            **payload,
            "username": "wangfang",
            "temporary_password": "wangfang123",
            "username_available": True,
            "final_permissions": {"order_finance_progress": "operate"},
        },
    )

    assert result == [{
        "name": "王芳",
        "username": "wangfang",
        "temporary_password": "wangfang123",
        "department": "财企处",
        "role": "用户",
        "permission_count": 1,
    }]
