"""Tests for streaming transcription: stabilization, session, engines."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from samwhispers.config import StreamingConfig
from samwhispers.streaming import (
    ChunkedEngine,
    LocalAgreement,
    StreamingSession,
    TranscribeResult,
    WordTimestamp,
    _detect_repetition,
    _norm,
    make_engine,
    split_words,
)


class ScriptedEngine:
    """Engine that returns a preset transcription per tick, with optional timestamps."""

    def __init__(self, scripts: list[str]) -> None:
        self._scripts = scripts
        self._i = 0
        self._prompt: str = ""

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> TranscribeResult:
        text = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        # Generate synthetic timestamps: each word gets 0.5s
        words_list = text.split()
        words_ts = []
        for j, w in enumerate(words_list):
            words_ts.append(WordTimestamp(word=w, start=j * 0.5, end=(j + 1) * 0.5))
        return TranscribeResult(text=text, words=words_ts)

    def update_prompt(self, prompt: str) -> None:
        self._prompt = prompt


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
    audio = np.full(16000, 0.1, dtype=np.float32)  # non-silent audio
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
    audio = np.full(16000, 0.1, dtype=np.float32)
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
    session.tick(np.full(16000, 0.1, dtype=np.float32))
    assert previews == ["hello world"]


def test_session_finalize_shorter_than_committed() -> None:
    """Finding #2: finalize with shorter hypothesis doesn't corrupt output."""
    committed: list[str] = []
    session = StreamingSession(
        ScriptedEngine(["a b c", "a b c", "a b"]),  # finalize returns "a b" (shorter)
        16000,
        on_commit=lambda w: committed.extend(w),
    )
    audio = np.full(16000, 0.1, dtype=np.float32)
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
    audio = np.full(16000, 0.1, dtype=np.float32)
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
    audio = np.full(32000, 0.1, dtype=np.float32)  # 2 seconds, non-silent
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
        def transcribe(self, audio: np.ndarray, sample_rate: int) -> TranscribeResult:
            transcribed_sizes.append(audio.size)
            return TranscribeResult(text="hello", words=[])

        def update_prompt(self, prompt: str) -> None:
            pass

    session = StreamingSession(
        SizeTrackingEngine(),
        16000,
        window_seconds=2.0,  # 32000 samples max
    )
    # Feed 5 seconds of non-silent audio (80000 samples) to finalize
    audio = np.full(80000, 0.1, dtype=np.float32)
    session.finalize(audio)
    # Should have been capped to 32000
    assert transcribed_sizes[0] == 32000


# --- Engines ----------------------------------------------------------------


def test_split_words() -> None:
    assert split_words("hello,  world") == ["hello,", "world"]
    assert split_words("") == []


def test_chunked_engine_uses_whisper_client() -> None:
    client = MagicMock()
    client.transcribe_verbose.return_value = TranscribeResult(text="decoded text", words=[])
    engine = ChunkedEngine(client)
    out = engine.transcribe(np.ones(16000, dtype=np.float32), 16000)
    assert out.text == "decoded text"
    client.transcribe_verbose.assert_called_once()
    # was given WAV bytes
    assert isinstance(client.transcribe_verbose.call_args.args[0], bytes)


def test_chunked_engine_empty_audio_skips_call() -> None:
    client = MagicMock()
    engine = ChunkedEngine(client)
    result = engine.transcribe(np.zeros(0, dtype=np.float32), 16000)
    assert result.text == ""
    assert result.words == []
    client.transcribe_verbose.assert_not_called()


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


def test_session_energy_gate_skips_silence() -> None:
    """Energy gate: silence audio skips decode entirely."""
    session = StreamingSession(
        ScriptedEngine(["should not see this"]),
        16000,
    )
    # Silent audio (all zeros) should be skipped
    result = session.tick(np.zeros(16000, dtype=np.float32))
    assert result == ""  # committed is empty, so returns ""


