"""PyInstaller entry point for the Windows executable.

The collector itself remains importable as ``wh6_collector.cli`` for local
tests and embedding.  A package-level launcher keeps relative imports valid
when PyInstaller starts the bundled executable as ``__main__``.
"""

import ctypes
import os

from wh6_collector.cli import main


MUTEX_NAME = r"Local\LTM-WH6-Collector-B7C23B59"


def acquire_single_instance():
    """Keep one collector process alive without touching the WH6 process."""

    if os.name != "nt":
        return object()
    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise OSError("无法创建 WH6 采集器单实例互斥锁")
    if kernel32.GetLastError() == 183:
        kernel32.CloseHandle(handle)
        return None
    return handle


def release_single_instance(handle) -> None:
    if os.name == "nt" and handle:
        ctypes.windll.kernel32.CloseHandle(handle)


if __name__ == "__main__":
    mutex_handle = acquire_single_instance()
    if mutex_handle is None:
        raise SystemExit(0)
    try:
        raise SystemExit(main())
    finally:
        release_single_instance(mutex_handle)
