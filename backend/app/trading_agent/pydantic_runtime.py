"""Pydantic AI model factory for the controlled migration path."""
import asyncio
import ssl

from httpx2 import AsyncHTTPTransport, MockTransport, Timeout
from httpcore2 import AsyncConnectionPool
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


class _DeadlineChatModel(OpenAIChatModel):
    """Bound one non-streaming provider request, leaving the SDK loop intact."""

    async def request(self, messages, model_settings, model_request_parameters):
        async with asyncio.timeout(15):
            return await super().request(messages, model_settings, model_request_parameters)


def create_sdk_model(*, api_key: str, base_url: str, model_name: str, http_client):
    """Create an offline-constructible OpenAI-compatible Pydantic AI model."""
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("api_key must be provided by protected configuration")
    if not isinstance(base_url, str) or not base_url.startswith("https://"):
        raise ValueError("base_url must use HTTPS")
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError("model_name must be provided by protected configuration")
    if http_client is None:
        raise ValueError("http_client must be explicitly configured")
    if getattr(http_client, "trust_env", None) is not False:
        raise ValueError("http_client must disable environment proxies")
    if getattr(http_client, "follow_redirects", None) is not False:
        raise ValueError("http_client must disable redirects")
    # httpx2 2.12 has no public TLS accessor. Fail closed on unknown transports;
    # its exact MockTransport is permitted for the offline protocol tests.
    transports = [getattr(http_client, "_transport", None)]
    transports.extend(t for t in getattr(http_client, "_mounts", {}).values() if t is not None)
    for transport in transports:
        if type(transport) is MockTransport:
            continue
        if type(transport) is not AsyncHTTPTransport:
            raise ValueError("http_client transport certificate configuration is unknown")
        pool = getattr(transport, "_pool", None)
        context = getattr(pool, "_ssl_context", None)
        if (getattr(context, "verify_mode", None) != ssl.CERT_REQUIRED
                or getattr(context, "check_hostname", None) is not True):
            raise ValueError("http_client must enable certificate and hostname verification")
        if type(pool) is not AsyncConnectionPool or getattr(pool, "_proxy", None) is not None:
            raise ValueError("http_client must disable explicit proxies")

    openai_client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
        timeout=Timeout(15.0),
    )
    provider = OpenAIProvider(openai_client=openai_client)
    return _DeadlineChatModel(
        model_name,
        provider=provider,
        settings={
            "thinking": False,
            "max_tokens": 4096,
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )
