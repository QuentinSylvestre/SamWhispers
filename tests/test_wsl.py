"""Tests for WSL support: detection, VK code parsing, clipboard, hotkey listener."""

from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

from samwhispers.hotkeys import parse_hotkey_vk
from samwhispers.wsl import is_wsl


def test_is_wsl_true() -> None:
    """Detect WSL from /proc/version containing 'microsoft'."""
    is_wsl.cache_clear()
    content = "Linux version 6.6.87.2-microsoft-standard-WSL2"
    with patch("builtins.open", mock_open(read_data=content)):
        assert is_wsl() is True
    is_wsl.cache_clear()


def test_is_wsl_false() -> None:
    """Non-WSL /proc/version returns False."""
    is_wsl.cache_clear()
    content = "Linux version 6.5.0-44-generic (buildd@lcy02-amd64-080)"
    with patch("builtins.open", mock_open(read_data=content)):
        assert is_wsl() is False
    is_wsl.cache_clear()


def test_is_wsl_no_proc() -> None:
    """Missing /proc/version returns False."""
    is_wsl.cache_clear()
    with patch("builtins.open", side_effect=OSError):
        assert is_wsl() is False
    is_wsl.cache_clear()


def test_parse_hotkey_vk_ctrl_shift_space() -> None:
    """Parse ctrl+shift+space into VK codes."""
    codes = parse_hotkey_vk("ctrl+shift+space")
    assert codes == [0xA2, 0xA0, 0x20]


def test_parse_hotkey_vk_alt_r() -> None:
    """Parse alt+r into VK codes."""
    codes = parse_hotkey_vk("alt+r")
    assert codes == [0xA4, ord("R")]


def test_parse_hotkey_vk_f5() -> None:
    """Parse single F-key."""
    codes = parse_hotkey_vk("f5")
    assert codes == [0x74]


def test_wsl_injector_inject() -> None:
    """WSLTextInjector calls clip.exe and powershell.exe."""
    with patch("samwhispers.wsl.find_windows_exe", return_value="/mnt/c/clip.exe"):
        from samwhispers.inject import WSLTextInjector

        injector = WSLTextInjector(paste_delay=0.0)

    with patch("subprocess.run") as mock_run:
        injector.inject("hello")
        assert mock_run.call_count == 2
        # First call: clip.exe
        assert mock_run.call_args_list[0][0][0] == ["/mnt/c/clip.exe"]
        # Second call: powershell SendKeys
        assert "SendKeys" in mock_run.call_args_list[1][0][0][-1]


def test_wsl_injector_empty_noop() -> None:
    """Empty text does not call any subprocess."""
    with patch("samwhispers.wsl.find_windows_exe", return_value="/mnt/c/clip.exe"):
        from samwhispers.inject import WSLTextInjector

        injector = WSLTextInjector(paste_delay=0.0)

    with patch("subprocess.run") as mock_run:
        injector.inject("")
        mock_run.assert_not_called()


def test_wsl_hotkey_listener_suppress_resume() -> None:
    """Suppress/resume toggles the suppressed flag."""
    from samwhispers.hotkeys import WSLHotkeyListener

    listener = WSLHotkeyListener("ctrl+space", "hold", MagicMock(), MagicMock())
    listener.suppress()
    assert listener._suppressed is True
    listener.resume()
    assert listener._suppressed is False
