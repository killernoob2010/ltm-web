"""Small Windows-current-user credential wrapper for the device token.

The development host is macOS, so tests keep a plain value in the local
config fallback.  The Windows bundle uses the OS DPAPI, which encrypts the
token for the current Windows user and machine without adding a third-party
secret-management dependency.
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import os


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _protect_windows(value: str) -> str:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    raw = value.encode("utf-8")
    source = ctypes.create_string_buffer(raw)
    source_blob = _DataBlob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    result_blob = _DataBlob()
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob), wintypes.LPVOID,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(source_blob), "WH6成交采集器 device token", None, None, None, 0, ctypes.byref(result_blob)
    ):
        raise OSError(ctypes.get_last_error(), "Windows 凭据保护失败")
    try:
        encrypted = ctypes.string_at(result_blob.pbData, result_blob.cbData)
    finally:
        kernel32.LocalFree(result_blob.pbData)
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def _unprotect_windows(value: str) -> str:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    encrypted = base64.b64decode(value.removeprefix("dpapi:"), validate=True)
    source = ctypes.create_string_buffer(encrypted)
    source_blob = _DataBlob(len(encrypted), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    result_blob = _DataBlob()
    description = wintypes.LPWSTR()
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source_blob), ctypes.byref(description), None, None, None, 0, ctypes.byref(result_blob)
    ):
        raise OSError(ctypes.get_last_error(), "Windows 凭据读取失败")
    try:
        return ctypes.string_at(result_blob.pbData, result_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(result_blob.pbData)
        if description:
            kernel32.LocalFree(description)


def protect_token(value: str) -> str:
    if not value or os.name != "nt":
        return value
    return _protect_windows(value)


def unprotect_token(value: str) -> str:
    if not value or not value.startswith("dpapi:"):
        return value
    if os.name != "nt":
        raise RuntimeError("该设备令牌由 Windows 当前用户保护，不能在非 Windows 主机解密")
    return _unprotect_windows(value)
