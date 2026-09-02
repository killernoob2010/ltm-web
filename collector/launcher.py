"""PyInstaller entry point for the Windows executable.

The collector itself remains importable as ``wh6_collector.cli`` for local
tests and embedding.  A package-level launcher keeps relative imports valid
when PyInstaller starts the bundled executable as ``__main__``.
"""

from wh6_collector.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
