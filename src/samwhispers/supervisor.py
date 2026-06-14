"""Supervisor process: owns the tray icon, whisper-server, and a worker child.

The worker is the existing daemon (``python -m samwhispers``), spawned as a
subprocess. The supervisor keeps it alive, restarts it on crash, and exposes
pause/resume/restart controls for the tray icon and the web UI. Running the
worker as a child means a config-save restart can swap the worker without
tearing down the tray or the web server that serve the UI.

The supervisor also owns the managed whisper-server (instead of the worker), so
restarting the worker for an unrelated config change (hotkey, vocabulary,
cleanup, ...) does not reload the whisper model. The worker is always launched
with ``--unmanaged-server`` and simply connects to the server the supervisor
manages (or an external one when ``whisper.managed = false``).
"""

from __future__ import annotations

import collections
import enum
import json
import logging
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from types import FrameType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from samwhispers.server import WhisperServerManager
    from samwhispers.webserver import WebServerHandle

from samwhispers.notify import notify
from samwhispers.overlay import OverlayController

log = logging.getLogger("samwhispers.supervisor")

# Module-level storage for launch args (populated in main()) so relaunch() can access them.
_launch_args: dict[str, Any] = {}

_SHUTDOWN_GRACE = 5.0
_MAX_RESTARTS = 5
_RESTART_BACKOFF = 2.0
_POLL_INTERVAL = 1.0
_EX_CONFIG = 78  # sysexits: startup/config failure — don't retry
_CREATE_NO_WINDOW = 0x08000000  # Windows: run a console child without a window
_DETACHED_PROCESS = 0x00000008  # Windows: detach a child from the console
_CREATE_NEW_PROCESS_GROUP = 0x00000200  # Windows: new process group (no parent Ctrl+C)


class WorkerState(enum.Enum):
    """Coarse worker lifecycle state surfaced to the tray icon."""

    STOPPED = "stopped"
    STARTING = "starting"  # spawned, not yet confirmed healthy
    RUNNING = "running"
    PAUSED = "paused"