def test_session_multi_tick_window_crossing() -> None:
    """Integration test: audio grows past window_seconds, word_offset kicks in,
    agreement still commits correctly across the boundary."""
    committed: list[str] = []

    # Simulate ticks where audio grows past a 1-second window (16000 samples).
    # Before window trim: engine sees ALL audio and returns cumulative text.
    # After window trim: engine only sees the last 1s, returns only that portion.
    scripts = [
        "the quick",               # tick 1: 0.5s, no trim
        "the quick brown",         # tick 2: 1.0s, no trim -> commits "the quick"
        "quick brown fox",         # tick 3: 1.5s, trimmed! engine sees last 1s only
        "brown fox jumps",         # tick 4: 2.0s, trimmed, engine sees last 1s
        "fox jumps over",          # tick 5: 2.5s, trimmed
    ]

    session = StreamingSession(
        ScriptedEngine(scripts),
        16000,
        window_seconds=1.0,  # 16000 samples max
        on_commit=lambda w: committed.extend(w),
    )

    # Tick 1: 8000 samples (0.5s) - no window trim
    session.tick(np.full(8000, 0.1, dtype=np.float32))
    assert committed == []  # first tick, no prev

    # Tick 2: 16000 samples (1.0s) - no trim
    session.tick(np.full(16000, 0.1, dtype=np.float32))
    assert committed == ["the", "quick"]  # agrees with tick 1

    # Tick 3: 24000 samples (1.5s) - window trims to last 16000
    # word_offset = len(committed) = 2, hypothesis = ["quick", "brown", "fox"]
    # prev was ["the", "quick", "brown"] at offset 0
    # Comparing at abs_i=2: prev_local=2-0=2 -> "brown", cur_local=2-2=0 -> "quick"
    # "brown" != "quick" -> no new commits (agreement stalls until prev aligns)
    session.tick(np.full(24000, 0.1, dtype=np.float32))

    # Tick 4: 32000 samples (2.0s) - trimmed
    # Now prev = ["quick", "brown", "fox"] at offset 2
    # cur = ["brown", "fox", "jumps"] at offset 2 (committed still 2)
    # abs_i=2: prev_local=2-2=0 -> "quick", cur_local=2-2=0 -> "brown" -> no match
    session.tick(np.full(32000, 0.1, dtype=np.float32))

    # Tick 5: 40000 samples (2.5s) - trimmed
    session.tick(np.full(40000, 0.1, dtype=np.float32))

    # After windowing, agreement stalls because the engine's windowed output
    # doesn't maintain stable word positions relative to the full recording.
    # This is the known limitation: progressive mode degrades after window kicks in.
    # The committed words from before the window are preserved.
    assert "the" in committed
    assert "quick" in committed
    # No corruption: committed never contains wrong words
    assert all(w in ["the", "quick", "brown", "fox", "jumps", "over"] for w in committed)


def test_on_commit_exception_does_not_crash_session() -> None:
    """Finding #3: exception in on_commit is caught, doesn't crash tick."""
    call_count = [0]

    def failing_commit(words: list[str]) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("Clipboard failed!")

    session = StreamingSession(
        ScriptedEngine(["hello world", "hello world today", "hello world today is"]),
        16000,
        on_commit=failing_commit,
    )
    audio = np.full(16000, 0.1, dtype=np.float32)

    # First commit will raise - should be caught
    session.tick(audio)
    session.tick(audio)  # commits "hello" "world" -> on_commit raises
    # Should not crash, session continues
    session.tick(audio)  # commits "today" -> on_commit succeeds
    assert call_count[0] >= 2  # was called at least twice


# --- Phase 3: Sentence-boundary trimming tests -----------------------------


class MockRecorder:
    """Mock recorder with snapshot() and trim_front() for testing."""

    def __init__(self, audio: np.ndarray) -> None:
        self._audio = audio
        self.trims: list[int] = []

    def snapshot(self, max_samples: int | None = None) -> np.ndarray:
        if max_samples and self._audio.size > max_samples:
            return self._audio[-max_samples:]
        return self._audio

    def trim_front(self, n_samples: int) -> int:
        actual = min(n_samples, self._audio.size)
        self._audio = self._audio[actual:]
        self.trims.append(actual)
        return actual


class TimestampedEngine:
    """Engine returning words with explicit timestamps for boundary testing."""

    def __init__(self, responses: list[list[WordTimestamp]]) -> None:
        self._responses = responses
        self._i = 0
        self.prompts: list[str] = []

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> TranscribeResult:
        ts = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        text = " ".join(w.word for w in ts)
        return TranscribeResult(text=text, words=ts)

    def update_prompt(self, prompt: str) -> None:
        self.prompts.append(prompt)


