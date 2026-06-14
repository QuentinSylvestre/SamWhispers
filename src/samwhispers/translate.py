"""Optional AI translation of dictated text via OpenAI or Anthropic.

Reuses the provider and credentials configured in ``[cleanup]`` so there is a
single place to manage API keys. Like cleanup, any failure degrades gracefully
by returning the original text.
"""

from __future__ import annotations

import logging

import httpx

from samwhispers.config import LANGUAGE_NAMES, CleanupConfig, TranslationConfig

log = logging.getLogger("samwhispers")


def _system_prompt(target_language: str) -> str:
    target = LANGUAGE_NAMES.get(target_language, target_language)
    return (
        f"Translate the user's text into {target}. Preserve meaning, tone, and "
        "formatting. Return only the translation, with no explanations or quotes."
    )


class Translator:
    """Translate text into a target language via the configured AI provider."""

    def __init__(self, config: TranslationConfig, cleanup_config: CleanupConfig) -> None:
        self._config = config
        self._provider = cleanup_config
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
        )

    def translate(self, text: str) -> str:
        """Translate text. Returns original on any failure or if disabled/empty."""
        if not self._config.enabled or not text.strip():
            return text
        try:
            if self._provider.provider == "openai":
                return self._openai_translate(text)
            return self._anthropic_translate(text)
        except Exception:
            log.exception("Translation failed, returning original text")
            return text

    def _openai_translate(self, text: str) -> str:
        cfg = self._provider.openai
        if not cfg.api_key:
            log.warning("OpenAI API key is empty, skipping translation")
            return text
        resp = self._client.post(
            f"{cfg.api_base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            json={
                "model": cfg.model,
                "messages": [
                    {"role": "system", "content": _system_prompt(self._config.target_language)},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.0,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices")
        if not choices or not isinstance(choices, list):
            log.warning("Unexpected OpenAI response shape, returning original")
            return text
        return str(choices[0].get("message", {}).get("content", text)).strip()

    def _anthropic_translate(self, text: str) -> str:
        cfg = self._provider.anthropic
        if not cfg.api_key:
            log.warning("Anthropic API key is empty, skipping translation")
            return text
        resp = self._client.post(
            f"{cfg.api_base.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": cfg.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": cfg.model,
                "max_tokens": 1024,
                "system": _system_prompt(self._config.target_language),
                "messages": [{"role": "user", "content": text}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content")
        if not content or not isinstance(content, list):
            log.warning("Unexpected Anthropic response shape, returning original")
            return text
        return str(content[0].get("text", text)).strip()

    def close(self) -> None:
        self._client.close()
