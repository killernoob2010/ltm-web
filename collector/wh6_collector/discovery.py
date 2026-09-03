"""Windows-independent WH6 source discovery and manual path validation."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Iterable, List, Optional, Sequence

from .models import SourceFile


def _default_roots() -> List[Path]:
    roots: List[Path] = []
    for key in ("PROGRAMDATA", "LOCALAPPDATA", "APPDATA"):
        value = os.getenv(key)
        if value:
            roots.append(Path(value))
    roots.extend(
        [
            Path("C:/文华财经"),
            Path("C:/wh6通用版"),
            Path("C:/WH6"),
        ]
    )
    return roots


def _cache_kind(path: Path) -> Optional[str]:
    name = path.name.lower()
    if name.endswith("match.dat"):
        return "match"
    if (
        name.endswith("position.dat")
        or name.endswith("position.bin")
        or name.endswith("position.json")
    ):
        return "position"
    return None


def _candidate_files(root: Path) -> Iterable[Path]:
    if not root.exists() or not root.is_dir():
        return ()
    # Only explicit match/position files are business sources.  order.dat and
    # arbitrary cache names are intentionally excluded.
    return (
        path
        for path in root.rglob("*")
        if path.is_file()
        and _cache_kind(path)
        and "record" in {part.lower() for part in path.parts}
    )


def _to_source(path: Path, root: Path) -> SourceFile:
    stat = path.stat()
    kind = _cache_kind(path)
    if kind is None:
        raise ValueError("不是已注册的 WH6 缓存文件")
    label = "WH6 成交缓存" if kind == "match" else "WH6 完整持仓缓存"
    validation = "已发现 match 成交缓存文件" if kind == "match" else "已发现 position 持仓缓存文件"
    date_match = re.search(r"(?P<date>\d{8})", path.name)
    return SourceFile(
        path=path,
        kind=kind,
        label=label,
        account_clue="",
        modified_ns=stat.st_mtime_ns,
        record_size=None,
        validation_reason=validation,
        readable=os.access(path, os.R_OK),
        trading_date=(date_match.group("date") if date_match else "") or None,
        root_path=str(root),
    )


def discover_wh6_sources(extra_roots: Sequence[Path] = ()) -> List[SourceFile]:
    roots: List[Path] = []
    seen = set()
    for root in list(extra_roots) + _default_roots():
        candidate = Path(root).expanduser()
        key = str(candidate)
        if key not in seen:
            roots.append(candidate)
            seen.add(key)
    found: List[SourceFile] = []
    seen_files = set()
    for root in roots:
        for path in _candidate_files(root):
            if str(path) in seen_files:
                continue
            seen_files.add(str(path))
            try:
                found.append(_to_source(path, root))
            except OSError:
                continue
    return sorted(found, key=lambda item: (item.path.as_posix().lower(), -item.modified_ns))


def validate_sources(path: Path) -> List[SourceFile]:
    """Validate a manually selected file or Record directory.

    A directory selection is a source root, not a single file: every
    supported match cache below it is returned so historical backfill cannot
    silently stop at the first trading day.
    """
    selected = Path(path).expanduser()
    if selected.is_file():
        return [validate_source(selected)]
    candidates = list(_candidate_files(selected))
    if not candidates:
        raise ValueError("所选目录未找到可读取的 WH6 match 或 position 缓存")
    return [
        _to_source(candidate, selected)
        for candidate in sorted(candidates, key=lambda item: item.as_posix().lower())
    ]


def validate_source(path: Path) -> SourceFile:
    selected = Path(path).expanduser()
    if selected.is_file():
        if _cache_kind(selected) is None:
            raise ValueError("手动选择的位置不是已注册的 WH6 成交或持仓缓存文件")
        return _to_source(selected, selected.parent)
    return validate_sources(selected)[0]
