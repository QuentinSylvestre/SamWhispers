"""Text post-processing between transcription and cleanup."""

from __future__ import annotations

import logging
import re

from samwhispers.config import PostprocessConfig, _TRAILING_MAP

log = logging.getLogger("samwhispers")


class TextPostprocessor:
    """Apply configurable text transformations to raw transcription output."""

    def __init__(self, config: PostprocessConfig) -> None:
        self._config = config

    def normalize(self, text: str) -> str:
        """Collapse newlines, whitespace, and trim. Run before cleanup."""
        if self._config.collapse_newlines:
            text = text.replace("\n", " ")

        if self._config.collapse_whitespace:
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
