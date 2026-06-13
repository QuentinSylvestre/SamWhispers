"""Tests for the worker supervisor process manager."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from samwhispers import supervisor as sup
from samwhispers.supervisor import WorkerState, WorkerSupervisor


def _running_proc() -> MagicMock:
    """A mock Popen that reports as still running."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    return proc


def test_build_cmd_minimal() -> None:
    s = WorkerSupervisor()
    assert s._build_cmd() == [sys.executable, "-m", "samwhispers"]


def test_build_cmd_with_config_and_verbose() -> None:
    s = WorkerSupervisor(config_path="/tmp/c.toml", verbose=True)
    assert s._build_cmd() == [
        sys.executable,
        "-m",
        "samwhispers",
        "--config",
        "/tmp/c.toml",
        "--verbose",
    ]


def test_start_spawns_and_runs_monitor() -> None:
    s = WorkerSupervisor()
    with patch.object(sup.subprocess, "Popen", return_value=_running_proc()) as popen:
        s.start()
        try:
            popen.assert_called_once()
            assert s.state == WorkerState.RUNNING
            assert s._monitor_thread is not None and s._monitor_thread.is_alive()
        finally:
            s.shutdown()
    assert s.state == WorkerState.STOPPED


def test_pause_terminates_and_resume_respawns() -> None:
    proc1, proc2 = _running_proc(), _running_proc()
    with patch.object(sup.subprocess, "Popen", side_effect=[proc1, proc2]) as popen:
        s = WorkerSupervisor()
        s.start()
        s.pause()
        assert s.state == WorkerState.PAUSED
        proc1.terminate.assert_called_once()
        s.resume()
        assert s.state == WorkerState.RUNNING
        assert popen.call_count == 2
        s.shutdown()


def test_pause_idempotent() -> None:
    with patch.object(sup.subprocess, "Popen", return_value=_running_proc()):
        s = WorkerSupervisor()
        s.start()
        s.pause()
        s.pause()  # no second terminate / no raise
        assert s.state == WorkerState.PAUSED
        s.shutdown()


def test_restart_swaps_process() -> None:
    proc1, proc2 = _running_proc(), _running_proc()
    with patch.object(sup.subprocess, "Popen", side_effect=[proc1, proc2]):
        s = WorkerSupervisor()
        s.start()
        s.restart()
        proc1.terminate.assert_called_once()
        assert s._proc is proc2
        assert s.state == WorkerState.RUNNING
        s.shutdown()


def test_shutdown_idempotent() -> None:
    with patch.object(sup.subprocess, "Popen", return_value=_running_proc()):
        s = WorkerSupervisor()
        s.start()
        s.shutdown()
        s.shutdown()  # should not raise


def test_state_listener_receives_transitions() -> None:
    seen: list[WorkerState] = []
    with patch.object(sup.subprocess, "Popen", return_value=_running_proc()):
        s = WorkerSupervisor(on_state_change=seen.append)
        s.start()
        s.pause()
        s.shutdown()
    assert WorkerState.RUNNING in seen
    assert WorkerState.PAUSED in seen
    assert seen[-1] == WorkerState.STOPPED


def test_terminate_kills_when_graceful_exit_times_out() -> None:
    proc = _running_proc()
    proc.wait.side_effect = [sup.subprocess.TimeoutExpired(cmd="x", timeout=1), 0]
    with patch.object(sup.subprocess, "Popen", return_value=proc):
        s = WorkerSupervisor()
        s.start()
        s.shutdown()
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_monitor_gives_up_after_max_restarts() -> None:
    """Monitor stops respawning once the crash budget is exhausted."""
    dead = MagicMock()
    dead.poll.return_value = 1
    dead.returncode = 1

    s = WorkerSupervisor()
    s._proc = dead
    s._state = WorkerState.RUNNING

    with (
        patch.object(sup, "_POLL_INTERVAL", 0.0),
        patch.object(sup, "_RESTART_BACKOFF", 0.0),
        patch.object(s, "_spawn") as spawn,  # keep _proc as the dead mock
    ):
        s._monitor_loop()

    # Five restart attempts, then it gives up on the sixth detection.
    assert spawn.call_count == sup._MAX_RESTARTS
    assert not s._stop_event.is_set()
    assert s.state == WorkerState.STOPPED


def test_monitor_does_not_restart_when_paused() -> None:
    """A worker stopped via pause() is not treated as a crash."""
    dead = MagicMock()
    dead.poll.return_value = 1
    dead.returncode = 0

    s = WorkerSupervisor()
    s._proc = dead
    s._paused = True

    with (
        patch.object(sup, "_POLL_INTERVAL", 0.0),
        patch.object(s, "_spawn") as spawn,
    ):
        # Stop the loop almost immediately so it can't spin forever.
        original_wait = s._stop_event.wait
        calls = {"n": 0}

        def fake_wait(timeout: float | None = None) -> bool:
            calls["n"] += 1
            if calls["n"] > 3:
                return True
            return original_wait(0.0)

        with patch.object(s._stop_event, "wait", side_effect=fake_wait):
            s._monitor_loop()

    spawn.assert_not_called()
