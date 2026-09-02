"""Account binding helpers; paths are never treated as account identity."""

from __future__ import annotations

import hashlib
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
