"""Tests for main app orchestration."""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock, patch

import pytest

from samwhispers.app import SamWhispers, State
from samwhispers.config import AppConfig


def _make_app() -> SamWhispers:
    """Create app with all components mocked, bypassing WSL detection."""
    config = AppConfig()
    config.whisper.managed = False
    with (
        patch("samwhispers.app.AudioRecorder") as mock_rec,
        patch("samwhispers.app.WhisperClient") as mock_wc,
        patch("samwhispers.app.CleanupProvider") as mock_cp,
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
        app.recorder = mock_rec.return_value
        app.whisper = mock_wc.return_value
        app.cleanup = mock_cp.return_value
    # Replace injector and hotkey_listener with mocks after init
    app.injector = MagicMock()
    app.hotkey_listener = MagicMock()
    return app


def test_initial_state_idle() -> None:
    """App starts in IDLE state."""
    app = _make_app()
    assert app._state == State.IDLE


def test_record_start_transitions_to_recording() -> None:
    """on_record_start transitions from IDLE to RECORDING."""
    app = _make_app()
    app._on_record_start()
    assert app._state == State.RECORDING
    app.recorder.start.assert_called_once()


def test_record_start_ignored_when_busy() -> None:
    """on_record_start is ignored when not IDLE."""
    app = _make_app()
    app._state = State.PROCESSING
    app._on_record_start()
    assert app._state == State.PROCESSING
    app.recorder.start.assert_not_called()


def test_record_stop_transitions_to_processing() -> None:
    """on_record_stop transitions from RECORDING to PROCESSING."""
    app = _make_app()
    app._state = State.RECORDING
    app.recorder.stop.return_value = b"fake-wav"
    app._on_record_stop()
    assert app._state == State.PROCESSING
    assert app._work_queue.qsize() == 1


def test_record_stop_ignored_when_not_recording() -> None:
    """on_record_stop is ignored when not RECORDING."""
    app = _make_app()
    app._on_record_stop()
    assert app._state == State.IDLE
    app.recorder.stop.assert_not_called()


def test_inject_failure_resumes_hotkey_listener() -> None:
    """Hotkey listener is resumed even when inject() raises."""
    import pytest

    app = _make_app()
    app.whisper.transcribe.return_value = "hello"
    app.cleanup.cleanup.return_value = "hello"
    app.injector.inject.side_effect = RuntimeError("clipboard crash")

    wav_bytes = b"\x00" * 20000
    with pytest.raises(RuntimeError, match="clipboard crash"):
        app._process_recording(wav_bytes)

    app.hotkey_listener.suppress.assert_called_once()
    app.hotkey_listener.resume.assert_called_once()


def test_process_recording_full_pipeline() -> None:
    """Full pipeline: transcribe -> cleanup -> inject."""
    app = _make_app()
    app.whisper.transcribe.return_value = "hello world"
    app.cleanup.cleanup.return_value = "Hello, world."

    wav_bytes = b"\x00" * 20000
    app._process_recording(wav_bytes)

    app.whisper.transcribe.assert_called_once_with(wav_bytes)
    app.cleanup.cleanup.assert_called_once_with("hello world")
    app.hotkey_listener.suppress.assert_called_once()
    app.injector.inject.assert_called_once_with("Hello, world.\n")
    app.hotkey_listener.resume.assert_called_once()


def test_process_recording_short_skipped() -> None:
    """Short recording is skipped."""
    app = _make_app()
    app._process_recording(b"tiny")
    app.whisper.transcribe.assert_not_called()


def test_process_recording_empty_transcription_skipped() -> None:
    """Empty transcription is skipped."""
    app = _make_app()
    app.whisper.transcribe.return_value = "   "
    app._process_recording(b"\x00" * 20000)
    app.cleanup.cleanup.assert_not_called()
    app.injector.inject.assert_not_called()


def test_process_loop_handles_exception() -> None:
    """Pipeline error is caught, state returns to IDLE."""
    app = _make_app()
    app.whisper.transcribe.side_effect = RuntimeError("boom")
    app._state = State.PROCESSING
    app._work_queue.put(b"\x00" * 20000)

    app._shutdown_event.clear()

    def run_loop() -> None:
        app._process_loop()

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()

    import time

    time.sleep(0.3)
    app._shutdown_event.set()
    t.join(timeout=2)

    assert app._state == State.IDLE


def test_shutdown_closes_all() -> None:
    """Shutdown closes all components."""
    app = _make_app()
    app.shutdown()
    app.hotkey_listener.stop.assert_called_once()
    app.recorder.close.assert_called_once()
    app.whisper.close.assert_called_once()
    app.cleanup.close.assert_called_once()


def test_concurrent_hotkey_rejected() -> None:
    """Second hotkey press while processing is rejected."""
    app = _make_app()
    app._state = State.PROCESSING
    app._on_record_start()
    assert app._state == State.PROCESSING
    app.recorder.start.assert_not_called()


def test_cycle_language_changes_whisper_language() -> None:
    """Language cycling updates the whisper client language."""
    config = AppConfig()
    config.whisper.languages = ["auto", "en", "fr"]
    with (
        patch("samwhispers.app.AudioRecorder"),
        patch("samwhispers.app.WhisperClient"),
        patch("samwhispers.app.CleanupProvider"),
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
        app.injector = MagicMock()
        app.hotkey_listener = MagicMock()

    with patch("samwhispers.notify.notify"):
        app._cycle_language()
        assert app.whisper.language == "en"
        assert app._lang_index == 1

        app._cycle_language()
        assert app.whisper.language == "fr"
        assert app._lang_index == 2


def test_cycle_language_wraps_around() -> None:
    """Language cycling wraps back to the first language."""
    config = AppConfig()
    config.whisper.languages = ["auto", "en"]
    with (
        patch("samwhispers.app.AudioRecorder"),
        patch("samwhispers.app.WhisperClient"),
        patch("samwhispers.app.CleanupProvider"),
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
        app.injector = MagicMock()
        app.hotkey_listener = MagicMock()

    with patch("samwhispers.notify.notify"):
        app._cycle_language()  # auto -> en
        app._cycle_language()  # en -> auto (wrap)
        assert app._lang_index == 0
        assert app.whisper.language == "auto"


def test_cycle_language_ignored_when_busy() -> None:
    """Language cycling is ignored when not IDLE."""
    config = AppConfig()
    config.whisper.languages = ["auto", "en"]
    with (
        patch("samwhispers.app.AudioRecorder"),
        patch("samwhispers.app.WhisperClient"),
        patch("samwhispers.app.CleanupProvider"),
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
        app.injector = MagicMock()
        app.hotkey_listener = MagicMock()

    app._state = State.RECORDING
    with patch("samwhispers.notify.notify") as mock_notify:
        app._cycle_language()
        mock_notify.assert_not_called()
        assert app._lang_index == 0  # unchanged


def test_single_language_no_cycle_wired() -> None:
    """Single-language config does not wire language cycle callback."""
    config = AppConfig()
    config.whisper.languages = ["en"]
    with (
        patch("samwhispers.app.AudioRecorder"),
        patch("samwhispers.app.WhisperClient"),
        patch("samwhispers.app.CleanupProvider"),
        patch("samwhispers.wsl.is_wsl", return_value=False),
        patch("samwhispers.hotkeys.HotkeyListener") as mock_hl,
    ):
        SamWhispers(config)
        call_kwargs = mock_hl.call_args[1]
        assert call_kwargs["language_key_str"] is None
        assert call_kwargs["on_language_cycle"] is None


def test_shutdown_stops_server_manager_before_whisper_close() -> None:
    """Shutdown calls server_manager.stop() before whisper.close()."""
    app = _make_app()
    app._server_manager = MagicMock()
    call_order: list[str] = []
    app._server_manager.stop.side_effect = lambda: call_order.append("server_stop")
    app.whisper.close.side_effect = lambda: call_order.append("whisper_close")
    app.shutdown()
    assert call_order == ["server_stop", "whisper_close"]


def test_whisper_client_receives_shutdown_event() -> None:
    """WhisperClient is constructed with the app's shutdown event."""
    config = AppConfig()
    config.whisper.managed = False
    with (
        patch("samwhispers.app.AudioRecorder"),
        patch("samwhispers.app.WhisperClient") as mock_wc,
        patch("samwhispers.app.CleanupProvider"),
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
        kwargs = mock_wc.call_args[1]
        assert kwargs["shutdown_event"] is app._shutdown_event


def test_startup_checks_fatal_when_whisper_unreachable() -> None:
    """Non-managed whisper server unreachable raises SystemExit."""
    import pytest

    app = _make_app()
    app.whisper.health_check.return_value = False
    app.recorder.start = MagicMock()  # prevent real audio init
    with pytest.raises(SystemExit):
        app._startup_checks()


def test_auto_stop_transitions_to_processing() -> None:
    """on_auto_stop transitions from RECORDING to PROCESSING and enqueues WAV."""
    app = _make_app()
    app._state = State.RECORDING
    wav_bytes = b"\x00" * 20000
    app._on_auto_stop(wav_bytes)
    assert app._state == State.PROCESSING
    assert app._work_queue.qsize() == 1
    assert app._work_queue.get_nowait() == wav_bytes


def test_auto_stop_ignored_when_not_recording() -> None:
    """on_auto_stop is ignored when not in RECORDING state."""
    app = _make_app()
    app._state = State.IDLE
    app._on_auto_stop(b"\x00" * 20000)
    assert app._state == State.IDLE
    assert app._work_queue.qsize() == 0


def test_startup_checks_fatal_when_managed_server_fails() -> None:
    """Managed whisper-server startup failure raises SystemExit."""
    import pytest

    from samwhispers.server import WhisperServerManager

    app = _make_app()
    mock_manager = MagicMock(spec=WhisperServerManager)
    mock_manager.start.side_effect = RuntimeError("whisper-server exited immediately")
    app._server_manager = mock_manager

    with pytest.raises(SystemExit):
        app._startup_checks()
    mock_manager.start.assert_called_once()


# --- Phase 1: Vocabulary prompt tests ---


def test_build_vocab_prompt_global_only() -> None:
    """Global words with language=auto returns comma-joined global words."""
    app = _make_app()
    app.config.vocabulary.words = ["RSSI", "pynput"]
    app.config.whisper.accent = ""
    app.whisper.language = "auto"
    assert app._build_prompt() == "RSSI, pynput"


def test_build_vocab_prompt_with_language() -> None:
    """Global + per-language words are merged when language matches."""
    app = _make_app()
    app.config.vocabulary.words = ["RSSI"]
    app.config.vocabulary.languages = {"fr": ["BLE"]}
    app.config.whisper.accent = ""
    app.whisper.language = "fr"
    assert app._build_prompt() == "RSSI, BLE"


def test_build_vocab_prompt_auto_language() -> None:
    """Language=auto uses only global words, not per-language."""
    app = _make_app()
    app.config.vocabulary.words = ["RSSI"]
    app.config.vocabulary.languages = {"fr": ["BLE"]}
    app.config.whisper.accent = ""
    app.whisper.language = "auto"
    assert app._build_prompt() == "RSSI"


def test_build_vocab_prompt_empty() -> None:
    """No vocabulary returns empty string."""
    app = _make_app()
    app.config.whisper.accent = ""
    app.whisper.language = "auto"
    assert app._build_prompt() == ""


def test_build_vocab_prompt_deduplicates() -> None:
    """Duplicate words (case-insensitive) are deduplicated."""
    app = _make_app()
    app.config.vocabulary.words = ["RSSI"]
    app.config.vocabulary.languages = {"en": ["RSSI"]}
    app.config.whisper.accent = ""
    app.whisper.language = "en"
    assert app._build_prompt() == "RSSI"


def test_vocab_prompt_updates_on_language_cycle() -> None:
    """Prompt rebuilds when language is cycled."""
    config = AppConfig()
    config.whisper.languages = ["auto", "fr"]
    config.whisper.managed = False
    config.vocabulary.words = ["RSSI"]
    config.vocabulary.languages = {"fr": ["BLE"]}
    with (
        patch("samwhispers.app.AudioRecorder"),
        patch("samwhispers.app.WhisperClient"),
        patch("samwhispers.app.CleanupProvider"),
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
        app.injector = MagicMock()
        app.hotkey_listener = MagicMock()
        # After init, whisper is a mock; set language to match initial
        app.whisper.language = "auto"

    # Initial prompt should be global only
    assert app._build_prompt() == "RSSI"

    with patch("samwhispers.notify.notify"):
        app._cycle_language()
        # _cycle_language() should have assigned the prompt to whisper
        assert app.whisper.prompt == "RSSI, BLE"


def test_build_vocab_prompt_warns_on_large_list(caplog: pytest.LogCaptureFixture) -> None:
    """Vocabulary >100 words logs a warning."""
    app = _make_app()
    app.config.vocabulary.words = [f"word{i}" for i in range(101)]
    app.config.whisper.accent = ""
    app.whisper.language = "auto"
    with caplog.at_level(logging.WARNING, logger="samwhispers"):
        app._build_prompt()
    assert any("101 words" in r.message for r in caplog.records)


def test_filler_word_list_with_builtins() -> None:
    """Default config (filler enabled, use_builtins=True) produces a FillerRemover."""
    app = _make_app()
    assert app.postprocessor._filler_remover is not None


def test_filler_word_list_disabled() -> None:
    """filler.enabled=False produces no FillerRemover."""
    config = AppConfig()
    config.whisper.managed = False
    config.filler.enabled = False
    with (
        patch("samwhispers.app.AudioRecorder"),
        patch("samwhispers.app.WhisperClient"),
        patch("samwhispers.app.CleanupProvider"),
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
    assert app.postprocessor._filler_remover is None


def test_filler_word_list_custom_with_builtins_dedup() -> None:
    """Custom words + builtins are merged and deduplicated."""
    config = AppConfig()
    config.whisper.managed = False
    config.filler.enabled = True
    config.filler.use_builtins = True
    config.filler.words = ["um", "hum"]  # "um" overlaps with builtin
    with (
        patch("samwhispers.app.AudioRecorder"),
        patch("samwhispers.app.WhisperClient"),
        patch("samwhispers.app.CleanupProvider"),
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
    remover = app.postprocessor._filler_remover
    assert remover is not None
    # "hum" should be in the list, "um" should appear only once
    # Verify by testing removal
    assert remover.remove("hum okay") == " okay"
    # "um" from builtins should still work
    assert remover.remove("um okay") == " okay"


# --- Accent bias prompt tests ---


def test_build_prompt_accent_only() -> None:
    """Accent with no vocabulary produces accent prompt."""
    app = _make_app()
    app.config.whisper.accent = "fr"
    app.config.whisper.accent_prompt = ""
    app.whisper.language = "en"
    assert app._build_prompt() == "The speaker has a French accent."


def test_build_prompt_accent_suppressed_when_language_matches() -> None:
    """Accent prompt is suppressed when active language matches accent code."""
    app = _make_app()
    app.config.whisper.accent = "fr"
    app.config.whisper.accent_prompt = ""
    app.whisper.language = "fr"
    assert app._build_prompt() == ""


def test_build_prompt_accent_with_vocabulary() -> None:
    """Accent prompt is appended after vocabulary words."""
    app = _make_app()
    app.config.vocabulary.words = ["RSSI"]
    app.config.whisper.accent = "fr"
    app.config.whisper.accent_prompt = ""
    app.whisper.language = "en"
    assert app._build_prompt() == "RSSI The speaker has a French accent."


def test_build_prompt_accent_prompt_override() -> None:
    """Custom accent_prompt overrides the generic template."""
    app = _make_app()
    app.config.whisper.accent = "fr"
    app.config.whisper.accent_prompt = "Custom accent text"
    app.whisper.language = "en"
    assert app._build_prompt() == "Custom accent text"


def test_build_prompt_accent_suppressed_on_cycle() -> None:
    """Accent prompt disappears when cycling to matching language, reappears on cycle back."""
    config = AppConfig()
    config.whisper.languages = ["en", "fr"]
    config.whisper.managed = False
    config.whisper.accent = "fr"
    config.whisper.accent_prompt = ""
    with (
        patch("samwhispers.app.AudioRecorder"),
        patch("samwhispers.app.WhisperClient"),
        patch("samwhispers.app.CleanupProvider"),
        patch("samwhispers.wsl.is_wsl", return_value=False),
    ):
        app = SamWhispers(config)
        app.injector = MagicMock()
        app.hotkey_listener = MagicMock()
        app.whisper.language = "en"

    # English active, accent=fr -> accent prompt present
    assert app._build_prompt() == "The speaker has a French accent."

    with patch("samwhispers.notify.notify"):
        app._cycle_language()  # en -> fr
        # Accent matches language -> suppressed
        assert app.whisper.prompt == ""

        app._cycle_language()  # fr -> en (wrap)
        # Accent != language -> reappears
        assert app.whisper.prompt == "The speaker has a French accent."


def test_build_prompt_accent_auto_language() -> None:
    """Accent prompt is included when language is auto (auto != accent code)."""
    app = _make_app()
    app.config.whisper.accent = "fr"
    app.config.whisper.accent_prompt = ""
    app.whisper.language = "auto"
    assert app._build_prompt() == "The speaker has a French accent."


def test_build_prompt_no_accent() -> None:
    """No accent set behaves identically to vocabulary-only prompt."""
    app = _make_app()
    app.config.vocabulary.words = ["RSSI"]
    app.config.whisper.accent = ""
    app.config.whisper.accent_prompt = ""
    app.whisper.language = "auto"
    assert app._build_prompt() == "RSSI"


def test_build_prompt_accent_prompt_override_with_vocabulary() -> None:
    """Custom accent_prompt combined with vocabulary produces both parts."""
    app = _make_app()
    app.config.vocabulary.words = ["RSSI"]
    app.config.whisper.accent = "fr"
    app.config.whisper.accent_prompt = "Custom accent text"
    app.whisper.language = "en"
    assert app._build_prompt() == "RSSI Custom accent text"


# --- Token budget validation tests ---


def test_startup_prompt_too_long_exits() -> None:
    """Very long prompt causes SystemExit at startup."""
    import pytest

    app = _make_app()
    app.config.whisper.accent = "fr"
    app.config.whisper.accent_prompt = "x" * 1000  # >900 chars -> >224 tokens
    app.whisper.language = "en"
    app.whisper.health_check.return_value = True
    app.injector.check_clipboard_available.return_value = True
    with (
        patch("samwhispers.notify.check_notify_available", return_value=True),
        patch("samwhispers.notify.notify"),
        pytest.raises(SystemExit),
    ):
        app._startup_checks()


def test_startup_prompt_warning_near_limit(caplog: pytest.LogCaptureFixture) -> None:
    """Prompt near the token limit logs a warning but does not exit."""
    import logging

    app = _make_app()
    app.config.whisper.accent = "fr"
    # ~750 chars -> ~187 tokens (above 180 warning threshold, below 224 error)
    app.config.whisper.accent_prompt = "x" * 750
    app.whisper.language = "en"
    app.whisper.health_check.return_value = True
    app.injector.check_clipboard_available.return_value = True
    with (
        patch("samwhispers.notify.check_notify_available", return_value=True),
        patch("samwhispers.notify.notify"),
        caplog.at_level(logging.WARNING),
    ):
        app._startup_checks()
    assert "approaching token limit" in caplog.text
