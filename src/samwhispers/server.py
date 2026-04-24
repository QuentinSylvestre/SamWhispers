"""Managed whisper-server subprocess."""

from __future__ import annotations

import atexit
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from samwhispers.config import WhisperConfig
from samwhispers.transcribe import WhisperClient

log = logging.getLogger("samwhispers")

_READY_POLL_INTERVAL = 0.5
_READY_TIMEOUT = 30.0
_SHUTDOWN_GRACE = 5.0
_MAX_RESTARTS = 5
_RESTART_BACKOFF = 2.0


def _resolve_server_bin(raw_path: str) -> str:
    """Resolve the whisper-server binary path, handling platform variants.

    On Windows, if the given path doesn't exist, try appending Release/ to the
    parent directory and .exe to the filename -- matching the default CMake
    output layout on Windows (build/bin/Release/whisper-server.exe).
    """
    p = Path(raw_path)
    if p.is_file():
        return str(p.resolve())
    if sys.platform == "win32":
        win_candidate = p.parent / "Release" / (p.name + ".exe")
        if win_candidate.is_file():
            return str(win_candidate.resolve())
    return str(p.resolve())


class WhisperServerManager:
    """Spawn, monitor, and restart whisper-server."""

    def __init__(self, config: WhisperConfig) -> None:
        self._config = config
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        parsed = urlparse(config.server_url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = str(parsed.port or 8080)

        # Note: parsed.hostname preserves the original form ("localhost" vs
        # "127.0.0.1"). whisper-server treats both identically for binding,
        # so no normalization is applied. If this causes issues with specific
        # whisper-server versions, normalize to "127.0.0.1" here.

        if self._host not in ("127.0.0.1", "localhost", "::1"):
            log.warning(
                "whisper.server_url binds managed server to non-loopback host %r. "
                "The whisper-server API has no authentication -- "
                "ensure this is intentional.",
                self._host,
            )

        self._bin = _resolve_server_bin(config.server_bin)
        self._model = str(Path(config.model_path).resolve())

        atexit.register(self.stop)

    def _build_cmd(self) -> list[str]:
        return [self._bin, "-m", self._model, "--host", self._host, "--port", self._port]

    def start(self) -> None:
        """Spawn whisper-server and block until it passes health check."""
        self._stop_event.clear()
        self._spawn()
        self._wait_ready()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _spawn(self) -> None:
        cmd = self._build_cmd()
        log.info("Starting whisper-server: %s", " ".join(cmd))
        with self._lock:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _wait_ready(self) -> None:
        client = WhisperClient(server_url=self._config.server_url)
        deadline = time.monotonic() + _READY_TIMEOUT
        try:
            while time.monotonic() < deadline:
                with self._lock:
                    proc = self._proc
                if proc and proc.poll() is not None:
                    raise RuntimeError(
                        f"whisper-server exited immediately with code {proc.returncode}. "
                        f"Possible causes: binary/model path incorrect, port {self._port} "
                        "already in use, or unsupported hardware. "
                        "Run the binary manually to see full output."
                    )
                if client.health_check():
                    log.info("whisper-server is ready on port %s", self._port)
                    return
                time.sleep(_READY_POLL_INTERVAL)
        finally:
            client.close()
        raise TimeoutError(
            f"whisper-server not ready after {_READY_TIMEOUT}s. "
            "The model may be too large -- try a smaller one."
        )

    def _monitor_loop(self) -> None:
        restart_count = 0
        while not self._stop_event.is_set():
            with self._lock:
                proc = self._proc
            if proc and proc.poll() is not None:
                if self._stop_event.is_set():
                    return
                # Reap the crashed process to avoid zombies
                proc.wait()
                restart_count += 1
                if restart_count > _MAX_RESTARTS:
                    log.critical(
                        "whisper-server has crashed %d times; giving up. "
                        "Transcription will be unavailable.",
                        restart_count - 1,
                    )
                    return
                log.error(
                    "whisper-server crashed (exit code %d), restarting (attempt %d/%d)...",
                    proc.returncode,
                    restart_count,
                    _MAX_RESTARTS,
                )
                if self._stop_event.wait(timeout=min(_RESTART_BACKOFF * restart_count, 10.0)):
                    return
                try:
                    self._spawn()
                    self._wait_ready()
                    restart_count = 0
                    log.info("whisper-server recovered successfully")
                except Exception:
                    log.exception("Failed to restart whisper-server")
                    return
            self._stop_event.wait(timeout=1.0)

    def stop(self) -> None:
        """Terminate the managed server process."""
        self._stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=3.0)
        with self._lock:
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            log.info("Stopping whisper-server (pid %d)...", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=_SHUTDOWN_GRACE)
            except subprocess.TimeoutExpired:
                log.warning("whisper-server did not exit gracefully, killing")
                proc.kill()
                proc.wait()
