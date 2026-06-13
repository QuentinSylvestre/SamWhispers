"""Tests for AI translation module."""

from __future__ import annotations

import httpx
import respx

from samwhispers.config import AnthropicConfig, CleanupConfig, OpenAIConfig, TranslationConfig
from samwhispers.translate import Translator, _system_prompt


def _cleanup(provider: str = "openai", openai_key: str = "sk-test") -> CleanupConfig:
    return CleanupConfig(
        provider=provider,
        openai=OpenAIConfig(api_key=openai_key),
        anthropic=AnthropicConfig(api_key="ant-test"),
    )


def _translator(enabled: bool = True, target: str = "fr", **kw: object) -> Translator:
    return Translator(
        TranslationConfig(enabled=enabled, target_language=target),
        _cleanup(**kw),  # type: ignore[arg-type]
    )


def test_disabled_passthrough() -> None:
    t = _translator(enabled=False)
    assert t.translate("hello") == "hello"
    t.close()


def test_empty_passthrough() -> None:
    t = _translator()
    assert t.translate("   ") == "   "
    t.close()


def test_system_prompt_names_target() -> None:
    assert "French" in _system_prompt("fr")
    assert "Japanese" in _system_prompt("ja")
    # Unknown code falls back to the code itself.
    assert "xx" in _system_prompt("xx")


@respx.mock
def test_openai_translate() -> None:
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "Bonjour"}}]})
    )
    t = _translator(target="fr", provider="openai")
    assert t.translate("hello") == "Bonjour"
    body = route.calls[0].request
    assert b"French" in body.content  # target language in the system prompt
    t.close()


@respx.mock
def test_anthropic_translate() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json={"content": [{"text": "Hola"}]})
    )
    t = _translator(target="es", provider="anthropic")
    assert t.translate("hello") == "Hola"
    t.close()


@respx.mock
def test_api_error_returns_original() -> None:
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="boom")
    )
    t = _translator(provider="openai")
    assert t.translate("hello") == "hello"
    t.close()


def test_missing_key_returns_original() -> None:
    t = _translator(provider="openai", openai_key="")
    assert t.translate("hello") == "hello"
    t.close()
