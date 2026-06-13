"""Tests for the autostart (login service) installer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from samwhispers import autostart


def test_systemd_unit_text_has_execstart_and_targets() -> None:
    text = autostart.systemd_unit_text("/path/to/samwhispers-supervisor")
    assert "ExecStart=/path/to/samwhispers-supervisor" in text
    assert "WantedBy=graphical-session.target" in text
    assert "Restart=on-failure" in text


def test_supervisor_command_prefers_installed_script() -> None:
    with patch.object(autostart.shutil, "which", return_value="/usr/bin/samwhispers-supervisor"):
        assert autostart.supervisor_command() == "/usr/bin/samwhispers-supervisor"


def test_supervisor_command_falls_back_to_module() -> None:
    with patch.object(autostart.shutil, "which", return_value=None):
        cmd = autostart.supervisor_command()
    assert "-m samwhispers.supervisor" in cmd


def test_supervisor_command_windows_uses_pythonw() -> None:
    with (
        patch.object(autostart.sys, "platform", "win32"),
        patch.object(autostart.sys, "executable", "/py/python.exe"),
        patch.object(autostart.Path, "exists", return_value=True),
    ):
        cmd = autostart.supervisor_command()
    assert "pythonw.exe" in cmd
    assert "-m samwhispers.supervisor" in cmd


def test_enable_linux_writes_unit_and_enables(tmp_path: object) -> None:
    unit = MagicMock()
    with (
        patch.object(autostart, "systemd_unit_path") as path_fn,
        patch.object(autostart.subprocess, "run") as run,
        patch.object(autostart, "supervisor_command", return_value="/x/supervisor"),
    ):
        path_fn.return_value = unit
        autostart._enable_linux()
    unit.write_text.assert_called_once()
    written = unit.write_text.call_args.args[0]
    assert "ExecStart=/x/supervisor" in written
    # daemon-reload + enable --now were invoked
    calls = [c.args[0] for c in run.call_args_list]
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert any("enable" in c for c in calls)


def test_dispatch_selects_platform(monkeypatch: object) -> None:
    with patch.object(autostart.sys, "platform", "win32"):
        with patch.object(autostart, "_enable_windows") as win_enable:
            autostart._dispatch("enable")
            win_enable.assert_called_once()
    with patch.object(autostart.sys, "platform", "linux"):
        with patch.object(autostart, "_start_linux") as lin_start:
            autostart._dispatch("start")
            lin_start.assert_called_once()


def test_enable_windows_creates_then_starts() -> None:
    with (
        patch.object(autostart, "supervisor_command", return_value="cmd"),
        patch.object(autostart.subprocess, "run") as run,
    ):
        autostart._enable_windows()
    calls = [c.args[0] for c in run.call_args_list]
    assert any("/Create" in c for c in calls)
    assert any("/Run" in c for c in calls)  # also starts immediately
