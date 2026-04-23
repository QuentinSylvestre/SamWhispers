"""Optional AI text cleanup via OpenAI or Anthropic."""

from __future__ import annotations

import logging

import httpx

from samwhispers.config import CleanupConfig

log = logging.getLogger("samwhispers")

_SYSTEM_PROMPT = (
    "You are a text cleanup assistant. Fix grammar, punctuation, and capitalization "
    "in the following dictated text. When appropriate for readability, add paragraph "
    "breaks. Return only the corrected text, nothing else."
)


class CleanupProvider:
    """Clean up transcribed text via OpenAI or Anthropic APIs."""

    def __init__(self, config: CleanupConfig) -> None:
        self._config = config
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
        )

    def cleanup(self, text: str) -> str:
        """Clean up text. Returns original on any failure or if disabled."""
        if not self._config.enabled:
            return text
        try:
            if self._config.provider == "openai":
                return self._openai_cleanup(text)
            return self._anthropic_cleanup(text)
        except Exception:
            log.exception("Cleanup failed, returning original text")
            return text

    def _openai_cleanup(self, text: str) -> str:
        cfg = self._config.openai
        if not cfg.api_key:
            log.warning("OpenAI API key is empty, skipping cleanup")
            return text
        resp = self._client.post(
            f"{cfg.api_base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            json={
                "model": cfg.model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.0,
            },
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"]).strip()

    def _anthropic_cleanup(self, text: str) -> str:
        cfg = self._config.anthropic
        if not cfg.api_key:
            log.warning("Anthropic API key is empty, skipping cleanup")
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
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": text}],
            },
        )
        resp.raise_for_status()
        return str(resp.json()["content"][0]["text"]).strip()

    def close(self) -> None:
        self._client.close()
