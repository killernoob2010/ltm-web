"""Small first-run setup dialog used by the packaged Windows executable.

The dialog only performs local path selection and the already-defined device
pairing request.  It never controls WH6 or submits a trading transaction.
"""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path
from typing import Optional
import uuid

from .account import probe_source_account
from .discovery import discover_wh6_sources, validate_sources
from .models import AccountIdentity


def build_device_fingerprint(*, machine_name: Optional[str] = None, mac_value: Optional[int] = None) -> str:
    """Return a stable, non-reversible identifier for this Windows install."""

    name = machine_name if machine_name is not None else platform.node()
    mac = mac_value if mac_value is not None else uuid.getnode()
    return hashlib.sha256(f"{name}|{mac}".encode("utf-8")).hexdigest()


def _record_root(path: Path) -> Path:
    return next((parent for parent in path.parents if parent.name.lower() == "record"), path.parent)


def run_first_setup(config_path: Path) -> int:
    """Run the minimal first-run wizard and persist a bound collector config."""

    # Keep imports lazy: the command-line core and Mac test suite must not need
    # a graphical display just to import the collector package.
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog
    except Exception as exc:  # pragma: no cover - only reachable on stripped Windows images
        print(f"WH6 首次设置需要 Windows 图形组件：{exc}")
        return 2

    from .cli import CLIENT_VERSION, DEFAULT_STAGING_URL, CollectorConfig, activate_remote_device

    config_path = Path(config_path)
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showinfo(
            "WH6 成交采集器首次设置",
            "本向导只读取 WH6 已成交缓存，不会下单、撤单或修改 WH6。\n\n"
            "请先登录宏源期货账户，再选择 WH6 的 Record 目录。",
            parent=root,
        )

        discovered = discover_wh6_sources()
        roots = sorted({_record_root(item.path) for item in discovered}, key=lambda item: str(item).lower())
        selected_path: Optional[Path] = None
        if len(roots) == 1:
            candidate = roots[0]
            if messagebox.askyesno(
                "发现 WH6 缓存",
                f"已发现一个 WH6 Record 目录：\n{candidate}\n\n是否使用它？",
                parent=root,
            ):
                selected_path = candidate
        if selected_path is None:
            chosen = filedialog.askdirectory(
                parent=root,
                title="请选择 WH6 的 Record 目录（只读）",
                mustexist=True,
            )
            if not chosen:
                messagebox.showwarning("尚未设置", "未选择目录，采集器保持未设置状态。", parent=root)
                return 1
            selected_path = Path(chosen)

        try:
            sources = validate_sources(selected_path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("目录不可用", str(exc), parent=root)
            return 1
        if not sources:
            messagebox.showerror("目录不可用", "所选目录没有可读取的 WH6 成交缓存。", parent=root)
            return 1

        observed = probe_source_account(sources[0].path)
        observed_label = observed.masked_label or "宏源期货账户待确认"
        if not messagebox.askyesno(
            "确认账户",
            f"当前缓存显示：{observed_label}\n\n"
            "请确认 WH6 当前登录的确实是要绑定的宏源期货账户。\n"
            "身份无法稳定读取时，切换账户会自动暂停。",
            parent=root,
        ):
            return 1

        pairing_code = simpledialog.askstring(
            "绑定采集设备",
            "请先在 Web 测试版“采集设备”页面生成一次性连接码，然后粘贴到这里：",
            parent=root,
        )
        if not pairing_code or not pairing_code.strip():
            messagebox.showwarning("尚未绑定", "未输入连接码，采集器保持未设置状态。", parent=root)
            return 1

        device_name = f"Windows WH6 {platform.node()[:48]}".strip() or "Windows WH6 采集器"
        try:
            activated = activate_remote_device(
                DEFAULT_STAGING_URL,
                pairing_code.strip(),
                device_name,
                build_device_fingerprint(),
                CLIENT_VERSION,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("设备绑定失败", str(exc), parent=root)
            return 1

        account_label = str(activated.get("account_label") or "宏源期货账户")
        token = str(activated.get("token") or "").strip()
        account_id = str(activated.get("account_id") or "").strip()
        if not token or not account_id:
            messagebox.showerror("设备绑定失败", "服务端没有返回完整的设备绑定结果。", parent=root)
            return 1

        account = AccountIdentity(
            account_code="hongyuan_futures",
            display_name="宏源期货账户",
            masked_label=account_label,
            stable_id=None,
            fingerprint=f"server-bound:{account_id}",
            binding_mode="strong",
            confirmed=True,
            requires_manual_confirmation=False,
        )
        config = CollectorConfig(
            staging_url=DEFAULT_STAGING_URL,
            source_path=str(selected_path),
            account=account,
            device_token=token,
            data_dir=str(config_path.parent),
            allow_weak_source=not bool(observed.fingerprint),
            source_account_fingerprint=observed.fingerprint,
        )
        config.save(config_path)
        messagebox.showinfo(
            "设置完成",
            "设备已绑定。采集器将先回补已识别的历史期权成交，之后每 10 秒检查一次新成交。\n\n"
            "本程序只读缓存，不执行任何交易操作。",
            parent=root,
        )
        return 0
    finally:
        root.destroy()