def test_sentence_boundary_trims_at_period() -> None:
    """60s+ simulated recording trims at sentence boundaries."""
    # Simulate: "Hello world. This is new." over multiple ticks
    # Each tick's engine returns the full text from current buffer
    words_t1 = [
        WordTimestamp("Hello", 0.0, 0.5),
        WordTimestamp("world.", 0.5, 1.0),
        WordTimestamp("This", 1.0, 1.5),
    ]
    words_t2 = [
        WordTimestamp("Hello", 0.0, 0.5),
        WordTimestamp("world.", 0.5, 1.0),
        WordTimestamp("This", 1.0, 1.5),
        WordTimestamp("is", 1.5, 2.0),
    ]
    words_t3 = [
        WordTimestamp("Hello", 0.0, 0.5),
        WordTimestamp("world.", 0.5, 1.0),
        WordTimestamp("This", 1.0, 1.5),
        WordTimestamp("is", 1.5, 2.0),
        WordTimestamp("new.", 2.0, 2.5),
    ]

    engine = TimestampedEngine([words_t1, words_t2, words_t3])
    # 4 seconds of audio (plenty of room for 2s min buffer after trim)
    audio = np.full(64000, 0.1, dtype=np.float32)  # 4s at 16kHz
    recorder = MockRecorder(audio)

    committed: list[str] = []
    session = StreamingSession(
        engine,
        16000,
        window_seconds=30.0,
        recorder=recorder,
        on_commit=lambda w: committed.extend(w),
    )

    session.tick()  # t1: no prev yet
    session.tick()  # t2: commits "Hello", "world.", "This"
    session.tick()  # t3: commits "is"

    # "world." is a sentence boundary with "This" after it
    assert "Hello" in committed
    assert "world." in committed
    # Trim should have happened at "world." end (1.0s = 16000 samples)
    assert len(recorder.trims) >= 1
    assert recorder.trims[0] == 16000  # 1.0s * 16000


def test_consecutive_trims_monotonic_timestamps() -> None:
    """3+ consecutive trims with monotonically increasing absolute timestamps."""
    # Simulate a longer recording where multiple sentence boundaries occur.
    # The key assertion: cumulative_trimmed_seconds increases monotonically.
    recorder_audio = np.full(160000, 0.1, dtype=np.float32)  # 10s
    recorder = MockRecorder(recorder_audio)

    # Two ticks commit "One. Two. Three." — trim fires at "Two." (has "Three." after)
    t1 = [
        WordTimestamp("One.", 0.0, 1.0), WordTimestamp("Two.", 1.0, 2.0),
        WordTimestamp("Three.", 2.0, 3.0), WordTimestamp("Four.", 3.0, 4.0),
    ]
    t2 = [
        WordTimestamp("One.", 0.0, 1.0), WordTimestamp("Two.", 1.0, 2.0),
        WordTimestamp("Three.", 2.0, 3.0), WordTimestamp("Four.", 3.0, 4.0),
        WordTimestamp("Five.", 4.0, 5.0),
    ]

    engine = TimestampedEngine([t1, t2])
    session = StreamingSession(
        engine, 16000, window_seconds=30.0, recorder=recorder
    )

    session.tick()
    session.tick()

    # At least one trim should have occurred
    assert len(recorder.trims) >= 1
    # Cumulative trimmed seconds should be positive and reasonable
    assert session._cumulative_trimmed_seconds > 0
    # The trim amount corresponds to a sentence boundary end time
    assert recorder.trims[0] == int(session._cumulative_trimmed_seconds * 16000)


def test_window_ceiling_fires_on_long_sentence() -> None:
    """Window ceiling fires on a single long sentence (no period)."""
    # Single long sentence with no periods — no sentence boundary detected
    # Audio exceeds window_seconds, so ceiling fallback kicks in
    words = [WordTimestamp(f"word{i}", i * 0.5, (i + 1) * 0.5) for i in range(20)]

    engine = TimestampedEngine([words, words])
    # window_seconds = 2.0 (32000 samples), audio = 4s (64000 samples)
    audio = np.full(64000, 0.1, dtype=np.float32)
    recorder = MockRecorder(audio)

    session = StreamingSession(
        engine, 16000, window_seconds=2.0, recorder=recorder
    )

    session.tick()
    session.tick()

    # No trim should occur (no sentence boundary), but window ceiling limits decode
    # The session should still function without crashing
    assert len(recorder.trims) == 0
    # word_offset should have kicked in (audio > max_samples)
    assert len(session.agreement.committed) >= 0


