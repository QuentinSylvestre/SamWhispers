"""Audio capture and WAV encoding."""

from __future__ import annotations

import io
import logging
import math
import threading
import time
import wave
from collections.abc import Callable
from typing import Any

import numpy as np

log = logging.getLogger("samwhispers")


def wav_to_float32(wav_bytes: bytes) -> np.ndarray:
    """Decode mono 16-bit PCM WAV bytes back to a float32 array (inverse of below)."""
    if not wav_bytes:
        return np.zeros(0, dtype=np.float32)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0


def compute_level(samples: np.ndarray, gain: float = 2.5) -> float:
    """Normalized 0..1 loudness for the on-screen meter.

    Uses a square-root (perceptual) curve so normal speech spans a useful range
    instead of pegging the bars at maximum.
    """
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    if not math.isfinite(rms):
        return 0.0
    return min(1.0, math.sqrt(rms) * gain)


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

    def __init__(
        self,
        sample_rate: int = 16000,
        max_duration: float = 300.0,
        on_auto_stop: Callable[[bytes], None] | None = None,
        on_level: Callable[[float], None] | None = None,
        silence_threshold: float = 0.0,
        silence_duration: float = 0.0,
        keep_stream_open: bool = True,
    ) -> None:
        self._sample_rate = sample_rate
        self._max_duration = max_duration
        self._on_auto_stop = on_auto_stop
        self._on_level = on_level
        self._lock = threading.Lock()
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._stream: Any = None
        self._timer: threading.Timer | None = None
        self._error: bool = False
        self._keep_stream_open = keep_stream_open
        self._closed = False
        # Client-side VAD
        self._silence_threshold = silence_threshold
        self._silence_duration = silence_duration
        self._silence_start: float | None = None
        self._vad_fired = False

    def _callback(self, indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
        if status:
            log.warning("Audio stream status: %s", status)
            self._error = True
        with self._lock:
            recording = self._recording
            if recording:
                self._frames.append(indata[:, 0].copy())
        # Emit the audio level for the on-screen meter outside the lock; never
        # let a callback failure disrupt recording.
        if recording and self._on_level is not None:
            try:
                self._on_level(compute_level(indata[:, 0]))
            except Exception:
                log.debug("Level callback failed", exc_info=True)
        # Client-side VAD: track silence duration (toggle mode only)
        if recording and self._silence_threshold > 0 and self._silence_duration > 0:
            level = compute_level(indata[:, 0])
            if level < self._silence_threshold:
                if self._silence_start is None:
                    self._silence_start = time.monotonic()
                elif (
                    time.monotonic() - self._silence_start >= self._silence_duration
                    and not self._vad_fired
                ):
                    self._vad_fired = True
                    # Defer stop to avoid deadlock (callback holds _lock)
                    threading.Timer(0, self._trigger_vad_stop).start()
            else:
                self._silence_start = None

    def _trigger_vad_stop(self) -> None:
        """Cancel max-duration timer and auto-stop after silence detection."""
        if self._timer:
            self._timer.cancel()
            self._timer = None
        log.info("Silence detected (%.1fs), auto-stopping", self._silence_duration)
        self._auto_stop()

    def start(self) -> None:
        """Open audio stream and begin recording."""
        import sounddevice as sd  # type: ignore[import-untyped]

        with self._lock:
            if self._recording or self._closed:
                return
            self._frames = []
            self._vad_fired = False
            self._silence_start = None

            # Try warm restart (stream kept open from previous recording)
            if self._stream is not None:
                try:
                    self._stream.start()
                    self._recording = True
                    self._error = False
                except Exception:
                    log.warning("Warm stream restart failed, re-opening device")
                    try:
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None

            if self._recording:
                # Warm restart succeeded
                self._timer = threading.Timer(self._max_duration, self._auto_stop)
                self._timer.daemon = True
                self._timer.start()
                log.debug("Recording started (warm, max %.0fs)", self._max_duration)
                return

        # Full open (first time, or after warm restart failure)
        for attempt in range(2):
            try:
                stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=self._callback,
                )
                stream.start()
                break
            except Exception:
                if attempt == 0:
                    log.warning("Audio stream failed, retrying in 0.5s...")
                    time.sleep(0.5)
                else:
                    raise

        with self._lock:
            if self._closed:
                stream.stop()
                stream.close()
                return
            self._stream = stream
            self._recording = True
            self._error = False

        self._timer = threading.Timer(self._max_duration, self._auto_stop)
        self._timer.daemon = True
        self._timer.start()
        log.debug("Recording started (max %.0fs)", self._max_duration)

    def _auto_stop(self) -> None:
        log.warning("Max recording duration (%.0fs) reached, auto-stopping", self._max_duration)
        wav_bytes = self.stop()
        if self._on_auto_stop and wav_bytes:
            self._on_auto_stop(wav_bytes)

    def stop(self) -> bytes:
        """Stop recording and return WAV bytes. Returns b'' if not recording."""
        with self._lock:
            if not self._recording:
                return b""
            self._recording = False
            stream = self._stream
            if not self._keep_stream_open:
                self._stream = None
            timer = self._timer
            self._timer = None
            frames = self._frames
            self._frames = []
            keep = self._keep_stream_open

        if timer:
            timer.cancel()

        if stream is not None:
            stream.stop()
            if not keep:
                stream.close()

        if self._error:
            log.warning("Audio errors occurred during recording, result may be partial")

        if not frames:
            return b""

        audio = np.concatenate(frames)
        wav = numpy_to_wav(audio, self._sample_rate)
        log.debug("Recording stopped: %.1fs, %d bytes", len(audio) / self._sample_rate, len(wav))
        return wav

    def snapshot(self) -> np.ndarray:
        """Return a copy of the audio captured so far (for streaming decode)."""
        with self._lock:
            if not self._frames:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._frames)

    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def close(self) -> None:
        """Release resources. Always closes stream regardless of keep_stream_open."""
        with self._lock:
            self._closed = True
            recording = self._recording
        if recording:
            self.stop()
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if self._timer:
            self._timer.cancel()


def min_wav_size(sample_rate: int, min_seconds: float = 0.5) -> int:
    """Minimum WAV file size for a given duration (header + PCM data)."""
    # WAV header = 44 bytes, 16-bit mono = 2 bytes per sample
    return 44 + int(sample_rate * min_seconds * 2)
