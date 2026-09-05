"""Small HTTPS client; no Supabase service credentials are shipped here."""

from __future__ import annotations

from typing import Any, Dict, Sequence

import requests


class UploadError(RuntimeError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class CollectorUploader:
    def __init__(self, base_url: str, device_token: str, *, timeout_seconds=(5, 30)):
        self.base_url = base_url.rstrip("/")
        self.device_token = device_token
        self.timeout_seconds = timeout_seconds

    def heartbeat(self, client_version: str) -> Dict[str, Any]:
        response = requests.post(
            self.base_url + "/api/trading-collector/device/heartbeat",
            json={"client_version": client_version},
            headers={"X-Collector-Token": self.device_token},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise UploadError(
                "采集服务状态上报失败（HTTP %s）" % response.status_code,
                response.status_code,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("采集服务状态响应格式无效")
        return payload

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
            raise UploadError("采集服务上传失败（HTTP %s）" % response.status_code, response.status_code)
        return response.json()

    def get_collection_policy(self) -> Dict[str, Any]:
        response = requests.get(
            self.base_url + "/api/trading-collector/device/collection-policy",
            headers={"X-Collector-Token": self.device_token},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise UploadError(
                "采集服务策略获取失败（HTTP %s）" % response.status_code,
                response.status_code,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("采集服务策略响应格式无效")
        return payload

    def __call__(self, token: str, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Compatibility adapter for callers that only have fill rows."""
        return self.send(token, items, ())


class StagingUploader(CollectorUploader):
    """Backward-compatible import name for existing local integrations."""