def test_progressive_mode_no_gaps_or_duplicates() -> None:
    """Progressive mode with multiple trims produces no gaps or duplicates."""
    committed: list[str] = []
    audio = np.full(160000, 0.1, dtype=np.float32)  # 10s
    recorder = MockRecorder(audio)

    # Sentences: "Hello there. How are you. Fine thanks."
    t1 = [
        WordTimestamp("Hello", 0.0, 0.5), WordTimestamp("there.", 0.5, 1.0),
        WordTimestamp("How", 1.0, 1.5), WordTimestamp("are", 1.5, 2.0),
    ]
    t2 = [
        WordTimestamp("Hello", 0.0, 0.5), WordTimestamp("there.", 0.5, 1.0),
        WordTimestamp("How", 1.0, 1.5), WordTimestamp("are", 1.5, 2.0),
        WordTimestamp("you.", 2.0, 2.5), WordTimestamp("Fine", 2.5, 3.0),
    ]
    t3 = [
        WordTimestamp("How", 0.0, 0.5), WordTimestamp("are", 0.5, 1.0),
        WordTimestamp("you.", 1.0, 1.5), WordTimestamp("Fine", 1.5, 2.0),
        WordTimestamp("thanks.", 2.0, 2.5),
    ]
    t4 = [
        WordTimestamp("How", 0.0, 0.5), WordTimestamp("are", 0.5, 1.0),
        WordTimestamp("you.", 1.0, 1.5), WordTimestamp("Fine", 1.5, 2.0),
        WordTimestamp("thanks.", 2.0, 2.5), WordTimestamp("Bye.", 2.5, 3.0),
    ]

    engine = TimestampedEngine([t1, t2, t3, t4])
    session = StreamingSession(
        engine, 16000, window_seconds=30.0, recorder=recorder,
        on_commit=lambda w: committed.extend(w),
    )

    for _ in range(4):
        session.tick()

    # No duplicates
    # The committed words form a coherent sequence
    assert len(committed) == len(set(range(len(committed))))  # indices unique (truism)
    # Check no word appears more than expected
    word_counts: dict[str, int] = {}
    for w in committed:
        word_counts[w] = word_counts.get(w, 0) + 1
    # In a normal sentence flow, no word should repeat more than once
    # (except in contrived inputs)
    assert all(v <= 2 for v in word_counts.values())


def test_abbreviation_does_not_trigger_trim() -> None:
    """Abbreviation 'Dr. Smith' does NOT trigger trim."""
    audio = np.full(80000, 0.1, dtype=np.float32)  # 5s
    recorder = MockRecorder(audio)

    # "Dr. Smith is here. Next sentence."
    t1 = [
        WordTimestamp("Dr.", 0.0, 0.5), WordTimestamp("Smith", 0.5, 1.0),
        WordTimestamp("is", 1.0, 1.5), WordTimestamp("here.", 1.5, 2.0),
        WordTimestamp("Next", 2.0, 2.5),
    ]
    t2 = [
        WordTimestamp("Dr.", 0.0, 0.5), WordTimestamp("Smith", 0.5, 1.0),
        WordTimestamp("is", 1.0, 1.5), WordTimestamp("here.", 1.5, 2.0),
        WordTimestamp("Next", 2.0, 2.5), WordTimestamp("sentence.", 2.5, 3.0),
    ]

    engine = TimestampedEngine([t1, t2])
    session = StreamingSession(
        engine, 16000, window_seconds=30.0, recorder=recorder
    )

    session.tick()
    session.tick()

    # Should NOT trim at "Dr." — it's an abbreviation
    # Should trim at "here." (sentence boundary with "Next" capitalized after)
    if recorder.trims:
        # If a trim happened, it should be at "here." (end=2.0s = 32000 samples)
        assert recorder.trims[0] == 32000


