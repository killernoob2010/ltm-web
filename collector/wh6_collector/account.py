"""Account binding helpers; paths are never treated as account identity."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from .models import AccountIdentity


def account_fingerprint(account_label: str, stable_id: Optional[str]) -> Optional[str]:
    """Return a stable, non-reversible fingerprint only with a verified ID."""
    if stable_id is None or not str(stable_id).strip():
        return None
    normalized_label = " ".join(str(account_label).strip().split()).lower()
    normalized_id = " ".join(str(stable_id).strip().split())
    return hashlib.sha256((normalized_label + "\0" + normalized_id).encode("utf-8")).hexdigest()


def confirm_weak_binding(masked_label: str, source_path: str) -> AccountIdentity:
    """Create an explicitly unconfirmed identity for formats without a stable ID."""
    return AccountIdentity(
        account_code="hongyuan_futures",
        display_name="宏源期货账户",
        masked_label=masked_label,
        stable_id=None,
        fingerprint=None,
        binding_mode="weak",
        confirmed=False,
        requires_manual_confirmation=True,
    )


def strong_binding(account_label: str, stable_id: str, masked_label: str = "") -> AccountIdentity:
    """Build the confirmed Macro Futures identity used by the first collector."""
    return AccountIdentity(
        account_code="hongyuan_futures",
        display_name="宏源期货账户",
        masked_label=masked_label or account_label,
        stable_id=stable_id,
        fingerprint=account_fingerprint(account_label, stable_id),
        binding_mode="strong",
        confirmed=True,
        requires_manual_confirmation=False,
    )


def probe_source_account(source_path: Path) -> AccountIdentity:
    """Best-effort read-only probe of nearby text metadata.

    WH6 builds do not share one documented account metadata filename.  We only
    trust an explicit account/investor key in a small text sidecar; otherwise
    the caller receives a weak identity and must pause for manual confirmation.
    """
    source_path = Path(source_path)
    roots = [source_path.parent]
    if source_path.parent.parent not in roots:
        roots.append(source_path.parent.parent)
    patterns = ("*account*", "*investor*", "*login*", "*user*")
    account_re = re.compile(r"(?:account|investor|\u8d44\u91d1\u8d26\u53f7|\u671f\u8d27\u8d26\u53f7|\u8d26\u6237)\s*[:=\uff1a,\t ]\s*([A-Za-z0-9_-]{6,20})", re.IGNORECASE)
    broker_re = re.compile(r"\u5b8f\u6e90|hongyuan", re.IGNORECASE)
    for root in roots:
        for pattern in patterns:
            for candidate in sorted(root.glob(pattern)):
                if not candidate.is_file() or candidate.stat().st_size > 64 * 1024:
                    continue
                try:
                    raw = candidate.read_bytes()
                except OSError:
                    continue
                for encoding in ("utf-8", "gb18030", "utf-16"):
                    try:
                        text = raw.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        text = ""
                match = account_re.search(text)
                if match and broker_re.search(text):
                    account_id = match.group(1)
                    return strong_binding(
                        "宏源期货账户",
                        account_id,
                        "宏源期货 ****" + account_id[-4:],
                    )
    return confirm_weak_binding("宏源期货账户待确认", str(source_path))


def compare_binding(bound: AccountIdentity, observed: AccountIdentity) -> str:
    """Return match, mismatch, or unknown without treating a path as identity."""
    if bound.fingerprint and str(bound.fingerprint).startswith("server-bound:"):
        return "unknown"
    if bound.fingerprint and observed.fingerprint:
        return "match" if bound.fingerprint == observed.fingerprint else "mismatch"
    return "unknown"
