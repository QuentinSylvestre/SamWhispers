"""Main application orchestration."""

from __future__ import annotations

import enum
import logging
import queue
import signal
import threading
from types import FrameType
from typing import Any

from samwhispers.audio import AudioRecorder, min_wav_size
from samwhispers.cleanup import CleanupProvider
from samwhispers.config import AppConfig
from samwhispers.exceptions import ShutdownRequested
from samwhispers.postprocess import TextPostprocessor
from samwhispers.server import WhisperServerManager
from samwhispers.transcribe import WhisperClient

log = logging.getLogger("samwhispers")


class State(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"


class SamWhispers:
    """State-machine-driven voice-to-text daemon."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._state = State.IDLE
        self._lock = threading.Lock()
        self._work_queue: queue.Queue[bytes] = queue.Queue()
        self._shutdown_event = threading.Event()

        self._languages = config.whisper.languages
        self._lang_index = 0

        self.recorder = AudioRecorder(
            sample_rate=config.audio.sample_rate,
            max_duration=config.audio.max_duration,
            on_auto_stop=self._on_auto_stop,
        )
        self.whisper = WhisperClient(
            server_url=config.whisper.server_url,
            language=self._languages[0],
            shutdown_event=self._shutdown_event,
        )
        self.cleanup = CleanupProvider(config.cleanup)
        self.postprocessor = TextPostprocessor(config.postprocess)

        self.whisper.prompt = self._build_vocab_prompt()

        self._server_manager: WhisperServerManager | None = None
        if config.whisper.managed:
            self._server_manager = WhisperServerManager(config.whisper)

        # Language cycle params (only when multiple languages configured)
        lang_key = config.hotkey.language_key if len(self._languages) > 1 else None
        lang_cb = self._cycle_language if len(self._languages) > 1 else None

        # Select WSL or native backends -- typed as Any to allow either implementation
        self.injector: Any
        self.hotkey_listener: Any

        from samwhispers.wsl import is_wsl

        if is_wsl():
            log.info("WSL detected, using Windows interop for hotkeys and clipboard")
            from samwhispers.hotkeys import WSLHotkeyListener
            from samwhispers.inject import WSLTextInjector

            self.injector = WSLTextInjector(paste_delay=config.inject.paste_delay)
            self.hotkey_listener = WSLHotkeyListener(
                hotkey_str=config.hotkey.key,
                mode=config.hotkey.mode,
                on_start=self._on_record_start,
                on_stop=self._on_record_stop,
                language_key_str=lang_key,
                on_language_cycle=lang_cb,
            )
        else:
            from samwhispers.hotkeys import HotkeyListener
            from samwhispers.inject import TextInjector

            self.injector = TextInjector(paste_delay=config.inject.paste_delay)
            self.hotkey_listener = HotkeyListener(
                hotkey_str=config.hotkey.key,
                mode=config.hotkey.mode,
                on_start=self._on_record_start,
                on_stop=self._on_record_stop,
                language_key_str=lang_key,
                on_language_cycle=lang_cb,
            )

    def _build_vocab_prompt(self) -> str:
        """Build initial_prompt string from vocabulary config and current language."""
        words = list(self.config.vocabulary.words)
        lang = self.whisper.language
        if lang != "auto" and lang in self.config.vocabulary.languages:
            words.extend(self.config.vocabulary.languages[lang])
        if not words:
            return ""
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for w in words:
            wl = w.lower()
            if wl not in seen:
                seen.add(wl)
                unique.append(w)
        if len(unique) > 100:
            log.warning(
                "Vocabulary has %d words; initial_prompt token limit is ~150-200 words. "
                "Consider trimming the list.",
                len(unique),
            )
        return ", ".join(unique)

    def run(self) -> None:
        """Start daemon: checks, worker thread, hotkey listener, block until shutdown."""
        self._startup_checks()

        worker = threading.Thread(target=self._process_loop, daemon=True)
        worker.start()

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        self.hotkey_listener.start()
        log.info(
            "Ready. Listening for hotkey '%s' (mode=%s)...",
            self.config.hotkey.key,
            self.config.hotkey.mode,
        )
        # Use a polling loop instead of bare .wait() so that Ctrl+C
        # (SIGINT) is deliverable on Windows.  Event.wait(timeout) releases
        # the GIL and re-acquires it periodically, giving the interpreter a
        # chance to raise KeyboardInterrupt / run signal handlers.
        while not self._shutdown_event.wait(timeout=0.5):
            pass
        self.shutdown()

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:
        log.info("Received signal %d, shutting down", signum)
        self._shutdown_event.set()

    def _cycle_language(self) -> None:
        """Cycle to the next language in the configured list."""
        with self._lock:
            if self._state != State.IDLE:
                log.debug("Busy (%s), ignoring language cycle", self._state.value)
                return
        self._lang_index = (self._lang_index + 1) % len(self._languages)
        lang = self._languages[self._lang_index]
        self.whisper.language = lang
        self.whisper.prompt = self._build_vocab_prompt()
        label = "Auto-detect" if lang == "auto" else lang
        log.info("Language switched to: %s", label)
        from samwhispers.notify import notify

        notify("SamWhispers", f"Language: {label}")

    def _on_record_start(self) -> None:
        with self._lock:
            if self._state != State.IDLE:
                log.warning("Busy (%s), ignoring hotkey", self._state.value)
                return
            self._state = State.RECORDING
        try:
            self.recorder.start()
        except Exception:
            log.exception("Failed to start recording (no audio device?)")
            with self._lock:
                self._state = State.IDLE
            return
        log.info("Recording...")

    def _on_record_stop(self) -> None:
        with self._lock:
            if self._state != State.RECORDING:
                return
            self._state = State.PROCESSING
        wav_bytes = self.recorder.stop()
        self._work_queue.put(wav_bytes)

    def _on_auto_stop(self, wav_bytes: bytes) -> None:
        """Handle max-duration auto-stop by processing the recorded audio."""
        with self._lock:
            if self._state != State.RECORDING:
                return
            self._state = State.PROCESSING
        log.info("Auto-stop triggered, processing recorded audio")
        self._work_queue.put(wav_bytes)

    def _process_loop(self) -> None:
        """Worker thread: dequeue WAV bytes, transcribe, cleanup, inject."""
        while not self._shutdown_event.is_set():
            try:
                wav_bytes = self._work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process_recording(wav_bytes)
            except ShutdownRequested:
                log.info("Processing interrupted by shutdown")
            except Exception:
                log.exception("Pipeline error")
            finally:
                with self._lock:
                    self._state = State.IDLE

    def _process_recording(self, wav_bytes: bytes) -> None:
        import time

        min_size = min_wav_size(self.config.audio.sample_rate)
        if len(wav_bytes) < min_size:
            log.warning(
                "Recording too short (%d bytes, min=%d), skipping", len(wav_bytes), min_size
            )
            return

        # Estimate recording duration from WAV size: (size - 44 header) / (sample_rate * 2 bytes)
        duration = (len(wav_bytes) - 44) / (self.config.audio.sample_rate * 2)
        log.info("Transcribing (%.1fs, %d bytes)...", duration, len(wav_bytes))

        t0 = time.monotonic()
        text = self.whisper.transcribe(wav_bytes)
        transcribe_ms = (time.monotonic() - t0) * 1000
        log.info("Transcription took %.0fms", transcribe_ms)

        if not text.strip():
            log.warning("Empty transcription, skipping")
            return

        text = self.postprocessor.normalize(text)

        t0 = time.monotonic()
        text = self.cleanup.cleanup(text)
        cleanup_ms = (time.monotonic() - t0) * 1000
        if self.config.cleanup.enabled:
            log.info("Cleanup took %.0fms", cleanup_ms)

        text = self.postprocessor.finalize(text)

        log.info("Result: %s", text)

        self.hotkey_listener.suppress()
        try:
            self.injector.inject(text)
        finally:
            self.hotkey_listener.resume()
        log.info(
            "Done (total pipeline: transcribe=%.0fms, cleanup=%.0fms)", transcribe_ms, cleanup_ms
        )

    def _startup_checks(self) -> None:
        """Validate mic, clipboard, whisper-server before entering main loop."""
        log.info("Running startup checks...")

        # Check audio device
        try:
            import sounddevice as sd  # type: ignore[import-untyped]

            sd.check_input_settings(samplerate=self.config.audio.sample_rate, channels=1)
            log.info("Audio device: OK")
        except Exception as e:
            log.warning("Audio device check failed: %s. Recording may not work.", e)

        # Start or check whisper-server
        if self._server_manager:
            try:
                self._server_manager.start()
                log.info("Whisper server (managed): OK")
            except (RuntimeError, TimeoutError, OSError) as e:
                log.error("Failed to start managed whisper-server: %s", e)
                raise SystemExit(1) from e
        elif self.whisper.health_check():
            log.info("Whisper server: OK")
        else:
            log.error(
                "Whisper server at %s is not reachable. "
                "Start the server and try again.",
                self.config.whisper.server_url,
            )
            raise SystemExit(1)

        # Check clipboard
        if self.injector.check_clipboard_available():
            log.info("Clipboard: OK")
        else:
            log.warning(
                "Clipboard not available. Text injection will fail. "
                "Install xclip (Linux) or check your display server."
            )

        # Check notifications
        from samwhispers.notify import check_notify_available

        if check_notify_available():
            log.info("Notifications: OK")
        else:
            log.warning(
                "Desktop notifications not available. "
                "Install notify-send (Linux) for language switch notifications."
            )

        # Log language configuration
        lang = self._languages[0]
        label = "Auto-detect" if lang == "auto" else lang
        if len(self._languages) > 1:
            log.info(
                "Language: %s (cycle with '%s' through %s)",
                label,
                self.config.hotkey.language_key,
                self._languages,
            )
            from samwhispers.notify import notify

            notify("SamWhispers", f"Language: {label}")
        else:
            log.info("Language: %s", label)

        # Vocabulary logging
        if self.config.vocabulary.words or self.config.vocabulary.languages:
            log.info(
                "Vocabulary: %d global + %d language-specific words",
                len(self.config.vocabulary.words),
                sum(len(v) for v in self.config.vocabulary.languages.values()),
            )

        # Filler logging
        if self.config.filler.enabled:
            filler_count = len(self.config.filler.words)
            builtin_label = "built-in + " if self.config.filler.use_builtins else ""
            log.info(
                "Filler removal: enabled (%s%d custom words)", builtin_label, filler_count
            )
        else:
            log.info("Filler removal: disabled")

        log.info("Startup checks complete")

    def shutdown(self) -> None:
        """Stop all components, close resources."""
        log.info("Shutting down...")
        self._shutdown_event.set()
        self.hotkey_listener.stop()
        self.recorder.close()
        if self._server_manager:
            self._server_manager.stop()
        self.whisper.close()
        self.cleanup.close()
        log.info("Shutdown complete")
