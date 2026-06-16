"""Whisper server HTTP client."""

from __future__ import annotations

import logging
import threading
import time

import httpx

from samwhispers.exceptions import ShutdownRequested, StreamingUnavailableError
from samwhispers.streaming import PUNCT_ONLY_RE, TranscribeResult, WordTimestamp

log = logging.getLogger("samwhispers")

_RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout)


class WhisperClient:
    """POST audio to whisper-server's /inference endpoint."""

    def __init__(
        self, server_url: str, language: str = "auto", shutdown_event: threading.Event | None = None
    ) -> None:
        self._language = language
        self._prompt: str = ""
        self._shutdown_event = shutdown_event
        self._client = httpx.Client(
            base_url=server_url.rstrip("/"),
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0),
        )

    @property
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        self._language = value

    @property
    def prompt(self) -> str:
        return self._prompt

    @prompt.setter
    def prompt(self, value: str) -> None:
        self._prompt = value

    def transcribe(self, wav_bytes: bytes) -> str:
        """Send WAV audio to /inference and return transcription text."""
        return self._post_with_retry(wav_bytes, retries=1, backoff=1.0)

    def transcribe_verbose(self, wav_bytes: bytes) -> TranscribeResult:
        """Send WAV audio with verbose_json format and return structured result with timestamps."""
        data: dict[str, str] = {
            "temperature": "0.0",
            "response_format": "verbose_json",
            "language": self._language,
        }
        if self._prompt:
            data["prompt"] = self._prompt

        resp = self._client.post(
            "/inference",
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data=data,
        )
        resp.raise_for_status()
        try:
            result = resp.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Whisper server returned non-JSON response (status {resp.status_code})"
            ) from exc

        text = str(result.get("text", "")).strip()

        # Parse word timestamps from segments
        words: list[WordTimestamp] = []
        segments = result.get("segments", [])
        for seg in segments:
            seg_words = seg.get("words")
            if not seg_words:
                continue
            for w in seg_words:
                start = w.get("start")
                end = w.get("end")
                # Validate numeric timestamps
                if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                    raise StreamingUnavailableError(
                        "Word timestamps missing or non-numeric; "
                        "ensure whisper-server is not using --no-timestamps"
                    )
                word_str = str(w.get("word", "")).strip()
                if not word_str:
                    continue
                # Skip zero-duration punctuation-only tokens
                if start == end and PUNCT_ONLY_RE.match(word_str):
                    continue
                words.append(WordTimestamp(word=word_str, start=float(start), end=float(end)))

        # If no words found at all in a non-empty transcription, timestamps are unavailable
        if text and not words:
            raise StreamingUnavailableError(
                "Verbose JSON response has no word timestamps; "
                "ensure whisper-server supports word-level output"
            )

        return TranscribeResult(text=text, words=words)

    def _post_with_retry(self, wav_bytes: bytes, retries: int, backoff: float) -> str:
        last_exc: Exception | None = None
        for attempt in range(1 + retries):
            try:
                data: dict[str, str] = {
                    "temperature": "0.0",
                    "response_format": "json",
                    "language": self._language,
                }
                if self._prompt:
                    data["prompt"] = self._prompt

                resp = self._client.post(
                    "/inference",
                    files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                    data=data,
                )
                if resp.status_code >= 500:
                    last_exc = httpx.HTTPStatusError(
                        f"Server error {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                    if attempt < retries:
                        log.warning(
                            "Whisper server %d, retrying in %.0fs", resp.status_code, backoff
                        )
                        self._interruptible_sleep(backoff)
                        continue
                    raise last_exc
                resp.raise_for_status()
                try:
                    result = resp.json()
                except ValueError as exc:
                    raise RuntimeError(
                        f"Whisper server returned non-JSON response (status {resp.status_code})"
                    ) from exc
                return str(result.get("text", "")).strip()
            except _RETRYABLE as exc:
                last_exc = exc
                if attempt < retries:
                    log.warning("Whisper server unreachable, retrying in %.0fs", backoff)
                    self._interruptible_sleep(backoff)
                    continue
        raise last_exc if last_exc else RuntimeError("Transcription failed")

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep that can be interrupted by the shutdown event."""
        if self._shutdown_event is not None:
            if self._shutdown_event.wait(seconds):
                raise ShutdownRequested("Shutdown requested during retry")
        else:
            time.sleep(seconds)

    def health_check(self) -> bool:
        """GET / returns 200 when whisper-server is ready."""
        try:
            resp = self._client.get("/")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        self._client.close()