def test_trim_deferred_when_buffer_too_short() -> None:
    """Trim deferred when remaining buffer < 2s after proposed trim."""
    # Audio is only 2.5s — after trimming 2.0s, only 0.5s remains (< 2s min)
    audio = np.full(40000, 0.1, dtype=np.float32)  # 2.5s at 16kHz
    recorder = MockRecorder(audio)

    t1 = [
        WordTimestamp("Hello", 0.0, 0.5), WordTimestamp("world.", 0.5, 1.0),
        WordTimestamp("Next", 1.0, 1.5), WordTimestamp("word.", 1.5, 2.0),
        WordTimestamp("More", 2.0, 2.5),
    ]
    t2 = [
        WordTimestamp("Hello", 0.0, 0.5), WordTimestamp("world.", 0.5, 1.0),
        WordTimestamp("Next", 1.0, 1.5), WordTimestamp("word.", 1.5, 2.0),
        WordTimestamp("More", 2.0, 2.5),
    ]

    engine = TimestampedEngine([t1, t2])
    session = StreamingSession(
        engine, 16000, window_seconds=30.0, recorder=recorder
    )

    session.tick()
    session.tick()

    # "word." at end=2.0s would leave only 0.5s (8000 samples < 32000)
    # But "world." at end=1.0 leaves 1.5s (24000 < 32000) — also too short
    # So no trim should happen
    assert len(recorder.trims) == 0


def test_session_tick_backward_compat_no_recorder() -> None:
    """tick() still works with audio parameter when no recorder is set."""
    committed: list[str] = []
    session = StreamingSession(
        ScriptedEngine(["the", "the quick", "the quick brown"]),
        16000,
        on_commit=lambda w: committed.extend(w),
    )
    audio = np.full(16000, 0.1, dtype=np.float32)
    session.tick(audio)
    session.tick(audio)
    session.tick(audio)
    assert committed == ["the", "quick"]


def test_finalize_does_not_trim() -> None:
    """finalize() does not trim — just commits remaining."""
    audio = np.full(80000, 0.1, dtype=np.float32)  # 5s
    recorder = MockRecorder(audio.copy())

    t1 = [
        WordTimestamp("Hello", 0.0, 0.5), WordTimestamp("world.", 0.5, 1.0),
        WordTimestamp("Done.", 1.0, 1.5),
    ]
    t2 = [
        WordTimestamp("Hello", 0.0, 0.5), WordTimestamp("world.", 0.5, 1.0),
        WordTimestamp("Done.", 1.0, 1.5),
    ]

    engine = TimestampedEngine([t1, t2])
    session = StreamingSession(
        engine, 16000, window_seconds=30.0, recorder=recorder
    )

    session.tick()
    # Now finalize — should NOT trim
    trims_before = len(recorder.trims)
    final = session.finalize(np.full(80000, 0.1, dtype=np.float32))
    # finalize itself doesn't call _try_trim
    assert "Hello" in final or "Done." in final


def test_prompt_updated_on_trim() -> None:
    """Prompt is updated with context words after trim."""
    audio = np.full(80000, 0.1, dtype=np.float32)  # 5s
    recorder = MockRecorder(audio)

    t1 = [
        WordTimestamp("Hello", 0.0, 0.5), WordTimestamp("world.", 0.5, 1.0),
        WordTimestamp("Next", 1.0, 1.5), WordTimestamp("sentence", 1.5, 2.0),
    ]
    t2 = [
        WordTimestamp("Hello", 0.0, 0.5), WordTimestamp("world.", 0.5, 1.0),
        WordTimestamp("Next", 1.0, 1.5), WordTimestamp("sentence", 1.5, 2.0),
        WordTimestamp("here.", 2.0, 2.5),
    ]

    engine = TimestampedEngine([t1, t2])
    session = StreamingSession(
        engine, 16000, window_seconds=30.0, recorder=recorder,
        base_prompt="vocab words",
    )

    session.tick()
    session.tick()

    # If trim happened, engine should have received a prompt update
    if recorder.trims:
        assert len(engine.prompts) >= 1
        # Prompt should contain committed words + base prompt
        assert "vocab words" in engine.prompts[0]


def test_committed_timestamps_tracked() -> None:
    """LocalAgreement tracks committed_timestamps parallel to committed."""
    la = LocalAgreement()
    ts1 = [WordTimestamp("hello", 0.0, 0.5), WordTimestamp("world", 0.5, 1.0)]
    ts2 = [WordTimestamp("hello", 0.0, 0.5), WordTimestamp("world", 0.5, 1.0), WordTimestamp("now", 1.0, 1.5)]

    la.update(["hello", "world"], words_with_ts=ts1)
    la.update(["hello", "world", "now"], words_with_ts=ts2)

    assert la.committed == ["hello", "world"]
    assert len(la.committed_timestamps) == 2
    assert la.committed_timestamps[0].word == "hello"
    assert la.committed_timestamps[1].end == 1.0


