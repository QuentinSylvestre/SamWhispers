"""Tests for the top-level entry dispatcher."""

from __future__ import annotations

import sys
from unittest.mock import patch

import samwhispers.__main__ as entry


def test_bare_invocation_runs_supervisor() -> None:
    with (
        patch.object(sys, "argv", ["samwhispers"]),
        patch("samwhispers.supervisor.main") as sup_main,
    ):
        entry.main()
    sup_main.assert_called_once()


def test_supervisor_args_pass_through() -> None:
    with (
        patch.object(sys, "argv", ["samwhispers", "--no-tray"]),
        patch("samwhispers.supervisor.main") as sup_main,
    ):
        entry.main()
    sup_main.assert_called_once()  # supervisor parses its own args


def test_worker_subcommand_dispatches() -> None:
    with (
        patch.object(sys, "argv", ["samwhispers", "worker", "--unmanaged-server"]),
        patch.object(entry, "_run_worker") as run_worker,
    ):
        entry.main()
    run_worker.assert_called_once_with(["--unmanaged-server"])
