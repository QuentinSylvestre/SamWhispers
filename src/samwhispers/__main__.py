"""SamWhispers entry point.

``samwhispers`` (and ``python -m samwhispers``) launches the full app -- the
supervisor (tray + web UI + a managed worker). The ``worker`` subcommand runs
just the dictation worker and is used internally by the supervisor; you
normally don't run it directly.

Subcommands:
  start   — Launch SamWhispers (default when no subcommand given)
  stop    — Stop a running instance
  restart — Full restart (supervisor + whisper-server + worker)
  worker  — Internal: run just the dictation worker
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request

from samwhispers import __version__


def _get_port() -> int:
    """Return the web UI port from config or the default."""
    from samwhispers.webserver import DEFAULT_PORT

    return DEFAULT_PORT


def _http_post(port: int, path: str) -> bool:
    """POST to a local endpoint. Returns True on success (2xx)."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError):
        return False


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID exists."""
    try:
        if sys.platform == "win32":
            os.kill(pid, 0)
        else:
            os.kill(pid, 0)
        return True
    except OSError:
        return False


def _force_kill(pid: int) -> None:
    """Force-kill a process by PID."""
    if sys.platform == "win32":
        os.kill(pid, signal.SIGTERM)  # TerminateProcess on Windows
    else:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):  # 5s in 100ms steps
            time.sleep(0.1)
            if not _is_process_alive(pid):
                return
        os.kill(pid, signal.SIGKILL)


def _do_stop(port: int) -> bool:
    """Stop a running instance. Returns True if something was stopped."""
    # Try graceful HTTP shutdown first
    if _http_post(port, "/api/supervisor/shutdown"):
        print("Stopping SamWhispers...")
        for _ in range(50):  # poll up to 5s
            time.sleep(0.1)
            from samwhispers.singleinstance import is_running

            if not is_running():
                print("SamWhispers stopped.")
                return True
        # Timeout — fall through to PID kill
    # Fallback: PID-based force kill
    from samwhispers.singleinstance import read_pid

    pid = read_pid()
    if pid and _is_process_alive(pid):
        print("Stopping SamWhispers...")
        _force_kill(pid)
        print("SamWhispers stopped.")
        return True
    print("SamWhispers is not running.")
    return False


def _cmd_stop(args: argparse.Namespace) -> None:
    _do_stop(_get_port())


def _cmd_restart(args: argparse.Namespace) -> None:
    port = _get_port()
    # Try graceful restart via HTTP
    if _http_post(port, "/api/supervisor/restart"):
        print("Restarting SamWhispers...")
        print("SamWhispers restarted.")
        return
    # Fallback: stop then start
    from samwhispers.singleinstance import is_running

    if is_running():
        _do_stop(port)
    # Start fresh
    from samwhispers.supervisor import main as supervisor_main

    print("Restarting SamWhispers...")
    supervisor_main()
    print("SamWhispers restarted.")


def _cmd_start(args: argparse.Namespace) -> None:
    from samwhispers.supervisor import main as supervisor_main

    supervisor_main()


def main() -> None:
    """Dispatch subcommands."""
    parser = argparse.ArgumentParser(
        prog="samwhispers",
        description="Local voice-to-text daemon. Press a hotkey, speak, release — your words appear as text.",
    )
    parser.add_argument("--version", action="version", version=f"samwhispers {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    # -- start (default) --
    sp_start = subparsers.add_parser("start", help="Launch SamWhispers (default)")
    sp_start.add_argument("-c", "--config", help="Path to config.toml", default=None)
    sp_start.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    sp_start.add_argument("--no-tray", action="store_true", help="Run headless without a tray icon")
    sp_start.add_argument("--no-web", action="store_true", help="Do not start the config web UI")
    sp_start.add_argument("--web-port", type=int, default=None, help="Port for the config web UI (default 7891)")
    sp_start.add_argument("-f", "--foreground", action="store_true", help="Run in this terminal instead of detaching")
    sp_start.set_defaults(func=_cmd_start)

    # -- stop --
    sp_stop = subparsers.add_parser("stop", help="Stop a running instance")
    sp_stop.set_defaults(func=_cmd_stop)

    # -- restart --
    sp_restart = subparsers.add_parser("restart", help="Full restart (supervisor + whisper-server + worker)")
    sp_restart.set_defaults(func=_cmd_restart)

    # -- worker (internal) --
    sp_worker = subparsers.add_parser("worker", help="(internal) Run only the dictation worker")
    sp_worker.add_argument("-c", "--config", help="Path to config.toml", default=None)
    sp_worker.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    sp_worker.add_argument("--unmanaged-server", action="store_true", help="Do not manage whisper-server")
    sp_worker.add_argument("--version", action="version", version=f"samwhispers {__version__}")
    sp_worker.set_defaults(func=lambda args: _run_worker(args))

    args, remaining = parser.parse_known_args()

    # Bare invocation (no subcommand) = start — pass all args to supervisor
    if args.command is None:
        from samwhispers.supervisor import main as supervisor_main

        supervisor_main()
        return

    # If there are remaining args and a subcommand was matched, error
    if remaining:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")

    args.func(args)


def _run_worker(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from samwhispers.app import SamWhispers
    from samwhispers.config import load_config

    config = load_config(args.config)
    app = SamWhispers(config, manage_server=not args.unmanaged_server)

    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