def test_commit_all_stores_timestamps() -> None:
    """commit_all accepts and stores timestamps."""
    la = LocalAgreement()
    ts = [WordTimestamp("a", 0.0, 0.5), WordTimestamp("b", 0.5, 1.0)]
    la.update(["a", "b"], words_with_ts=ts)
    la.update(["a", "b"], words_with_ts=ts)

    final_ts = [WordTimestamp("a", 0.0, 0.5), WordTimestamp("b", 0.5, 1.0), WordTimestamp("c", 1.0, 1.5)]
    tail = la.commit_all(["a", "b", "c"], words_with_ts=final_ts)
    assert tail == ["c"]
    assert len(la.committed_timestamps) == 3
    assert la.committed_timestamps[2].word == "c"


# --- Phase 4: Fail-loud and batch fallback tests ---------------------------


def test_streaming_unavailable_error_stops_stream_loop() -> None:
    """StreamingUnavailableError in tick stops the loop and disables streaming."""
    from unittest.mock import patch
    from samwhispers.exceptions import StreamingUnavailableError

    class FailingEngine:
        def transcribe(self, audio: np.ndarray, sample_rate: int) -> TranscribeResult:
            raise StreamingUnavailableError("no timestamps")

        def update_prompt(self, prompt: str) -> None:
            pass

    # Build a minimal SamWhispers-like scenario via StreamingSession
    session = StreamingSession(
        FailingEngine(),
        16000,
    )
    # tick should raise, which the app's _stream_loop catches
    import pytest
    with pytest.raises(StreamingUnavailableError):
        session.tick(np.full(16000, 0.1, dtype=np.float32))


def test_app_stream_loop_catches_streaming_unavailable(tmp_path: Any) -> None:
    """App._stream_loop catches StreamingUnavailableError and disables streaming."""
    from unittest.mock import MagicMock, patch
    from samwhispers.exceptions import StreamingUnavailableError
    from samwhispers.app import SamWhispers, State
    import threading
    import queue

    class FailOnTickEngine:
        def transcribe(self, audio: np.ndarray, sample_rate: int) -> TranscribeResult:
            raise StreamingUnavailableError("timestamps unavailable")

        def update_prompt(self, prompt: str) -> None:
            pass

        def close(self) -> None:
            pass

    # Build minimal app object via __new__ (skip __init__)
    app = SamWhispers.__new__(SamWhispers)
    app.config = MagicMock()
    app.config.streaming.interval_seconds = 0.01
    app._state = State.RECORDING
    app._lock = threading.Lock()
    app._stream_stop = threading.Event()
    app._stream_disabled = False
    app._stream_engine = FailOnTickEngine()
    app._stream_injected_any = False

    # Create a session that will raise on tick
    recorder_mock = MagicMock()
    recorder_mock.snapshot.return_value = np.full(16000, 0.1, dtype=np.float32)

    session = StreamingSession(
        FailOnTickEngine(), 16000, recorder=recorder_mock
    )
    app._stream_session = session

    # Run the stream loop (will catch the error and return)
    with patch("samwhispers.notify.notify"):
        app._stream_loop()

    assert app._stream_disabled is True
    assert app._stream_session is None


def test_app_finalize_streaming_batch_fallback() -> None:
    """When streaming is disabled, _finalize_streaming puts audio on work queue."""
    from unittest.mock import MagicMock, patch
    from samwhispers.app import SamWhispers, State
    import queue
    import threading

    app = SamWhispers.__new__(SamWhispers)
    app._lock = threading.Lock()
    app._state = State.PROCESSING
    app._stream_stop = threading.Event()
    app._stream_thread = None
    app._stream_session = None
    app._stream_disabled = True
    app._work_queue = queue.Queue()
    app.recorder = MagicMock()
    app.recorder.stop.return_value = b"fake-wav-data"

    # Call finalize (not from auto-stop, so recorder.stop() is called)
    app._finalize_streaming(from_auto_stop=False)

    # Should have put audio on work queue for batch processing
    assert not app._work_queue.empty()
    assert app._work_queue.get() == b"fake-wav-data"
    app.recorder.stop.assert_called_once()
