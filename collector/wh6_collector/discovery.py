"""Windows-independent WH6 source discovery and manual path validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Sequence

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


def _candidate_files(root: Path) -> Iterable[Path]:
    if not root.exists() or not root.is_dir():
        return ()
    # Only match/fill files are business sources.  order.dat is intentionally excluded.
    return (
        path
        for path in root.rglob("*match.dat")
        if path.is_file() and "record" in {part.lower() for part in path.parts}
    )


def _to_source(path: Path, root: Path) -> SourceFile:
    stat = path.stat()
    return SourceFile(
        path=path,
        kind="match",
        label="WH6 成交缓存",
        account_clue="",
        modified_ns=stat.st_mtime_ns,
        record_size=None,
        validation_reason="已发现 match 成交缓存文件",
        readable=os.access(path, os.R_OK),
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
        raise ValueError("所选目录未找到可读取的 WH6 match 成交缓存")
    return [
        _to_source(candidate, selected)
        for candidate in sorted(candidates, key=lambda item: item.as_posix().lower())
    ]


def validate_source(path: Path) -> SourceFile:
    selected = Path(path).expanduser()
    if selected.is_file():
        if "match" not in selected.name.lower() or not selected.name.lower().endswith(".dat"):
            raise ValueError("手动选择的位置不是 WH6 成交缓存文件")
        return _to_source(selected, selected.parent)
    return validate_sources(selected)[0]
