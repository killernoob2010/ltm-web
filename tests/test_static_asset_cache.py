import asyncio
from types import SimpleNamespace

from backend.app.main import (
    INDEX_CACHE_CONTROL,
    STATIC_CACHE_CONTROL,
    api_performance_log,
)


class _Response:
    def __init__(self):
        self.headers = {}


def test_index_and_static_assets_use_explicit_cache_policy():
    async def call_next(_request):
        return _Response()

    async def check(path, expected):
        request = SimpleNamespace(
            headers={},
            url=SimpleNamespace(path=path),
        )
        response = await api_performance_log(request, call_next)
        assert response.headers["Cache-Control"] == expected
        assert response.headers["x-request-id"]

    asyncio.run(check("/", INDEX_CACHE_CONTROL))
    asyncio.run(check("/static/app.js", STATIC_CACHE_CONTROL))
