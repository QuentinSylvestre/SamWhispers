"""Tests for AI cleanup module."""

from __future__ import annotations

import httpx
import respx

from samwhispers.cleanup import CleanupProvider
from samwhispers.config import AnthropicConfig, CleanupConfig, OpenAIConfig


def _make_config(
    enabled: bool = True,
    provider: str = "openai",
    openai_key: str = "sk-test",
    anthropic_key: str = "ant-test",
) -> CleanupConfig:
    return CleanupConfig(
        enabled=enabled,
        provider=provider,
        openai=OpenAIConfig(api_key=openai_key),
        anthropic=AnthropicConfig(api_key=anthropic_key),
    )


def test_disabled_passthrough() -> None:
    """Disabled cleanup returns text unchanged."""
    provider = CleanupProvider(_make_config(enabled=False))
    assert provider.cleanup("hello world") == "hello world"
    provider.close()


@respx.mock
def test_openai_request_format() -> None:
    """OpenAI cleanup sends correct request and parses response."""
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Hello, world."}}]},
        )
    )
    provider = CleanupProvider(_make_config(provider="openai"))
    result = provider.cleanup("hello world")
    assert result == "Hello, world."
    assert route.call_count == 1
    # Verify auth header
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer sk-test"
    provider.close()


@respx.mock
def test_anthropic_request_format() -> None:
    """Anthropic cleanup sends correct headers and body format."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={"content": [{"text": "Hello, world."}]},
        )
    )
    provider = CleanupProvider(_make_config(provider="anthropic"))
    result = provider.cleanup("hello world")
    assert result == "Hello, world."
    req = route.calls[0].request
    assert req.headers["x-api-key"] == "ant-test"
    assert req.headers["anthropic-version"] == "2023-06-01"
    provider.close()


@respx.mock
def test_api_error_fallback() -> None:
    """API error returns original text."""
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    provider = CleanupProvider(_make_config(provider="openai"))
    result = provider.cleanup("hello world")
    assert result == "hello world"
    provider.close()


def test_missing_key_fallback() -> None:
    """Missing API key returns original text with warning."""
    provider = CleanupProvider(_make_config(provider="openai", openai_key=""))
    result = provider.cleanup("hello world")
    assert result == "hello world"
    provider.close()
