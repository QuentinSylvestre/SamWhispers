"""SamWhispers entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from samwhispers import __version__


def main() -> None:
    """Run SamWhispers daemon."""
    parser = argparse.ArgumentParser(
        prog="samwhispers",
        description="Local voice-to-text daemon using whisper.cpp",
    )
    parser.add_argument("-c", "--config", help="Path to config.toml", default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--version", action="version", version=f"samwhispers {__version__}")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from samwhispers.app import SamWhispers
    from samwhispers.config import load_config

    config = load_config(args.config)
    app = SamWhispers(config)

    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
