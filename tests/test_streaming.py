"""Tests for streaming transcription: stabilization, session, engines."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from samwhispers.config import StreamingConfig
from samwhispers.streaming import (
    ChunkedEngine,
    LocalAgreement,
    StreamingSession,
    make_engine,
    split_words,
)


class ScriptedEngine:
    """Engine that returns a preset transcription per tick."""

    def __init__(self, scripts: list[str]) -> None:
        self._scripts = scripts
        self._i = 0

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        text = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        return text


# --- LocalAgreement ---------------------------------------------------------


def test_agreement_commits_stable_prefix() -> None:
    la = LocalAgreement()
    assert la.update(["the", "quick"]) == []  # first hypothesis commits nothing
    # second hypothesis agrees on "the quick", commits them
    assert la.update(["the", "quick", "brown"]) == ["the", "quick"]
    assert la.committed == ["the", "quick"]


def test_agreement_waits_for_unstable_tail() -> None:
    la = LocalAgreement()
    la.update(["hello", "wold"])  # typo in tail
    # tail changes -> only the stable "hello" commits
    assert la.update(["hello", "world"]) == ["hello"]
    assert la.update(["hello", "world", "now"]) == ["world"]


def test_agreement_is_case_punctuation_insensitive() -> None:
    la = LocalAgreement()
    la.update(["Hello", "there"])
    # "Hello," vs "Hello" should still agree; committed keeps the newer form
    assert la.update(["Hello,", "there", "friend"]) == ["Hello,", "there"]


def test_agreement_never_uncommits_on_shorter_hypothesis() -> None:
    la = LocalAgreement()
    la.update(["a", "b", "c"])
    la.update(["a", "b", "c"])  # commits a b c
    assert la.committed == ["a", "b", "c"]
    assert la.update(["a"]) == []  # shorter revision doesn't remove commits
    assert la.committed == ["a", "b", "c"]


def test_commit_all_returns_tail() -> None:
    la = LocalAgreement()
    la.update(["one", "two"])
    la.update(["one", "two", "three"])  # commits one two
    assert la.commit_all(["one", "two", "three", "four"]) == ["three", "four"]
    assert la.committed == ["one", "two", "three", "four"]


# --- StreamingSession -------------------------------------------------------


def test_session_progressive_emits_committed_words() -> None:
    committed: list[str] = []
    session = StreamingSession(
        ScriptedEngine(["the", "the quick", "the quick brown"]),
        16000,
        on_commit=lambda w: committed.extend(w),
    )
    audio = np.zeros(10, dtype=np.float32)
    session.tick(audio)  # "the" -> nothing yet
    session.tick(audio)  # "the quick" -> commit "the"
    session.tick(audio)  # "the quick brown" -> commit "quick"
    assert committed == ["the", "quick"]


def test_session_finalize_flushes_tail_and_returns_full() -> None:
    committed: list[str] = []
    previews: list[str] = []
    session = StreamingSession(
        ScriptedEngine(["a b", "a b c", "a b c d"]),
        16000,
        on_commit=lambda w: committed.extend(w),
        on_preview=lambda t: previews.append(t),
    )
    audio = np.zeros(10, dtype=np.float32)
    session.tick(audio)  # a b
    session.tick(audio)  # a b c -> commit a b
    final = session.finalize(audio)  # a b c d -> tail c d
    assert final == "a b c d"
    assert committed == ["a", "b", "c", "d"]
    assert previews[-1] == "a b c d"


def test_session_preview_only_when_no_commit_callback() -> None:
    previews: list[str] = []
    session = StreamingSession(
        ScriptedEngine(["hello world"]),
        16000,
        on_preview=lambda t: previews.append(t),
    )
    session.tick(np.zeros(10, dtype=np.float32))
    assert previews == ["hello world"]


# --- Engines ----------------------------------------------------------------


def test_split_words() -> None:
    assert split_words("hello,  world") == ["hello,", "world"]
    assert split_words("") == []


def test_chunked_engine_uses_whisper_client() -> None:
    client = MagicMock()
    client.transcribe.return_value = "  decoded text  "
    engine = ChunkedEngine(client)
    out = engine.transcribe(np.ones(16000, dtype=np.float32), 16000)
    assert out == "decoded text"
    client.transcribe.assert_called_once()
    # was given WAV bytes
    assert isinstance(client.transcribe.call_args.args[0], bytes)


def test_chunked_engine_empty_audio_skips_call() -> None:
    client = MagicMock()
    engine = ChunkedEngine(client)
    assert engine.transcribe(np.zeros(0, dtype=np.float32), 16000) == ""
    client.transcribe.assert_not_called()


def test_make_engine_chunked() -> None:
    cfg = StreamingConfig(engine="chunked")
    client = MagicMock()
    client.language = "en"
    assert isinstance(make_engine(cfg, client), ChunkedEngine)
