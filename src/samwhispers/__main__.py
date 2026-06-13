"""SamWhispers entry point.

``samwhispers`` (and ``python -m samwhispers``) launches the full app -- the
supervisor (tray + web UI + a managed worker). The ``worker`` subcommand runs
just the dictation worker and is used internally by the supervisor; you
normally don't run it directly.
"""

from __future__ import annotations

import argparse
import logging
import sys

from samwhispers import __version__


def main() -> None:
    """Dispatch: 'worker' runs the dictation worker; otherwise run the app."""
    argv = sys.argv[1:]
    if argv and argv[0] == "worker":
        _run_worker(argv[1:])
        return
    # Default: the full app (tray + web UI + worker), configured via config.toml.
    from samwhispers.supervisor import main as supervisor_main

    supervisor_main()


def _run_worker(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="samwhispers worker",
        description="Run only the voice-to-text worker (used internally by the supervisor).",
    )
    parser.add_argument("-c", "--config", help="Path to config.toml", default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--unmanaged-server",
        action="store_true",
        help="Do not manage whisper-server; connect to an externally managed one",
    )
    parser.add_argument("--version", action="version", version=f"samwhispers {__version__}")
    args = parser.parse_args(argv)

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
