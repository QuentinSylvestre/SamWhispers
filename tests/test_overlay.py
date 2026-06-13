"""Tests for the overlay controller and pure helpers (no display needed)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np

from samwhispers import overlay as ov
from samwhispers.audio import compute_level
from samwhispers.overlay import OverlayController, bar_targets, bottom_center_geometry


def test_compute_level_silence_and_clipping() -> None:
    assert compute_level(np.zeros(0, dtype=np.float32)) == 0.0
    assert compute_level(np.zeros(100, dtype=np.float32)) == 0.0
    loud = np.ones(100, dtype=np.float32)  # rms 1.0 * gain -> clamped
    assert compute_level(loud) == 1.0
    quiet = np.full(100, 0.01, dtype=np.float32)
    assert 0.0 < compute_level(quiet) < 1.0


def test_bottom_center_geometry() -> None:
    x, y = bottom_center_geometry(1920, 1080, w=150, h=46, margin=80)
    assert x == (1920 - 150) // 2
    assert y == 1080 - 46 - 80


def test_bottom_center_geometry_clamps_negative() -> None:
    _, y = bottom_center_geometry(800, 40, w=150, h=46, margin=80)
    assert y == 0


def test_bar_targets_monotonic_with_level() -> None:
    low = bar_targets(0.0, 4)
    high = bar_targets(1.0, 4)
    assert len(low) == len(high) == 4
    assert all(0.0 <= v <= 1.0 for v in low + high)
    # All bars rise with louder input; none collapse to zero while recording.
    assert all(h > lo for h, lo in zip(high, low))
    assert all(v > 0 for v in low)


def _fake_proc(alive: bool = True) -> MagicMock:
    proc = MagicMock()
    proc.poll.return_value = None if alive else 0
    proc.stdin = MagicMock()
    return proc


def test_start_noop_without_display() -> None:
    c = OverlayController()
    with (
        patch.object(ov, "_display_available", return_value=False),
        patch.object(ov.subprocess, "Popen") as popen,
    ):
        c.start()
    popen.assert_not_called()


def test_start_spawns_overlay_process() -> None:
    c = OverlayController()
    proc = _fake_proc()
    with (
        patch.object(ov, "_display_available", return_value=True),
        patch.object(ov.subprocess, "Popen", return_value=proc) as popen,
    ):
        c.start()
        try:
            popen.assert_called_once()
            cmd = popen.call_args.args[0]
            assert cmd[1:] == ["-m", "samwhispers.overlay"]
        finally:
            c.stop()


def test_write_serializes_message() -> None:
    c = OverlayController()
    c._proc = _fake_proc()
    assert c._write({"state": "recording", "level": 0.5}) is True
    written = c._proc.stdin.write.call_args.args[0]
    assert json.loads(written.strip()) == {"state": "recording", "level": 0.5}


def test_write_handles_dead_pipe() -> None:
    c = OverlayController()
    c._proc = _fake_proc()
    c._proc.stdin.write.side_effect = BrokenPipeError()
    assert c._write({"state": "recording", "level": 0.1}) is False


def test_set_state_idle_resets_level() -> None:
    c = OverlayController()
    c.set_level(0.8)
    c.set_state("idle")
    assert c._level == 0.0


def test_stop_is_safe_without_start() -> None:
    OverlayController().stop()  # must not raise
