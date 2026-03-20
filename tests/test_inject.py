"""Tests for text injection module."""

from __future__ import annotations

import pytest


def _clipboard_works() -> bool:
    """Check if clipboard is actually functional (not just DISPLAY set)."""
    try:
        import pyperclip

        pyperclip.paste()
        return True
    except Exception:
        return False


_no_clipboard = not _clipboard_works()


def test_empty_text_noop() -> None:
    """Empty text does not attempt clipboard or paste."""
    from samwhispers.inject import TextInjector

    injector = TextInjector()
    injector.inject("")


@pytest.mark.skipif(_no_clipboard, reason="No functional clipboard available")
def test_clipboard_roundtrip() -> None:
    """Text written to clipboard can be read back."""
    import pyperclip

    from samwhispers.inject import TextInjector

    injector = TextInjector()
    assert injector.check_clipboard_available() is True
    pyperclip.copy("samwhispers test")
    assert pyperclip.paste() == "samwhispers test"


@pytest.mark.skipif(_no_clipboard, reason="No functional clipboard available")
def test_check_clipboard_available() -> None:
    """check_clipboard_available returns True when clipboard works."""
    from samwhispers.inject import TextInjector

    injector = TextInjector()
    assert injector.check_clipboard_available() is True
