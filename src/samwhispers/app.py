"""Main application orchestration."""

from __future__ import annotations

import enum
import logging
import queue
import signal
import threading
from types import FrameType

from samwhispers.audio import AudioRecorder, min_wav_size
from samwhispers.cleanup import CleanupProvider
from samwhispers.config import AppConfig
from samwhispers.hotkeys import HotkeyListener
from samwhispers.inject import TextInjector
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

        self.recorder = AudioRecorder(
            sample_rate=config.audio.sample_rate,
            max_duration=config.audio.max_duration,
        )
        self.whisper = WhisperClient(
            server_url=config.whisper.server_url,
            language=config.whisper.language,
        )
        self.cleanup = CleanupProvider(config.cleanup)
        self.injector = TextInjector(paste_delay=config.inject.paste_delay)
        self.hotkey_listener = HotkeyListener(
            hotkey_str=config.hotkey.key,
            mode=config.hotkey.mode,
            on_start=self._on_record_start,
            on_stop=self._on_record_stop,
        )

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
        self._shutdown_event.wait()
        self.shutdown()

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:
        log.info("Received signal %d, shutting down", signum)
        self._shutdown_event.set()

    def _on_record_start(self) -> None:
        with self._lock:
            if self._state != State.IDLE:
                log.warning("Busy (%s), ignoring hotkey", self._state.value)
                return
            self._state = State.RECORDING
        self.recorder.start()
        log.info("Recording...")

    def _on_record_stop(self) -> None:
        with self._lock:
            if self._state != State.RECORDING:
                return
            self._state = State.PROCESSING
        wav_bytes = self.recorder.stop()
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

        t0 = time.monotonic()
        text = self.cleanup.cleanup(text)
        cleanup_ms = (time.monotonic() - t0) * 1000
        if self.config.cleanup.enabled:
            log.info("Cleanup took %.0fms", cleanup_ms)

        log.info("Result: %s", text)

        self.hotkey_listener.suppress()
        self.injector.inject(text)
        self.hotkey_listener.resume()
        log.info(
            "Done (total pipeline: transcribe=%.0fms, cleanup=%.0fms)", transcribe_ms, cleanup_ms
        )

    def _startup_checks(self) -> None:
        """Validate mic, clipboard, whisper-server before entering main loop."""
        log.info("Running startup checks...")

        # Check whisper-server
        if self.whisper.health_check():
            log.info("Whisper server: OK")
        else:
            log.warning(
                "Whisper server at %s is not reachable. "
                "Transcription will fail until it's started.",
                self.config.whisper.server_url,
            )

        # Check clipboard
        if self.injector.check_clipboard_available():
            log.info("Clipboard: OK")
        else:
            log.warning(
                "Clipboard not available. Text injection will fail. "
                "Install xclip (Linux) or check your display server."
            )

        log.info("Startup checks complete")

    def shutdown(self) -> None:
        """Stop all components, close resources."""
        log.info("Shutting down...")
        self._shutdown_event.set()
        self.hotkey_listener.stop()
        self.recorder.close()
        self.whisper.close()
        self.cleanup.close()
        log.info("Shutdown complete")
