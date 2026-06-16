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
import threading
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

from samwhispers.audio import numpy_to_wav

if TYPE_CHECKING:
    from samwhispers.config import StreamingConfig
    from samwhispers.transcribe import WhisperClient

log = logging.getLogger("samwhispers.streaming")

PUNCT_ONLY_RE = re.compile(r"^[^\w]+$", re.UNICODE)

# Minimum RMS energy to bother decoding (prevents hallucination on silence)
_MIN_ENERGY = 0.005


def split_words(text: str) -> list[str]:
    """Split a transcription into whitespace-delimited tokens (keeps punctuation)."""
    return text.split()


def _norm(word: str) -> str:
    """Normalize a word for agreement comparison.

    Unicode-normalizes (NFKC), strips leading/trailing punctuation, lowercases.
    Preserves internal apostrophes so "don't" != "do nt".
    """
    w = unicodedata.normalize("NFKC", word).lower()
    stripped = re.sub(r"^\W+|\W+$", "", w)
    return stripped if stripped else w


def _detect_repetition(words: list[str], min_repeat: int = 3) -> bool:
    """Detect Whisper hallucination loops (same phrase repeated N+ times at tail).

    Only checks the last 20 words to avoid false positives on legitimate
    repeated structure in longer text. Short phrases (1-4 words) use min_repeat=3;
    longer phrases (5+) are almost certainly real content and are skipped.
    """
    # Only examine the tail to reduce false positives on legitimate content
    tail = words[-20:] if len(words) > 20 else words
    n = len(tail)
    if n < min_repeat:
        return False
    # Only check phrase lengths 1-4 (hallucination loops are short phrases)
    max_phrase = min(4, n // min_repeat)
    for phrase_len in range(1, max_phrase + 1):
        tail_phrase = tail[-phrase_len:]
        repeats = 0
        for i in range(n - phrase_len, -1, -phrase_len):
            segment = tail[i : i + phrase_len]
            if [_norm(w) for w in segment] == [_norm(w) for w in tail_phrase]:
                repeats += 1
            else:
                break
        if repeats >= min_repeat:
            return True
    return False


class LocalAgreement:
    """LocalAgreement-2 prefix stabilization over cumulative hypotheses.

    Each ``update`` receives the full hypothesis for all audio so far (a growing
    word list). A word is committed once two consecutive hypotheses agree on it,
    and committed words are never revised.

    The ``word_offset`` parameter accounts for sliding-window trimming: when the
    engine only decodes the last N seconds, earlier words are no longer in the
    hypothesis. The offset tells the stabilizer where the hypothesis starts
    relative to the full recording's word timeline.
    """

    def __init__(self) -> None:
        self.committed: list[str] = []
        self._prev: list[str] = []
        self._prev_offset: int = 0

    def update(self, words: list[str], word_offset: int = 0) -> list[str]:
        """Feed a new hypothesis; return the words newly committed.

        ``word_offset`` is the number of words trimmed from the start due to
        windowing. Agreement comparison aligns by absolute position.
        """
        if not words:
            return []  # Preserve _prev from last non-empty hypothesis

        newly: list[str] = []
        commit_len = len(self.committed)

        # Align previous and current hypotheses by absolute word index
        prev_offset = self._prev_offset
        cur_offset = word_offset

        # Start comparing from just after committed prefix (absolute index)
        abs_start = commit_len

        for abs_i in range(abs_start, abs_start + len(words)):
            prev_local = abs_i - prev_offset
            cur_local = abs_i - cur_offset
            if cur_local < 0 or cur_local >= len(words):
                break
            if prev_local < 0 or prev_local >= len(self._prev):
                break
            if _norm(self._prev[prev_local]) == _norm(words[cur_local]):
                newly.append(words[cur_local])
            else:
                break

        self.committed.extend(newly)
        self._prev = words
        self._prev_offset = word_offset
        return newly

    def commit_all(self, words: list[str]) -> list[str]:
        """Commit everything in ``words`` beyond the current prefix (used at finalize).

        Never shrinks committed — if the final hypothesis is shorter than what
        was already committed, returns empty (the progressive output is already
        injected and cannot be retracted).
        """
        if len(words) <= len(self.committed):
            # Final decode produced fewer words; keep existing commits
            return []
        tail = words[len(self.committed) :]
        self.committed = list(words)
        self._prev = list(words)
        self._prev_offset = 0
        return tail

    def pending(self, words: list[str], word_offset: int = 0) -> list[str]:
        local_start = len(self.committed) - word_offset
        if local_start < 0:
            local_start = 0
        return words[local_start:]


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

    def __init__(
        self, model: str, compute_type: str, language: str, prompt: str = ""
    ) -> None:
        from faster_whisper import WhisperModel  # type: ignore

        self._model = WhisperModel(model, compute_type=compute_type)
        self._language = None if language in ("", "auto") else language
        self._prompt = prompt or None

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if audio.size == 0:
            return ""
        segments, _ = self._model.transcribe(
            audio.astype(np.float32),
            language=self._language,
            beam_size=1,
            initial_prompt=self._prompt,
        )
        return "".join(seg.text for seg in segments).strip()


def make_engine(config: StreamingConfig, whisper_client: WhisperClient) -> StreamingEngine:
    """Build the configured streaming engine."""
    if config.engine == "faster_whisper":
        return FasterWhisperEngine(
            config.model, config.compute_type, whisper_client.language, whisper_client.prompt
        )
    return ChunkedEngine(whisper_client)


class StreamingSession:
    """Drives an engine + stabilizer, emitting committed words and a preview.

    ``on_commit`` receives newly-stabilized words (output mode B / progressive).
    ``on_preview`` receives the full current hypothesis text (output mode A).

    Thread-safe: ``tick`` and ``finalize`` use an internal lock for state mutation.
    The engine transcription call (HTTP/compute) runs outside the lock to avoid
    blocking finalize behind a long-running tick.
    """

    def __init__(
        self,
        engine: StreamingEngine,
        sample_rate: int,
        *,
        window_seconds: float = 30.0,
        on_commit: Callable[[list[str]], None] | None = None,
        on_preview: Callable[[str], None] | None = None,
    ) -> None:
        self._engine = engine
        self._sample_rate = sample_rate
        self._window_seconds = window_seconds
        self._on_commit = on_commit
        self._on_preview = on_preview
        self._lock = threading.Lock()
        self.agreement = LocalAgreement()
        self._cancelled = False

    def cancel(self) -> None:
        """Signal that no more ticks should run (used when join times out)."""
        self._cancelled = True

    def tick(self, audio: np.ndarray) -> str:
        """Decode the current audio (windowed), stabilize, emit updates."""
        if self._cancelled:
            return ""

        max_samples = int(self._window_seconds * self._sample_rate)
        word_offset = 0
        if audio.size > max_samples:
            # Use committed count as the offset floor — these words are stable
            # and correspond to the trimmed portion of audio
            word_offset = len(self.agreement.committed)
            audio = audio[-max_samples:]

        # Energy gate: skip decode if recent audio is silence (prevents hallucination)
        check_samples = min(self._sample_rate, audio.size)
        rms = float(np.sqrt(np.mean(np.square(audio[-check_samples:], dtype=np.float64))))
        if rms < _MIN_ENERGY:
            return " ".join(self.agreement.committed)

        # Transcribe OUTSIDE the lock (pure function, no shared state)
        text = self._engine.transcribe(audio, self._sample_rate)
        words = split_words(text)

        # Acquire lock only for state mutation
        with self._lock:
            if self._cancelled:
                return ""

            # Hallucination guard: reject hypotheses with repetition loops
            if words and _detect_repetition(words):
                log.debug("Repetition loop detected, skipping hypothesis")
                return " ".join(self.agreement.committed)

            newly = self.agreement.update(words, word_offset)

        # Callbacks outside lock to avoid holding lock during IO
        if newly and self._on_commit is not None:
            try:
                self._on_commit(newly)
            except Exception:
                log.exception("on_commit callback failed (words lost: %s)", " ".join(newly))

        preview = " ".join(words)
        if self._on_preview is not None:
            try:
                self._on_preview(preview)
            except Exception:
                log.debug("on_preview callback failed", exc_info=True)
        return preview

    def finalize(self, audio: np.ndarray) -> str:
        """Final decode: commit everything and return the full text."""
        # Cap finalize audio to window_seconds to avoid server timeout
        max_samples = int(self._window_seconds * self._sample_rate)
        if audio.size > max_samples:
            audio = audio[-max_samples:]

        # Energy gate: if final audio is silence, use what we already have
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
        if rms < _MIN_ENERGY:
            with self._lock:
                return " ".join(self.agreement.committed)

        # Transcribe outside lock
        text = self._engine.transcribe(audio, self._sample_rate)
        words = split_words(text)

        with self._lock:
            # Hallucination guard on finalize too
            if words and _detect_repetition(words):
                log.debug("Repetition loop in finalize, using committed text")
                return " ".join(self.agreement.committed)

            tail = self.agreement.commit_all(words)

        # Callback outside lock
        if tail and self._on_commit is not None:
            try:
                self._on_commit(tail)
            except Exception:
                log.exception("on_commit callback failed in finalize (words lost: %s)", " ".join(tail))

        with self._lock:
            final = " ".join(self.agreement.committed)

        if self._on_preview is not None:
            try:
                self._on_preview(final)
            except Exception:
                log.debug("on_preview callback failed", exc_info=True)
        return final
