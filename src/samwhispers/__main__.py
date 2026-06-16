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


def _http_post(host: str, port: int, path: str, csrf_token: str | None = None) -> bool:
    """POST to a local endpoint. Returns True on success (2xx)."""
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}{path}",
            data=b"",
            method="POST",
            headers={"Host": f"{host}:{port}"},
        )
        if csrf_token:
            req.add_header("X-SamWhispers-CSRF", csrf_token)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return bool(200 <= resp.status < 300)
    except (urllib.error.URLError, OSError):
        return False


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID exists."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _force_kill(pid: int) -> None:
    """Force-kill a process by PID."""
    if sys.platform == "win32":
        os.kill(pid, signal.SIGTERM)
    else:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            time.sleep(0.1)
            if not _is_process_alive(pid):
                return
        os.kill(pid, signal.SIGKILL)


def _do_stop() -> bool:
    """Stop a running instance using runtime metadata for topology. Returns True if stopped."""
    from samwhispers.runtime import (
        delete_metadata,
        is_pid_alive,
        is_samwhispers_process,
        read_metadata,
        validate_metadata,
    )
    from samwhispers.singleinstance import is_running, read_pid

    meta = read_metadata()

    # Try metadata-based HTTP shutdown first
    if meta and validate_metadata(meta) and meta.web_enabled and meta.web_port:
        if _http_post(meta.web_host, meta.web_port, "/api/supervisor/shutdown", meta.csrf_token):
            print("Stopping SamWhispers...")
            for _ in range(50):
                time.sleep(0.1)
                if not is_running():
                    delete_metadata()
                    print("SamWhispers stopped.")
                    return True
            # Timeout — fall through to PID kill

    # Fallback: verified process termination
    pid = meta.pid if meta else read_pid()
    if pid and is_pid_alive(pid) and is_samwhispers_process(pid):
        print("Stopping SamWhispers...")
        _force_kill(pid)
        delete_metadata()
        print("SamWhispers stopped.")
        return True

    # Last check: lock is held but we can't identify the process
    if is_running():
        pid = read_pid()
        if pid and is_pid_alive(pid):
            print("Stopping SamWhispers...")
            _force_kill(pid)
            delete_metadata()
            print("SamWhispers stopped.")
            return True

    print("SamWhispers is not running.")
    return False


def _do_restart() -> None:
    """Restart a running instance using runtime metadata for topology."""
    from samwhispers.runtime import read_metadata, validate_metadata

    meta = read_metadata()

    # Try metadata-based HTTP restart first
    if meta and validate_metadata(meta) and meta.web_enabled and meta.web_port:
        if _http_post(meta.web_host, meta.web_port, "/api/supervisor/restart", meta.csrf_token):
            print("Restarting SamWhispers...")
            print("SamWhispers restarted.")
            return

    # Fallback: stop then start using recorded launch args
    from samwhispers.singleinstance import is_running

    if is_running():
        _do_stop()

    # Reconstruct launch from metadata if available
    if meta and meta.launch_args:
        from samwhispers.supervisor import main as supervisor_main

        # Inject recorded args (without 'restart' token)
        sys.argv = [meta.launch_args[0]] + [a for a in meta.launch_args[1:] if a != "restart"]
        supervisor_main()
    else:
        from samwhispers.supervisor import main as supervisor_main

        supervisor_main()

    print("SamWhispers restarted.")


def _cmd_stop(args: argparse.Namespace) -> None:
    _do_stop()


def _cmd_restart(args: argparse.Namespace) -> None:
    _do_restart()


def _cmd_start(args: argparse.Namespace) -> None:
    # Pass supervisor arguments without the 'start' token
    # Rebuild sys.argv without 'start' so supervisor sees clean args
    new_argv = [sys.argv[0]]
    skip_next = False
    for i, arg in enumerate(sys.argv[1:], 1):
        if skip_next:
            skip_next = False
            continue
        if arg == "start":
            continue
        new_argv.append(arg)
    sys.argv = new_argv

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
