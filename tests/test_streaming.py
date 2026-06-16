"""Tests for streaming transcription: stabilization, session, engines."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from samwhispers.config import StreamingConfig
from samwhispers.streaming import (
    ChunkedEngine,
    LocalAgreement,
    StreamingSession,
    _detect_repetition,
    _norm,
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


def test_commit_all_never_shrinks() -> None:
    """Finding #2: commit_all must not shrink committed list."""
    la = LocalAgreement()
    la.update(["a", "b", "c"])
    la.update(["a", "b", "c"])  # commits a b c
    assert la.committed == ["a", "b", "c"]
    # finalize with fewer words -> should return empty, keep committed
    assert la.commit_all(["a", "b"]) == []
    assert la.committed == ["a", "b", "c"]


def test_empty_update_preserves_prev() -> None:
    """Finding #13: empty hypothesis should not erase agreement memory."""
    la = LocalAgreement()
    la.update(["hello", "world"])
    la.update([])  # empty -> should be a no-op
    # Next non-empty should still agree with the original prev
    assert la.update(["hello", "world", "now"]) == ["hello", "world"]


def test_agreement_with_word_offset() -> None:
    """Finding #1: window trimming passes word_offset to align positions."""
    la = LocalAgreement()
    # First tick: full audio, 5 words
    la.update(["one", "two", "three", "four", "five"])
    # Second tick: agrees on "one", "two", "three" (full overlap)
    la.update(["one", "two", "three", "four", "five"])
    assert la.committed == ["one", "two", "three", "four", "five"]

    # Now simulate window trim: next hypothesis only covers words from offset 3
    # (positions 3,4,5 = "four", "five", "six")
    la2 = LocalAgreement()
    la2.update(["a", "b", "c", "d", "e"])
    la2.update(["a", "b", "c", "d", "e"])  # commits a b c d e
    # Windowed hypothesis starts at word_offset=3, only has ["d", "e", "f"]
    result = la2.update(["d", "e", "f"], word_offset=3)
    # Should not commit anything new since positions must align
    # (committed already has 5 words, next comparison starts at abs_i=5)
    assert result == []


def test_agreement_word_offset_extends_committed() -> None:
    """Word offset correctly extends committed prefix after windowing."""
    la = LocalAgreement()
    # Build up committed to 2 words
    la.update(["hello", "world", "how"])
    la.update(["hello", "world", "how", "are"])  # commits hello world how
    assert la.committed == ["hello", "world", "how"]

    # Now window trims to offset=1, hypothesis = ["world", "how", "are", "you"]
    # abs_start = 3 (len(committed)), cur_local = 3-1=2 -> words[2]="are"
    # prev was ["hello", "world", "how", "are"], prev_offset=0, prev_local=3-0=3 -> prev[3]="are"
    # "are" == "are" -> commit "are"
    result = la.update(["world", "how", "are", "you"], word_offset=1)
    assert result == ["are"]
    assert la.committed == ["hello", "world", "how", "are"]


# --- Normalization ----------------------------------------------------------


def test_norm_strips_edge_punctuation_only() -> None:
    """Finding #15: _norm should strip edges, not internal punctuation."""
    assert _norm("don't") == "don't"
    assert _norm("Hello,") == "hello"
    assert _norm(",hello,") == "hello"
    assert _norm("...") == "..."  # all-punct stays as-is (lowercased)


def test_norm_preserves_word_identity() -> None:
    """'don't' and 'dont' should NOT be considered equal."""
    assert _norm("don't") != _norm("dont")


# --- Hallucination detection ------------------------------------------------


def test_detect_repetition_catches_loop() -> None:
    """Finding #7: detect repeating phrases as hallucination."""
    words = ["hello", "world", "hello", "world", "hello", "world"]
    assert _detect_repetition(words, min_repeat=3) is True


def test_detect_repetition_allows_normal_text() -> None:
    words = ["the", "quick", "brown", "fox", "jumps", "over"]
    assert _detect_repetition(words, min_repeat=3) is False