class WorkerSupervisor:
    """Spawn, monitor, and restart the SamWhispers worker subprocess."""

    def __init__(
        self,
        config_path: str | None = None,
        verbose: bool = False,
        on_state_change: Callable[[WorkerState], None] | None = None,
    ) -> None:
        self._config_path = config_path
        self._verbose = verbose
        self._on_state_change = on_state_change

        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()  # set => supervisor is shutting down
        self._paused = False
        self._state = WorkerState.STOPPED

        # Log ring buffer (shared between worker stderr and supervisor logger)
        self._log_buffer: collections.deque[str] = collections.deque(maxlen=200)
        self._log_lock = threading.Lock()
        self._log_reader: threading.Thread | None = None

        # Capture supervisor logger output into the ring buffer
        handler = _RingBufferHandler(self._log_buffer, self._log_lock)
        logging.getLogger("samwhispers.supervisor").addHandler(handler)

        # Managed whisper-server is owned here, not by the worker.
        self._whisper_manager: WhisperServerManager | None = None
        self._whisper_lock = threading.Lock()

        # Startup overlay: shown during launch, dismissed once worker is running.
        self._startup_overlay: OverlayController | None = None

        # Supervisor lifecycle events (checked by main() after tray/headless loop exits)
        self._shutdown_requested = threading.Event()
        self._relaunch_requested = threading.Event()

    # --- public API -----------------------------------------------------

    @property
    def state(self) -> WorkerState:
        with self._lock:
            return self._state

    @property
    def logs(self) -> list[str]:
        with self._log_lock:
            return list(self._log_buffer)

    def set_state_listener(self, callback: Callable[[WorkerState], None] | None) -> None:
        """Register a callback invoked on every state transition (e.g. tray update)."""
        with self._lock:
            self._on_state_change = callback

    def start(self) -> None:
        """Start whisper-server (if managed), then the worker and monitor thread."""
        self._stop_event.clear()
        self._start_startup_overlay()
        self._start_whisper()
        with self._lock:
            self._paused = False
            self._spawn()
        if self._monitor_thread is None or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, daemon=True, name="supervisor-monitor"
            )
            self._monitor_thread.start()

    def restart(self) -> None:
        """Restart the worker child (used after a config save needing a reload)."""
        log.info("Restarting worker...")
        with self._lock:
            self._terminate_proc()
            if not self._paused and not self._stop_event.is_set():
                self._spawn()

    def restart_whisper(self) -> None:
        """Stop and restart the managed whisper-server, reloading whisper config."""
        log.info("Restarting managed whisper-server...")
        self._stop_whisper()
        self._start_whisper()

    def apply_config_change(self, restart_whisper: bool) -> None:
        """Apply a saved config change: bounce whisper-server only if needed.

        Whisper-server is reloaded only when ``[whisper]`` settings changed
        (the slow part -- model reload); the worker is always restarted to pick
        up the new config.
        """
        if restart_whisper:
            self.restart_whisper()
        self.restart()

    def pause(self) -> None:
        """Stop the worker so it releases the hotkey/mic, without exiting the supervisor."""
        with self._lock:
            if self._paused:
                return
            self._paused = True
            self._terminate_proc()
            self._set_state(WorkerState.PAUSED)
        log.info("Worker paused")

    def resume(self) -> None:
        """Restart the worker after a pause."""
        with self._lock:
            if not self._paused:
                return
            self._paused = False
            if not self._stop_event.is_set():
                self._spawn()
        log.info("Worker resumed")

    def shutdown(self) -> None:
        """Stop the worker, whisper-server, and the monitor; the supervisor exits."""
        self._stop_event.set()
        if self._startup_overlay is not None:
            self._startup_overlay.stop()
            self._startup_overlay = None
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=3.0)
            if self._monitor_thread.is_alive():
                log.warning("Monitor thread did not exit within 3s (will be cleaned up at process exit)")
        with self._lock:
            self._terminate_proc()
            self._set_state(WorkerState.STOPPED)
        self._stop_whisper()
        log.info("Supervisor shutdown complete")

    def request_shutdown(self) -> None:
        """Signal the main thread to shut down after the tray/headless loop exits."""
        self._shutdown_requested.set()

    def request_relaunch(self) -> None:
        """Signal the main thread to re-exec after the tray/headless loop exits."""
        self._relaunch_requested.set()

    # --- internals ------------------------------------------------------

    def _build_cmd(self) -> list[str]:
        # The supervisor owns the managed whisper-server, so the worker always
        # runs unmanaged and just connects to it.
        cmd = [sys.executable, "-m", "samwhispers", "worker", "--unmanaged-server"]
        if self._config_path:
            cmd += ["--config", self._config_path]
        if self._verbose:
            cmd.append("--verbose")
        return cmd

    def _start_startup_overlay(self) -> None:
        """Show spinner overlay immediately so the user sees launch feedback."""
        try:
            from samwhispers.webconfig import current_app_config

            if not current_app_config(self._config_path).overlay.enabled:
                return
        except Exception:
            return
        ov = OverlayController()
        ov.start()
        ov.set_state("processing")
        self._startup_overlay = ov

    def _dismiss_startup_overlay(self) -> None:
        """Transition startup overlay to checkmark then dismiss after 1s."""
        ov = self._startup_overlay
        if ov is None:
            return
        self._startup_overlay = None
        ov.set_state("ready")
        threading.Timer(1.0, ov.stop).start()

    def _start_whisper(self) -> None:
        """Start the managed whisper-server if ``whisper.managed`` is set."""
        with self._whisper_lock:
            if self._stop_event.is_set() or self._whisper_manager is not None:
                return
            whisper_cfg = self._load_whisper_config()
            if whisper_cfg is None or not whisper_cfg.managed:
                return
            try:
                from samwhispers.server import WhisperServerManager

                vad_cfg = self._load_vad_config()
                manager = WhisperServerManager(whisper_cfg, vad_config=vad_cfg)
                manager.start()
                self._whisper_manager = manager
                log.info("Managed whisper-server started")
            except Exception:
                log.exception(
                    "Failed to start managed whisper-server; "
                    "the worker may be unable to transcribe until this is fixed"
                )
                notify(
                    "SamWhispers",
                    "Voice transcription unavailable \u2014 the speech engine failed to start",
                )

    def _stop_whisper(self) -> None:
        with self._whisper_lock:
            manager, self._whisper_manager = self._whisper_manager, None
        if manager is not None:
            manager.stop()

    def _load_whisper_config(self) -> Any:
        """Load whisper config from disk without strict validation, or None on error."""
        try:
            from samwhispers.webconfig import current_app_config

            return current_app_config(self._config_path).whisper
        except Exception:
            log.exception("Failed to load config for whisper-server")
            return None

    def _load_vad_config(self) -> Any:
        """Load VAD config from disk without strict validation, or None on error."""
        try:
            from samwhispers.webconfig import current_app_config

            return current_app_config(self._config_path).vad
        except Exception:
            log.exception("Failed to load VAD config")
            return None

    def _spawn(self) -> None:
        """Launch a fresh worker process. Caller holds the lock."""
        cmd = self._build_cmd()
        log.info("Starting worker: %s", " ".join(cmd))
        flags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(cmd, creationflags=flags, stderr=subprocess.PIPE, text=True)
        with self._log_lock:
            self._log_buffer.append("--- worker started ---")
        self._log_reader = threading.Thread(
            target=self._read_worker_logs, daemon=True, name="worker-log-reader"
        )
        self._log_reader.start()
        self._set_state(WorkerState.STARTING)

    def _terminate_proc(self) -> None:
        """Terminate the current worker if running. Caller holds the lock."""
        proc, self._proc = self._proc, None
        reader, self._log_reader = self._log_reader, None
        if proc and proc.poll() is None:
            log.info("Stopping worker (pid %d)...", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=_SHUTDOWN_GRACE)
            except subprocess.TimeoutExpired:
                log.warning("Worker did not exit gracefully, killing")
                proc.kill()
                proc.wait()
        if reader is not None:
            reader.join(timeout=2.0)

    def _read_worker_logs(self) -> None:
        """Read lines from worker stderr and append to the ring buffer."""
        with self._lock:
            proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.rstrip("\n")
            if line:
                with self._log_lock:
                    self._log_buffer.append(line)

    def _set_state(self, state: WorkerState) -> None:
        """Update state and notify the listener. Caller holds the lock."""
        if state == self._state:
            return
        self._state = state
        if state == WorkerState.RUNNING:
            self._dismiss_startup_overlay()
        callback = self._on_state_change
        if callback is not None:
            try:
                callback(state)
            except Exception:
                log.exception("State-change callback failed")

    def _monitor_loop(self) -> None:
        """Watch the worker; auto-restart on unexpected exit with capped backoff."""
        restart_count = 0
        startup_ticks = 0
        while not self._stop_event.wait(timeout=_POLL_INTERVAL):
            with self._lock:
                proc = self._proc
                if self._paused or proc is None:
                    restart_count = 0
                    continue
            if proc.poll() is None:
                if self._state == WorkerState.STARTING:
                    startup_ticks += 1
                    if startup_ticks >= 3:
                        with self._lock:
                            self._set_state(WorkerState.RUNNING)
                restart_count = 0
                continue

            # Worker exited unexpectedly -- reap it and decide whether to restart.
            if self._log_reader is not None:
                self._log_reader.join(timeout=2.0)
            proc.wait()
            code = proc.returncode

            # Startup/config failure — deterministic, don't retry.
            if code == _EX_CONFIG:
                log.error(
                    "Worker startup failed (configuration or setup error). Not retrying."
                )
                notify(
                    "SamWhispers",
                    "SamWhispers couldn\u2019t start \u2014 click to open Logs",
                    on_click_url="http://127.0.0.1:7891/#logs",
                )
                with self._lock:
                    if self._proc is proc:
                        self._set_state(WorkerState.STOPPED)
                return

            restart_count += 1
            if restart_count > _MAX_RESTARTS:
                log.critical(
                    "Worker has crashed %d times; giving up. SamWhispers is not running.",
                    restart_count - 1,
                )
                notify(
                    "SamWhispers",
                    "SamWhispers stopped after repeated failures \u2014 click to open Logs",
                    on_click_url="http://127.0.0.1:7891/#logs",
                )
                with self._lock:
                    if self._proc is proc:
                        self._set_state(WorkerState.STOPPED)
                return
            log.error(
                "Worker crashed (exit code %s), restarting (attempt %d/%d)...",
                code,
                restart_count,
                _MAX_RESTARTS,
            )
            if self._stop_event.wait(timeout=min(_RESTART_BACKOFF * restart_count, 10.0)):
                return
            with self._lock:
                # Skip if an intentional pause/restart/shutdown replaced the process
                # while we were backing off.
                if self._stop_event.is_set() or self._paused or self._proc is not proc:
                    continue
                startup_ticks = 0
                self._spawn()


