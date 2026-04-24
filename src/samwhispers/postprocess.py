"""Text post-processing between transcription and cleanup."""

from __future__ import annotations

import logging
import re

from samwhispers.config import PostprocessConfig, _TRAILING_MAP

log = logging.getLogger("samwhispers")


class FillerRemover:
    """Remove filler words using word-boundary-anchored regex with elongation support."""

    def __init__(self, words: list[str]) -> None:
        self._pattern: re.Pattern[str] | None = None
        if words:
            alternatives = [self._build_pattern(w) for w in words]
            combined = "|".join(alternatives)
            # Match filler word anchored by non-word boundaries (prevents partial matches).
            # Use case-insensitive matching.
            self._pattern = re.compile(
                r"(?<!\w)(?:" + combined + r")(?!\w)",
                re.IGNORECASE,
            )

    @staticmethod
    def _build_pattern(word: str) -> str:
        """Build regex pattern allowing repeated characters.

        "euh" -> "e+u+h+"
        "pfff" -> "p+f+"  (collapse consecutive identical chars)
        "mmh" -> "m+h+"
        """
        parts: list[str] = []
        prev = ""
        for ch in word.lower():
            if ch == prev:
                continue  # skip consecutive duplicates, the + handles them
            if ch.isalpha():
                parts.append(re.escape(ch) + "+")
            else:
                parts.append(re.escape(ch))
            prev = ch
        return "".join(parts)

    def remove(self, text: str) -> str:
        """Remove filler words and clean up orphaned punctuation."""
        if not self._pattern:
            return text

        # Remove filler words
        text = self._pattern.sub("", text)

        # Clean orphaned punctuation left by filler removal.
        text = re.sub(r"^\s*,\s*", "", text)  # leading comma (filler at start of text)
        text = re.sub(r",\s*,", ",", text)  # double commas → single comma
        text = re.sub(r",\s+([.!?])", r"\1", text)  # comma before sentence-end punct
        # Note: double-space collapse is handled by normalize()'s collapse_spaces step

        return text


class TextPostprocessor:
    """Apply configurable text transformations to raw transcription output."""

    def __init__(self, config: PostprocessConfig, filler_words: list[str] | None = None) -> None:
        self._config = config
        self._filler_remover: FillerRemover | None = None
        if filler_words:
            self._filler_remover = FillerRemover(filler_words)

    def normalize(self, text: str) -> str:
        """Collapse newlines, remove fillers, collapse whitespace, and trim. Run before cleanup."""
        if self._config.collapse_newlines:
            text = text.replace("\n", " ")

        if self._filler_remover:
            text = self._filler_remover.remove(text)

        if self._config.collapse_spaces:
            text = re.sub(r" {2,}", " ", text)

        if self._config.trim:
            text = text.strip()

        return text

    def finalize(self, text: str) -> str:
        """Append trailing character. Run after cleanup."""
        if not text:
            return text

        trailing = _TRAILING_MAP[self._config.trailing]
        if trailing:
            text = text + trailing

        return text
