"""Audio capture and WAV encoding."""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from typing import Any

import numpy as np

log = logging.getLogger("samwhispers")


def numpy_to_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    """Convert float32 numpy array to 16-bit PCM WAV bytes."""
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


class AudioRecorder:
    """Record microphone audio and produce 16kHz mono 16-bit PCM WAV bytes."""

    def __init__(self, sample_rate: int = 16000, max_duration: float = 300.0) -> None:
        self._sample_rate = sample_rate
        self._max_duration = max_duration
        self._lock = threading.Lock()
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._stream: Any = None
        self._timer: threading.Timer | None = None
        self._error: bool = False

    def _callback(self, indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
        if status:
            log.warning("Audio stream status: %s", status)
            self._error = True
        with self._lock:
            if self._recording:
                self._frames.append(indata[:, 0].copy())

    def start(self) -> None:
        """Open audio stream and begin recording."""
        import sounddevice as sd  # type: ignore[import-untyped]

        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._frames = []
            self._error = False

        # Retry once — WSLg PulseAudio can timeout on first attempt
        for attempt in range(2):
            try:
                self._stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=self._callback,
                )
                self._stream.start()
                break
            except Exception:
                if attempt == 0:
                    log.warning("Audio stream failed, retrying in 0.5s...")
                    time.sleep(0.5)
                else:
                    with self._lock:
                        self._recording = False
                    raise

        self._timer = threading.Timer(self._max_duration, self._auto_stop)
        self._timer.daemon = True
        self._timer.start()
        log.debug("Recording started (max %.0fs)", self._max_duration)

    def _auto_stop(self) -> None:
        log.warning("Max recording duration (%.0fs) reached, auto-stopping", self._max_duration)
        self.stop()

    def stop(self) -> bytes:
        """Stop recording and return WAV bytes. Returns b'' if not recording."""
        with self._lock:
            if not self._recording:
                return b""
            self._recording = False

        if self._timer:
            self._timer.cancel()
            self._timer = None

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if self._error:
            log.warning("Audio errors occurred during recording, result may be partial")

        with self._lock:
            frames = self._frames
            self._frames = []

        if not frames:
            return b""

        audio = np.concatenate(frames)
        wav = numpy_to_wav(audio, self._sample_rate)
        log.debug("Recording stopped: %.1fs, %d bytes", len(audio) / self._sample_rate, len(wav))
        return wav

    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def close(self) -> None:
        """Release resources."""
        if self._recording:
            self.stop()
        if self._timer:
            self._timer.cancel()


def min_wav_size(sample_rate: int, min_seconds: float = 0.5) -> int:
    """Minimum WAV file size for a given duration (header + PCM data)."""
    # WAV header = 44 bytes, 16-bit mono = 2 bytes per sample
    return 44 + int(sample_rate * min_seconds * 2)
