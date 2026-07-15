import os
import sys
from datetime import date

import pytest
import requests


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.iron_ore_basis_sources import (  # noqa: E402
    BasisSourceError,
    EbcBasisSource,
    SinaI0Source,
)


class FakeResponse:
    def __init__(self, payload=None, *, text="", status_error=None, status_code=200):
        self.payload = payload
        self.text = text
        self.status_error = status_error
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}", response=self)

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_ebc_requires_credentials_without_making_request():
    session = FakeSession([])
    source = EbcBasisSource(session=session, environ={})

    with pytest.raises(BasisSourceError) as exc:
        source.login()

    assert exc.value.code == "missing_credentials"
    assert session.calls == []


def test_ebc_logs_in_once_and_fetches_all_codes_in_one_batch():
    session = FakeSession(
        [
            FakeResponse({"success": True, "code": "200", "data": {"accessToken": "temporary-token"}}),
            FakeResponse(
                {
                    "success": True,
                    "code": "200",
                    "data": [
                        {"dataDate": "2026-07-13", "ID1": 700.5, "ID2": None},
                    ],
                }
            ),
        ]
    )
    source = EbcBasisSource(
        session=session,
        environ={"EBC_ACCOUNT": "account", "EBC_PASSWORD": "password"},
    )

    points = source.fetch_points(["ID1", "ID2"], date(2026, 7, 13), date(2026, 7, 13))

    assert [(point.indicator_key, point.value) for point in points] == [("ID1", 700.5), ("ID2", None)]
    assert all(point.business_date == date(2026, 7, 13) for point in points)
    assert all(len(point.payload_sha256) == 64 for point in points)
    assert len(session.calls) == 2
    query_call = session.calls[1][1]
    assert query_call["json"]["indexCodes"] == ["ID1", "ID2"]
    assert query_call["headers"]["accessToken"] == "temporary-token"


def test_ebc_retries_transient_request_failure_then_succeeds():
    delays = []
    session = FakeSession(
        [
            requests.Timeout("temporary timeout"),
            FakeResponse({"success": True, "code": "200", "data": {"accessToken": "token"}}),
        ]
    )
    source = EbcBasisSource(
        session=session,
        environ={"EBC_ACCOUNT": "account", "EBC_PASSWORD": "password"},
        sleep_func=delays.append,
    )

    assert source.login() == "token"
    assert len(session.calls) == 2
    assert delays == [3]


def test_ebc_does_not_retry_unauthorized_response():
    session = FakeSession([FakeResponse(status_code=401)])
    source = EbcBasisSource(
        session=session,
        environ={"EBC_ACCOUNT": "account", "EBC_PASSWORD": "password"},
        sleep_func=lambda _seconds: None,
    )

    with pytest.raises(BasisSourceError) as exc:
        source.login()

    assert exc.value.code == "http_error"
    assert exc.value.stage == "ebc_login"
    assert exc.value.http_status == 401
    assert len(session.calls) == 1


@pytest.mark.parametrize("status_code", [429, 503])
def test_ebc_retries_retryable_http_statuses(status_code):
    delays = []
    session = FakeSession(
        [
            FakeResponse(status_code=status_code),
            FakeResponse({"success": True, "code": "200", "data": {"accessToken": "token"}}),
        ]
    )
    source = EbcBasisSource(
        session=session,
        environ={"EBC_ACCOUNT": "account", "EBC_PASSWORD": "password"},
        sleep_func=delays.append,
    )

    assert source.login() == "token"
    assert len(session.calls) == 2
    assert delays == [3]


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (FakeResponse(ValueError("not json")), "invalid_response"),
        (FakeResponse({"success": False, "code": "800001", "data": None}), "login_rejected"),
        (
            FakeResponse(
                status_error=requests.HTTPError("unauthorized")
            ),
            "http_error",
        ),
    ],
)
def test_ebc_login_reports_structured_errors(response, expected_code):
    source = EbcBasisSource(
        session=FakeSession([response]),
        environ={"EBC_ACCOUNT": "account", "EBC_PASSWORD": "password"},
    )

    with pytest.raises(BasisSourceError) as exc:
        source.login()

    assert exc.value.code == expected_code
    assert "password" not in str(exc.value).lower()


def test_sina_i0_parses_jsonp_and_filters_requested_range():
    response = FakeResponse(
        text='var _i0=([{"d":"2026-07-12","c":"740"},{"d":"2026-07-13","c":"744.5"},{"d":"2026-07-14","c":"746"}])'
    )
    source = SinaI0Source(http_get=lambda *args, **kwargs: response)

    closes = source.fetch_closes(date(2026, 7, 13), date(2026, 7, 14))

    assert closes == {date(2026, 7, 13): 744.5, date(2026, 7, 14): 746.0}


def test_sina_i0_distinguishes_http_failure_from_empty_data():
    failing = FakeResponse(status_error=requests.Timeout("timeout"))
    source = SinaI0Source(http_get=lambda *args, **kwargs: failing)

    with pytest.raises(BasisSourceError) as exc:
        source.fetch_closes(date(2026, 7, 13), date(2026, 7, 14))

    assert exc.value.code == "http_error"


def test_sina_i0_retries_transient_request_failure_then_succeeds():
    responses = [
        requests.ConnectionError("temporary disconnect"),
        FakeResponse(text='var _i0=([{"d":"2026-07-13","c":"744.5"}])'),
    ]
    delays = []

    def get_next(*_args, **_kwargs):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    source = SinaI0Source(http_get=get_next, sleep_func=delays.append)

    assert source.fetch_closes(date(2026, 7, 13), date(2026, 7, 13)) == {
        date(2026, 7, 13): 744.5
    }
    assert delays == [3]
