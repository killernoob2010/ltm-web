"""Pydantic AI model factory for the controlled migration path."""
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


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

    openai_client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
    )
    provider = OpenAIProvider(openai_client=openai_client)
    return OpenAIChatModel(
        model_name,
        provider=provider,
        settings={
            "thinking": False,
            "max_tokens": 4096,
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )
