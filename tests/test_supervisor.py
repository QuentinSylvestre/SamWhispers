"""Tests for the worker supervisor process manager."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from samwhispers import supervisor as sup
from samwhispers.supervisor import WorkerState, WorkerSupervisor
from samwhispers.webserver import DEFAULT_PORT


@pytest.fixture(autouse=True)
def _no_managed_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """By default, supervisors in tests don't start a real whisper-server.

    Tests that exercise whisper management override _load_whisper_config locally.
    """
    monkeypatch.setattr(WorkerSupervisor, "_load_whisper_config", lambda self: None)


@pytest.fixture(autouse=True)
def _no_startup_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the startup overlay in tests to avoid extra subprocess spawning."""
    monkeypatch.setattr(WorkerSupervisor, "_start_startup_overlay", lambda self: None)


def _running_proc() -> MagicMock:
    """A mock Popen that reports as still running."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.stderr = iter([])  # empty iterable so _read_worker_logs exits immediately
    return proc


def test_build_cmd_minimal() -> None:
    s = WorkerSupervisor()
    assert s._build_cmd() == [sys.executable, "-m", "samwhispers", "worker", "--unmanaged-server"]


def test_build_cmd_with_config_and_verbose() -> None:
    s = WorkerSupervisor(config_path="/tmp/c.toml", verbose=True)
    assert s._build_cmd() == [
        sys.executable,
        "-m",
        "samwhispers",
        "worker",
        "--unmanaged-server",
        "--config",
        "/tmp/c.toml",
        "--verbose",
    ]


def test_main_detaches_by_default() -> None:
    with (
        patch.object(sup.sys, "argv", ["samwhispers-supervisor"]),
        patch("samwhispers.singleinstance.is_running", return_value=False),
        patch.object(sup, "_relaunch_detached") as relaunch,
    ):
        sup.main()
    relaunch.assert_called_once()


def test_main_skips_launch_when_already_running() -> None:
    with (
        patch.object(sup.sys, "argv", ["samwhispers-supervisor", "--no-web"]),
        patch("samwhispers.singleinstance.is_running", return_value=True),
        patch.object(sup, "_relaunch_detached") as relaunch,
    ):
        sup.main()
    relaunch.assert_not_called()  # detected an existing instance; no second launch


def test_relaunch_detached_builds_foreground_cmd() -> None:
    args = MagicMock(config=None, verbose=False, no_tray=False, no_web=False, web_port=None)
    with (
        patch.object(sup, "_python_launcher", return_value="py"),
        patch.object(sup.subprocess, "Popen") as popen,
    ):
        sup._relaunch_detached(args)
    cmd = popen.call_args.args[0]
    assert cmd[0] == "py"
    assert cmd[1] == "-c"
    # Args are embedded in the -c script string
    script = cmd[2]
    assert "--foreground" in script
    assert "from samwhispers.supervisor import main; main()" in script
    kwargs = popen.call_args.kwargs
    assert kwargs["stdout"] == sup.subprocess.DEVNULL
    if sys.platform == "win32":
        flags = kwargs.get("creationflags", 0)
        assert flags & 0x08000000  # CREATE_NO_WINDOW
        assert flags & 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        assert kwargs.get("start_new_session") is True


def test_relaunch_detached_passes_through_args() -> None:
    args = MagicMock(config="/c.toml", verbose=True, no_tray=True, no_web=True, web_port=9000)
    with (
        patch.object(sup, "_python_launcher", return_value="py"),
        patch.object(sup.subprocess, "Popen") as popen,
    ):
        sup._relaunch_detached(args)
    cmd = popen.call_args.args[0]
    # All args are embedded in the -c script string
    script = cmd[2]
    for token in ("--foreground", "--no-tray", "--no-web", "--verbose", "--config", "/c.toml"):
        assert token in script
    assert "--web-port" in script and "9000" in script


def test_apply_config_change_without_whisper_restart() -> None:
    s = WorkerSupervisor()
    with (
        patch.object(s, "restart_whisper") as rw,
        patch.object(s, "restart") as rworker,
    ):
        s.apply_config_change(restart_whisper=False)
    rw.assert_not_called()
    rworker.assert_called_once()


def test_apply_config_change_with_whisper_restart() -> None:
    s = WorkerSupervisor()
    with (
        patch.object(s, "restart_whisper") as rw,
        patch.object(s, "restart") as rworker,
    ):
        s.apply_config_change(restart_whisper=True)
    rw.assert_called_once()
    rworker.assert_called_once()


def test_start_whisper_skips_when_not_managed() -> None:
    s = WorkerSupervisor()
    whisper_cfg = MagicMock()
    whisper_cfg.managed = False
    with (
        patch.object(s, "_load_whisper_config", return_value=whisper_cfg),
        patch("samwhispers.server.WhisperServerManager") as mgr_cls,
    ):
        s._start_whisper()
    mgr_cls.assert_not_called()
    assert s._whisper_manager is None


def test_start_whisper_starts_manager_when_managed() -> None:
    s = WorkerSupervisor()
    whisper_cfg = MagicMock()
    whisper_cfg.managed = True
    manager = MagicMock()
    with (
        patch.object(s, "_load_whisper_config", return_value=whisper_cfg),
        patch.object(s, "_load_vad_config", return_value=None),
        patch("samwhispers.server.WhisperServerManager", return_value=manager) as mgr_cls,
    ):
        s._start_whisper()
    mgr_cls.assert_called_once_with(whisper_cfg, vad_config=None)
    manager.start.assert_called_once()
    assert s._whisper_manager is manager
    # stop_whisper tears it down
    s._stop_whisper()
    manager.stop.assert_called_once()
    assert s._whisper_manager is None


def test_start_spawns_and_runs_monitor() -> None:
    s = WorkerSupervisor()
    with patch.object(sup.subprocess, "Popen", return_value=_running_proc()) as popen:
        s.start()
        try:
            popen.assert_called_once()
            assert s.state == WorkerState.STARTING
            assert s._monitor_thread is not None and s._monitor_thread.is_alive()
        finally:
            s.shutdown()
    assert s.state == WorkerState.STOPPED


def test_pause_terminates_and_resume_respawns() -> None:
    proc1, proc2 = _running_proc(), _running_proc()
    with patch.object(sup.subprocess, "Popen", side_effect=[proc1, proc2]) as popen:
        s = WorkerSupervisor()
        s.start()
        s._startup_ticks = 5  # simulate accumulated ticks
        s.pause()
        assert s.state == WorkerState.PAUSED
        proc1.terminate.assert_called_once()
        s.resume()
        assert s.state == WorkerState.STARTING
        assert s._startup_ticks == 0  # reset on resume
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
        s._startup_ticks = 5  # simulate accumulated ticks
        s.restart()
        proc1.terminate.assert_called_once()
        assert s._proc is proc2
        assert s.state == WorkerState.STARTING
        assert s._startup_ticks == 0  # reset on restart
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
    assert WorkerState.STARTING in seen
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


def test_monitor_stops_on_config_exit_code() -> None:
    """Exit code 78 (EX_CONFIG) stops the retry loop immediately."""
    dead = MagicMock()
    dead.poll.return_value = 78
    dead.returncode = 78

    s = WorkerSupervisor()
    s._proc = dead
    s._state = WorkerState.RUNNING

    with (
        patch.object(sup, "_POLL_INTERVAL", 0.0),
        patch.object(s, "_spawn") as spawn,
        patch("samwhispers.supervisor.notify"),
    ):
        s._monitor_loop()

    spawn.assert_not_called()
    assert s.state == WorkerState.STOPPED


def test_monitor_notifies_on_config_exit_code() -> None:
    """Exit code 78 triggers a user notification."""
    dead = MagicMock()
    dead.poll.return_value = 78
    dead.returncode = 78

    s = WorkerSupervisor()
    s._proc = dead
    s._state = WorkerState.RUNNING

    with (
        patch.object(sup, "_POLL_INTERVAL", 0.0),
        patch.object(s, "_spawn"),
        patch("samwhispers.supervisor.notify") as mock_notify,
    ):
        s._monitor_loop()

    mock_notify.assert_called_once_with(
        "SamWhispers",
        "SamWhispers couldn\u2019t start \u2014 click to open Logs",
        on_click_url="http://127.0.0.1:7891/#logs",
    )


def test_monitor_notifies_on_max_restarts() -> None:
    """Max-restart exhaustion triggers a user notification."""
    dead = MagicMock()
    dead.poll.return_value = 1
    dead.returncode = 1

    s = WorkerSupervisor()
    s._proc = dead
    s._state = WorkerState.RUNNING

    with (
        patch.object(sup, "_POLL_INTERVAL", 0.0),
        patch.object(sup, "_RESTART_BACKOFF", 0.0),
        patch.object(s, "_spawn"),
        patch("samwhispers.supervisor.notify") as mock_notify,
    ):
        s._monitor_loop()

    mock_notify.assert_called_once_with(
        "SamWhispers",
        "SamWhispers stopped after repeated failures \u2014 click to open Logs",
        on_click_url="http://127.0.0.1:7891/#logs",
    )


def test_start_whisper_notifies_on_failure() -> None:
    """Whisper-server start failure triggers a user notification."""
    s = WorkerSupervisor()
    whisper_cfg = MagicMock()
    whisper_cfg.managed = True

    with (
        patch.object(s, "_load_whisper_config", return_value=whisper_cfg),
        patch("samwhispers.server.WhisperServerManager") as mgr_cls,
        patch("samwhispers.supervisor.notify") as mock_notify,
    ):
        mgr_cls.return_value.start.side_effect = RuntimeError("boom")
        s._start_whisper()

    mock_notify.assert_called_once_with(
        "SamWhispers",
        "Voice transcription unavailable \u2014 the speech engine failed to start",
    )


# ---------------------------------------------------------------------------
# 4c: web_enabled=False when port pre-bound (subprocess integration test)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="supervisor integration test (Windows only)")
@pytest.mark.integration
def test_web_enabled_false_when_port_bound() -> None:
    """web_enabled must be False in runtime.json when port 7891 is already bound.

    IMPORTANT: This test must run with no other supervisor instance active.
    A running supervisor already holds the lock; a new subprocess would be
    blocked by the Phase 1 guard and never write runtime.json.
    """
    from samwhispers.singleinstance import is_running
    assert not is_running(), (
        "This integration test requires no running supervisor instance. "
        "Stop the running supervisor before running integration tests."
    )

    from samwhispers.history import resolve_data_dir

    data_dir = resolve_data_dir()
    meta_path = data_dir / "runtime.json"

    # Pre-bind DEFAULT_PORT so the supervisor's uvicorn thread cannot bind it.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", DEFAULT_PORT))
    srv.listen(1)
    proc = None
    try:
        # Clear any stale runtime.json from a prior run so we don't false-pass.
        meta_path.unlink(missing_ok=True)

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "samwhispers",
                "supervisor",
                "--foreground",
                "--no-tray",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Poll up to 12s: 5s web-poll timeout + startup overhead + safety margin.
        deadline = time.time() + 12
        while time.time() < deadline:
            if meta_path.exists():
                break
            time.sleep(0.1)

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        else:
            proc.wait(timeout=5)

        assert meta_path.exists(), "runtime.json was never written"
        meta = json.loads(meta_path.read_text())
        assert meta["pid"] == proc.pid, (
            f"runtime.json PID {meta['pid']} does not match proc.pid {proc.pid} — stale metadata"
        )
        assert meta["web_enabled"] is False, (
            f"Expected web_enabled=False when port {DEFAULT_PORT} is pre-bound, got: {meta}"
        )
        assert meta["web_port"] is None, (
            f"Expected web_port=None when port {DEFAULT_PORT} is pre-bound, got: {meta}"
        )
    finally:
        srv.close()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------------------------------------------------------------------
# 4d: supervisor.pid absent after clean exit (subprocess integration test)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="supervisor integration test (Windows only)")
@pytest.mark.integration
def test_supervisor_pid_cleaned_on_exit() -> None:
    """supervisor.pid must not exist after a clean supervisor exit.

    IMPORTANT: This test must run with no other supervisor instance active.
    A running supervisor already holds the lock; the new subprocess would exit
    immediately via the Phase 1 guard before writing supervisor.pid.
    """
    from samwhispers.singleinstance import is_running
    assert not is_running(), (
        "This integration test requires no running supervisor instance. "
        "Stop the running supervisor before running integration tests."
    )

    from samwhispers.history import resolve_data_dir

    data_dir = resolve_data_dir()
    pid_file = data_dir / "supervisor.pid"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "samwhispers",
            "supervisor",
            "--foreground",
            "--no-tray",
            "--no-web",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        # Wait up to 5s for supervisor.pid to appear, confirming the supervisor
        # has passed lock acquisition and written its PID.
        deadline = time.time() + 5
        while time.time() < deadline:
            if pid_file.exists():
                break
            time.sleep(0.1)
        assert pid_file.exists(), (
            "supervisor.pid was never written — check that no other supervisor is running"
        )

        # Graceful shutdown via CTRL_BREAK_EVENT: the supervisor's SIGINT handler
        # sets _main_stop, the while loop exits, and the finally block runs
        # (including pid_path().unlink()). TerminateProcess (proc.terminate()) is
        # a hard kill that never runs finally blocks on Windows.
        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        # Allow up to 5s for the finally block to finish deleting supervisor.pid.
        deadline = time.time() + 5.0
        while pid_file.exists() and time.time() < deadline:
            time.sleep(0.05)

        assert not pid_file.exists(), (
            f"supervisor.pid was not removed on clean exit: {pid_file}"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
