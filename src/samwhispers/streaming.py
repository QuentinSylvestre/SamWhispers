"""Streaming (continuous) transcription engines and prefix stabilization.

Two interchangeable engines produce a transcription of the audio captured so
far; a ``LocalAgreement`` stabilizer turns the noisy, ever-changing hypotheses
into a stable committed prefix plus a still-changing tail. A ``StreamingSession``
ties them together and emits committed words (output mode B) and/or a live
preview (output mode A).

Engines:
  - ChunkedEngine: re-decode the audio via the existing whisper.cpp server.
  - FasterWhisperEngine: decode with faster-whisper (optional dependency).
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

from samwhispers.audio import numpy_to_wav

if TYPE_CHECKING:
    from samwhispers.config import StreamingConfig
    from samwhispers.transcribe import WhisperClient

log = logging.getLogger("samwhispers.streaming")

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def split_words(text: str) -> list[str]:
    """Split a transcription into whitespace-delimited tokens (keeps punctuation)."""
    return text.split()


def _norm(word: str) -> str:
    """Normalize a word for agreement comparison (case/punctuation-insensitive)."""
    m = _WORD_RE.findall(word.lower())
    return "".join(m)


class LocalAgreement:
    """LocalAgreement-2 prefix stabilization over cumulative hypotheses.

    Each ``update`` receives the full hypothesis for all audio so far (a growing
    word list). A word is committed once two consecutive hypotheses agree on it,
    and committed words are never revised.
    """

    def __init__(self) -> None:
        self.committed: list[str] = []
        self._prev: list[str] = []

    def update(self, words: list[str]) -> list[str]:
        """Feed a new full hypothesis; return the words newly committed."""
        newly: list[str] = []
        i = len(self.committed)
        prev, cur = self._prev, words
        while i < len(prev) and i < len(cur) and _norm(prev[i]) == _norm(cur[i]):
            newly.append(cur[i])
            i += 1
        self.committed.extend(newly)
        self._prev = words
        return newly

    def commit_all(self, words: list[str]) -> list[str]:
        """Commit everything in ``words`` beyond the current prefix (used at finalize)."""
        tail = words[len(self.committed) :]
        self.committed = list(words)
        self._prev = list(words)
        return tail

    def pending(self, words: list[str]) -> list[str]:
        return words[len(self.committed) :]


class StreamingEngine(ABC):
    """Transcribes a buffer of mono float32 audio to text."""

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...

    def close(self) -> None:  # noqa: B027 - optional override
        """Release any resources (no-op by default)."""


class ChunkedEngine(StreamingEngine):
    """Re-decode audio via the existing whisper.cpp server (no new dependency)."""

    def __init__(self, client: WhisperClient) -> None:
        self._client = client

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if audio.size == 0:
            return ""
        return self._client.transcribe(numpy_to_wav(audio, sample_rate)).strip()


class FasterWhisperEngine(StreamingEngine):
    """Decode with faster-whisper (CTranslate2). Optional dependency."""

    def __init__(self, model: str, compute_type: str, language: str) -> None:
        from faster_whisper import WhisperModel  # type: ignore

        self._model = WhisperModel(model, compute_type=compute_type)
        self._language = None if language in ("", "auto") else language

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if audio.size == 0:
            return ""
        segments, _ = self._model.transcribe(
            audio.astype(np.float32), language=self._language, beam_size=1
        )
        return "".join(seg.text for seg in segments).strip()


def make_engine(config: StreamingConfig, whisper_client: WhisperClient) -> StreamingEngine:
    """Build the configured streaming engine."""
    if config.engine == "faster_whisper":
        return FasterWhisperEngine(config.model, config.compute_type, whisper_client.language)
    return ChunkedEngine(whisper_client)


class StreamingSession:
    """Drives an engine + stabilizer, emitting committed words and a preview.

    ``on_commit`` receives newly-stabilized words (output mode B / progressive).
    ``on_preview`` receives the full current hypothesis text (output mode A).
    """

    def __init__(
        self,
        engine: StreamingEngine,
        sample_rate: int,
        *,
        on_commit: Callable[[list[str]], None] | None = None,
        on_preview: Callable[[str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._sample_rate = sample_rate
        self._on_commit = on_commit
        self._on_preview = on_preview
        self.agreement = LocalAgreement()

    def tick(self, audio: np.ndarray) -> str:
        """Decode the current audio, stabilize, emit updates; return preview text."""
        words = split_words(self._engine.transcribe(audio, self._sample_rate))
        newly = self.agreement.update(words)
        if newly and self._on_commit is not None:
            self._on_commit(newly)
        preview = " ".join(words)
        if self._on_preview is not None:
            self._on_preview(preview)
        return preview

    def finalize(self, audio: np.ndarray) -> str:
        """Final decode: commit everything and return the full text."""
        words = split_words(self._engine.transcribe(audio, self._sample_rate))
        tail = self.agreement.commit_all(words)
        if tail and self._on_commit is not None:
            self._on_commit(tail)
        final = " ".join(words)
        if self._on_preview is not None:
            self._on_preview(final)
        return final