def test_detect_repetition_single_word_loop() -> None:
    words = ["um", "um", "um", "um"]
    assert _detect_repetition(words, min_repeat=3) is True


def test_detect_repetition_short_text_no_false_positive() -> None:
    words = ["hi", "hi"]
    assert _detect_repetition(words, min_repeat=3) is False


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


def test_session_finalize_shorter_than_committed() -> None:
    """Finding #2: finalize with shorter hypothesis doesn't corrupt output."""
    committed: list[str] = []
    session = StreamingSession(
        ScriptedEngine(["a b c", "a b c", "a b"]),  # finalize returns "a b" (shorter)
        16000,
        on_commit=lambda w: committed.extend(w),
    )
    audio = np.zeros(10, dtype=np.float32)
    session.tick(audio)  # a b c
    session.tick(audio)  # a b c -> commits a b c
    assert committed == ["a", "b", "c"]
    # finalize returns "a b" which is shorter -> commit_all guards against shrink
    final = session.finalize(audio)
    # final should be the committed words (not the shorter hypothesis)
    assert final == "a b c"
    assert committed == ["a", "b", "c"]  # no extra commits


def test_session_hallucination_rejected() -> None:
    """Finding #7: session skips hypothesis with repetition loops."""
    committed: list[str] = []
    session = StreamingSession(
        ScriptedEngine([
            "hello world",
            "hello world hello world hello world",  # hallucination loop
            "hello world how are you",
        ]),
        16000,
        on_commit=lambda w: committed.extend(w),
    )
    audio = np.zeros(10, dtype=np.float32)
    session.tick(audio)  # normal
    session.tick(audio)  # hallucination -> skipped
    session.tick(audio)  # normal, agrees with tick 1 on "hello world"
    assert "hello" in committed
    assert "world" in committed


def test_session_window_applies() -> None:
    """Verify window trimming works and passes word_offset."""
    committed: list[str] = []
    session = StreamingSession(
        ScriptedEngine(["the quick brown fox"]),
        16000,
        window_seconds=1.0,  # 16000 samples max
        on_commit=lambda w: committed.extend(w),
    )
    # Audio longer than window_seconds -> should be trimmed
    audio = np.zeros(32000, dtype=np.float32)  # 2 seconds
    session.tick(audio)
    # First tick commits nothing (no prev), but window was applied
    assert committed == []


def test_session_cancel_stops_tick() -> None:
    """Finding #4: cancelled session returns empty from tick."""
    session = StreamingSession(
        ScriptedEngine(["hello world"]),
        16000,
    )
    session.cancel()
    result = session.tick(np.zeros(10, dtype=np.float32))
    assert result == ""


def test_session_finalize_caps_audio() -> None:
    """Finding #3: finalize caps audio to window_seconds."""
    transcribed_sizes: list[int] = []

    class SizeTrackingEngine:
        def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
            transcribed_sizes.append(audio.size)
            return "hello"

    session = StreamingSession(
        SizeTrackingEngine(),
        16000,
        window_seconds=2.0,  # 32000 samples max
    )
    # Feed 5 seconds of audio (80000 samples) to finalize
    audio = np.zeros(80000, dtype=np.float32)
    session.finalize(audio)
    # Should have been capped to 32000
    assert transcribed_sizes[0] == 32000


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
    client.prompt = ""
    assert isinstance(make_engine(cfg, client), ChunkedEngine)


def test_make_engine_passes_prompt_to_faster_whisper() -> None:
    """Finding #8: faster-whisper engine receives vocabulary/accent prompt."""
    cfg = StreamingConfig(engine="faster_whisper", model="tiny")
    client = MagicMock()
    client.language = "en"
    client.prompt = "RSSI BLE Bluetooth"
    # Can't actually instantiate FasterWhisperEngine without the dep,
    # but we can verify make_engine passes the prompt through
    from samwhispers.streaming import FasterWhisperEngine
    import unittest.mock as mock

    with mock.patch.object(FasterWhisperEngine, "__init__", return_value=None) as init_mock:
        make_engine(cfg, client)
        init_mock.assert_called_once_with("tiny", "int8", "en", "RSSI BLE Bluetooth")
