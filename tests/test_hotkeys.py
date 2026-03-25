"""Tests for global hotkey listener."""

from __future__ import annotations

import pytest

from samwhispers.hotkeys import parse_hotkey


def test_parse_ctrl_shift_space() -> None:
    """Parse ctrl+shift+space into pynput keys."""
    from pynput.keyboard import Key  # type: ignore[import-untyped]

    keys = parse_hotkey("ctrl+shift+space")
    assert Key.ctrl_l in keys
    assert Key.shift_l in keys
    assert Key.space in keys
    assert len(keys) == 3


def test_parse_alt_r() -> None:
    """Parse alt+r into pynput keys."""
    from pynput.keyboard import Key, KeyCode  # type: ignore[import-untyped]

    keys = parse_hotkey("alt+r")
    assert Key.alt_l in keys
    assert KeyCode.from_char("r") in keys
    assert len(keys) == 2


def test_parse_ctrl_space() -> None:
    """Parse ctrl+space."""
    from pynput.keyboard import Key  # type: ignore[import-untyped]

    keys = parse_hotkey("ctrl+space")
    assert Key.ctrl_l in keys
    assert Key.space in keys


def test_parse_f5() -> None:
    """Parse single F-key."""
    from pynput.keyboard import Key  # type: ignore[import-untyped]

    keys = parse_hotkey("f5")
    assert Key.f5 in keys
    assert len(keys) == 1


def test_parse_unknown_key_raises() -> None:
    """Unknown key name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown key"):
        parse_hotkey("ctrl+badkey")


def test_parse_case_insensitive() -> None:
    """Parsing is case-insensitive."""
    from pynput.keyboard import Key  # type: ignore[import-untyped]

    keys = parse_hotkey("Ctrl+Shift+Space")
    assert Key.ctrl_l in keys
    assert Key.shift_l in keys
    assert Key.space in keys


def test_parse_language_key() -> None:
    """Parse language cycle hotkey ctrl+shift+l."""
    from pynput.keyboard import Key, KeyCode  # type: ignore[import-untyped]

    keys = parse_hotkey("ctrl+shift+l")
    assert Key.ctrl_l in keys
    assert Key.shift_l in keys
    assert KeyCode.from_char("l") in keys
    assert len(keys) == 3