class _RingBufferHandler(logging.Handler):
    """Logging handler that appends formatted records to a shared ring buffer."""

    def __init__(self, buffer: collections.deque[str], lock: threading.Lock) -> None:
        super().__init__()
        self._buffer = buffer
        self._buf_lock = lock

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._buf_lock:
                self._buffer.append(msg)
        except Exception:
            self.handleError(record)


def _python_launcher() -> str:
    """Interpreter to relaunch ourselves with.

    Always use python.exe; CREATE_NO_WINDOW hides the console.  pythonw.exe
    silently swallows errors and breaks pystray on some Windows setups.
    """
    return sys.executable


def _relaunch_detached(args: Any = None) -> None:
    """Start the supervisor as a detached background process, then return.

    Uses ``args`` (argparse namespace) when called from the detach path, or
    falls back to the module-level ``_launch_args`` dict for re-exec.
    """
    # Normalize to a dict
    if args is not None and not isinstance(args, dict):
        la = {
            "config": args.config,
            "verbose": args.verbose,
            "no_tray": args.no_tray,
            "no_web": args.no_web,
            "web_port": args.web_port,
        }
    elif args is not None:
        la = args
    else:
        la = _launch_args

    extra_args = ["--foreground"]
    if la.get("config"):
        extra_args += ["--config", la["config"]]
    if la.get("verbose"):
        extra_args.append("--verbose")
    if la.get("no_tray"):
        extra_args.append("--no-tray")
    if la.get("no_web"):
        extra_args.append("--no-web")
    if la.get("web_port") is not None:
        extra_args += ["--web-port", str(la["web_port"])]
    cmd = [_python_launcher(), "-c",
           f"import sys, json; sys.argv = ['samwhispers-supervisor'] + json.loads('{json.dumps(extra_args)}'); "
           "from samwhispers.supervisor import main; main()"]

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **popen_kwargs)
    print("SamWhispers started in the background. Quit it from the tray icon.")
    print("Run with --foreground (-f) to keep it attached to this terminal.")


