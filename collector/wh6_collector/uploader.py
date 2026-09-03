"""Small HTTPS client; no Supabase service credentials are shipped here."""

from __future__ import annotations

from typing import Any, Dict, Sequence

import requests


class StagingUploader:
    def __init__(self, base_url: str, device_token: str, *, timeout_seconds: int = 8):
        self.base_url = base_url.rstrip("/")
        self.device_token = device_token
        self.timeout_seconds = timeout_seconds

    def send(
        self,
        _token: str,
        fills: Sequence[Dict[str, Any]],
        position_snapshots: Sequence[Dict[str, Any]] = (),
    ) -> Dict[str, Any]:
        response = requests.post(
            self.base_url + "/api/trading-collector/device/ingest",
            json={
                "observations": list(fills),
                "position_snapshots": list(position_snapshots),
            },
            headers={"X-Collector-Token": self.device_token},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError("Staging 上传失败（HTTP %s）" % response.status_code)
        return response.json()

    def __call__(self, token: str, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Compatibility adapter for callers that only have fill rows."""
        return self.send(token, items, ())
