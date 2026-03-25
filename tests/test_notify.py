"""Tests for cross-platform notifications."""

from __future__ import annotations

from unittest.mock import patch

from samwhispers.notify import check_notify_available, notify


def test_notify_linux_calls_notify_send() -> None:
    """On Linux, notify() calls notify-send."""
    with (
        patch("samwhispers.wsl.is_wsl", return_value=False),
        patch("samwhispers.notify.sys") as mock_sys,
        patch("samwhispers.notify.subprocess") as mock_sub,
    ):
        mock_sys.platform = "linux"
        notify("Title", "Message")
        mock_sub.run.assert_called_once()
        args = mock_sub.run.call_args[0][0]
        assert args[0] == "notify-send"
        assert "Title" in args
        assert "Message" in args


def test_notify_wsl_calls_powershell() -> None:
    """On WSL, notify() dispatches to _notify_windows."""
    with (
        patch("samwhispers.wsl.is_wsl", return_value=True),
        patch("samwhispers.notify._notify_windows") as mock_win,
    ):
        notify("Title", "Msg")
        mock_win.assert_called_once_with("Title", "Msg")


def test_notify_windows_calls_powershell() -> None:
    """On native Windows, notify() dispatches to _notify_windows."""
    with (
        patch("samwhispers.wsl.is_wsl", return_value=False),
        patch("samwhispers.notify.sys") as mock_sys,
        patch("samwhispers.notify._notify_windows") as mock_win,
    ):
        mock_sys.platform = "win32"
        notify("Title", "Msg")
        mock_win.assert_called_once_with("Title", "Msg")


def test_notify_failure_does_not_raise() -> None:
    """Notification failure is swallowed."""
    with patch("samwhispers.wsl.is_wsl", side_effect=RuntimeError("boom")):
        notify("Title", "Msg")  # Should not raise


def test_check_notify_available_linux() -> None:
    """On Linux, checks for notify-send."""
    with (
        patch("samwhispers.wsl.is_wsl", return_value=False),
        patch("samwhispers.notify.sys") as mock_sys,
        patch("samwhispers.notify.shutil") as mock_shutil,
    ):
        mock_sys.platform = "linux"
        mock_shutil.which.return_value = "/usr/bin/notify-send"
        assert check_notify_available() is True
        mock_shutil.which.return_value = None
        assert check_notify_available() is False


def test_check_notify_available_wsl() -> None:
    """On WSL, always returns True."""
    with patch("samwhispers.wsl.is_wsl", return_value=True):
        assert check_notify_available() is True


def test_notify_windows_passes_env_vars() -> None:
    """_notify_windows passes title/message via env vars, not f-string interpolation."""
    from samwhispers.notify import _notify_windows

    with (
        patch("samwhispers.wsl.is_wsl", return_value=True),
        patch("samwhispers.wsl.find_windows_exe", return_value="/mnt/c/ps.exe"),
        patch("samwhispers.notify.subprocess") as mock_sub,
    ):
        _notify_windows("Test Title", "Test Msg")
        mock_sub.Popen.assert_called_once()
        kwargs = mock_sub.Popen.call_args[1]
        assert kwargs["env"]["SW_TITLE"] == "Test Title"
        assert kwargs["env"]["SW_MSG"] == "Test Msg"
        # Script should NOT contain the title/message literally
        script = mock_sub.Popen.call_args[0][0][-1]
        assert "Test Title" not in script
        assert "$env:SW_TITLE" in script