def main() -> None:
    """Entry point: supervise the worker and (optionally) show a tray icon."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="samwhispers-supervisor",
        description="Run SamWhispers in the background with a system tray icon.",
    )
    parser.add_argument("-c", "--config", help="Path to config.toml", default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Run headless without a tray icon (block until terminated)",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Do not start the config web UI",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=None,
        help="Port for the config web UI (default 7891)",
    )
    parser.add_argument(
        "-f",
        "--foreground",
        action="store_true",
        help="Run in this terminal instead of detaching to the background",
    )
    args = parser.parse_args()

    # By default, detach to the background so the terminal is freed and closing
    # it doesn't kill SamWhispers. --foreground keeps it attached (and is what
    # the autostart service uses, since it manages the process itself).
    if not args.foreground:
        from samwhispers.singleinstance import is_running

        if is_running():
            print("SamWhispers is already running.")
            if not args.no_web:
                import webbrowser

                url = f"http://127.0.0.1:{args.web_port or 7891}/"
                print(f"Opening {url} ...")
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            return
        _relaunch_detached(args)
        return

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from samwhispers.singleinstance import InstanceLock

    lock = InstanceLock()
    if not lock.acquire():
        log.error("Another SamWhispers instance is already running; exiting.")
        return

    # Store launch args for re-exec and write PID file
    global _launch_args
    _launch_args = {
        "config": args.config,
        "verbose": args.verbose,
        "no_tray": args.no_tray,
        "no_web": args.no_web,
        "web_port": args.web_port,
    }
    from samwhispers.singleinstance import write_pid

    write_pid()

    supervisor = WorkerSupervisor(config_path=args.config, verbose=args.verbose)
    supervisor.start()

    # Shared stop mechanism: the web endpoints call this to unblock the main thread.
    _main_stop = threading.Event()

    def _stop_main_loop() -> None:
        _main_stop.set()

    web_handle = _start_web(supervisor, args.config, args.no_web, args.web_port, stop_callback=_stop_main_loop)
    settings_url = web_handle.url if web_handle else None

    use_tray = not args.no_tray
    if use_tray:
        from samwhispers.tray import tray_available

        if not tray_available():
            log.warning(
                "pystray/Pillow not available; running headless. "
                "Install with 'pip install pystray Pillow' for a tray icon."
            )
            use_tray = False

    try:
        if use_tray:
            from samwhispers.tray import run_tray

            try:
                run_tray(supervisor, settings_url, stop_event=_main_stop)
            except Exception:
                log.exception("Tray failed to start; falling back to headless mode")
                use_tray = False

        if not use_tray:
            # Headless: block until a termination signal or stop_callback fires.
            def _handle_signal(signum: int, frame: FrameType | None) -> None:
                log.info("Received signal %d, shutting down", signum)
                _main_stop.set()

            signal.signal(signal.SIGTERM, _handle_signal)
            signal.signal(signal.SIGINT, _handle_signal)
            while not _main_stop.wait(timeout=0.5):
                pass
    finally:
        supervisor.shutdown()
        if web_handle is not None:
            web_handle.shutdown()
        lock.release()

    # Post-loop: check if a relaunch was requested
    if supervisor._relaunch_requested.is_set():
        _relaunch_detached()


def _start_web(
    supervisor: WorkerSupervisor,
    config_path: str | None,
    no_web: bool,
    port: int | None,
    stop_callback: Callable[[], None] | None = None,
) -> WebServerHandle | None:
    """Start the config web UI in a background thread, or return None."""
    if no_web:
        return None
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        log.warning(
            "FastAPI/uvicorn not available; config UI disabled. "
            "Install with 'pip install fastapi uvicorn' to enable it."
        )
        return None
    try:
        from samwhispers.webserver import DEFAULT_PORT, create_app, serve

        app = create_app(supervisor, config_path=config_path, stop_callback=stop_callback)
        handle = serve(app, port=port or DEFAULT_PORT)
        log.info("Config UI available at %s", handle.url)
        return handle
    except Exception:
        log.exception("Failed to start config web UI")
        return None


if __name__ == "__main__":
    main()
