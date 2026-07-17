"""Read-only EBC and Sina data-source adapters for iron-ore basis sync."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
import time
from typing import Callable, Mapping, Sequence

import requests

from .info_summary_backfill import parse_sina_history_text


EBC_BASE_URL = "https://ebc.ejianlong.com"
EBC_LOGIN_PATH = "/framework/web/customer/login"
EBC_QUERY_PATH = "/api/database/db/queryIndexData"
SINA_I0_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php"
    "/var%20_i0=/InnerFuturesNewService.getDailyKLine?symbol=i0"
)
RETRY_DELAYS = (3, 8)


class BasisSourceError(RuntimeError):
    """A source failure safe to persist without credentials or response bodies."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str | None = None,
        http_status: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.http_status = http_status


@dataclass(frozen=True)
class SourcePoint:
    source_name: str
    indicator_key: str
    business_date: date
    value: float | None
    payload_sha256: str


def _point_hash(source_name: str, indicator_key: str, business_date: date, value: float | None) -> str:
    encoded = json.dumps(
        {
            "source_name": source_name,
            "indicator_key": indicator_key,
            "business_date": business_date.isoformat(),
            "value": value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _http_status(error: requests.RequestException) -> int | None:
    response = getattr(error, "response", None)
    return int(response.status_code) if response is not None and response.status_code is not None else None


def _is_retryable_request_error(error: requests.RequestException) -> bool:
    status = _http_status(error)
    if status is not None:
        return status == 429 or status >= 500
    return isinstance(error, (requests.ConnectionError, requests.Timeout))


def _request_with_retry(
    request: Callable[[], requests.Response],
    *,
    stage: str,
    message: str,
    sleep_func: Callable[[float], None],
):
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = request()
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            if attempt >= len(RETRY_DELAYS) or not _is_retryable_request_error(exc):
                raise BasisSourceError(
                    "http_error",
                    message,
                    stage=stage,
                    http_status=_http_status(exc),
                ) from exc
            sleep_func(RETRY_DELAYS[attempt])
    raise RuntimeError("unreachable")


class EbcBasisSource:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        environ: Mapping[str, str] | None = None,
        base_url: str = EBC_BASE_URL,
        timeout: int = 15,
        sleep_func: Callable[[float], None] = time.sleep,
    ):
        self.session = session or requests.Session()
        self.environ = environ if environ is not None else os.environ
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.sleep_func = sleep_func
        self._token: str | None = None

    @property
    def _browser_headers(self) -> dict[str, str]:
        return {
            "Referer": f"{self.base_url}/",
            "User-Agent": "Mozilla/5.0 (compatible; LTM-IronOreBasisSync/1.0)",
        }

    def _post_json(self, path: str, *, stage: str, **kwargs) -> dict:
        response = _request_with_retry(
            lambda: self.session.post(
                f"{self.base_url}{path}",
                timeout=self.timeout,
                **kwargs,
            ),
            stage=stage,
            message="EBC 请求失败",
            sleep_func=self.sleep_func,
        )
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise BasisSourceError("invalid_response", "EBC 返回格式无效", stage=stage) from exc
        if not isinstance(payload, dict):
            raise BasisSourceError("invalid_response", "EBC 返回格式无效", stage=stage)
        return payload

    def login(self) -> str:
        account = (self.environ.get("EBC_ACCOUNT") or "").strip()
        password = self.environ.get("EBC_PASSWORD") or ""
        if not account or not password:
            raise BasisSourceError("missing_credentials", "EBC 凭据未配置")
        payload = self._post_json(
            EBC_LOGIN_PATH,
            stage="ebc_login",
            json={
                "account": account,
                "password": password,
                "verifCode": "",
                "mainboard": self.environ.get("EBC_MAINBOARD", "1"),
                "centralProcessUnit": self.environ.get("EBC_CPU", "1"),
                "pc": 1,
            },
            headers=self._browser_headers,
        )
        token = (payload.get("data") or {}).get("accessToken") if payload.get("success") else None
        if str(payload.get("code")) != "200" or not token:
            raise BasisSourceError("login_rejected", "EBC 登录未通过")
        self._token = str(token)
        return self._token

    def fetch_points(
        self,
        indicator_codes: Sequence[str],
        start_date: date,
        end_date: date,
    ) -> list[SourcePoint]:
        codes = list(dict.fromkeys(str(code).strip() for code in indicator_codes if str(code).strip()))
        if not codes:
            return []
        token = self._token or self.login()
        query = {
            "indexCodes": codes,
            "deriveIndexes": [],
            "frequency": "",
            "beginTime": start_date.isoformat(),
            "endTime": end_date.isoformat(),
            "sortByDate": -1,
            "timeCount": None,
            "timeType": None,
            "dateType": 0,
            "formatTime": False,
            "codeAndResourceIdList": [{"indexCode": code} for code in codes],
            "decimalPlaces": {code: None for code in codes},
        }
        response = self._post_json(
            EBC_QUERY_PATH,
            stage="ebc_query",
            json=query,
            headers={**self._browser_headers, "accessToken": token},
        )
        if str(response.get("code")) != "200" or not response.get("success"):
            raise BasisSourceError("query_rejected", "EBC 数据查询未通过")
        rows = response.get("data") or []
        if not isinstance(rows, list):
            raise BasisSourceError("invalid_response", "EBC 数据格式无效")
        points: list[SourcePoint] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("dataDate"):
                raise BasisSourceError("invalid_response", "EBC 数据行格式无效")
            try:
                business_date = date.fromisoformat(str(row["dataDate"])[:10])
            except ValueError as exc:
                raise BasisSourceError("invalid_response", "EBC 数据日期无效") from exc
            if business_date < start_date or business_date > end_date:
                continue
            for code in codes:
                raw_value = row.get(code)
                value = None if raw_value in (None, "") else float(raw_value)
                points.append(
                    SourcePoint(
                        source_name="EBC",
                        indicator_key=code,
                        business_date=business_date,
                        value=value,
                        payload_sha256=_point_hash("EBC", code, business_date, value),
                    )
                )
        return points


class SinaI0Source:
    def __init__(
        self,
        *,
        http_get: Callable = requests.get,
        timeout: int = 8,
        sleep_func: Callable[[float], None] = time.sleep,
    ):
        self.http_get = http_get
        self.timeout = timeout
        self.sleep_func = sleep_func

    def fetch_closes(self, start_date: date, end_date: date) -> dict[date, float]:
        response = _request_with_retry(
            lambda: self.http_get(SINA_I0_URL, timeout=self.timeout),
            stage="i0_query",
            message="新浪 I0 请求失败",
            sleep_func=self.sleep_func,
        )
        try:
            parsed = parse_sina_history_text(
                response.text,
                since_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
        except (ValueError, json.JSONDecodeError) as exc:
            raise BasisSourceError(
                "invalid_response",
                "新浪 I0 返回格式无效",
                stage="i0_query",
            ) from exc
        return {date.fromisoformat(day): value for day, value in parsed.items()}
