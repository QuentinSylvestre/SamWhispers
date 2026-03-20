"""Text injection via clipboard and paste simulation."""

from __future__ import annotations

import logging
import time

log = logging.getLogger("samwhispers")


class TextInjector:
    """Copy text to clipboard and simulate Ctrl+V to paste into active app."""

    def __init__(self, paste_delay: float = 0.1) -> None:
        self._paste_delay = paste_delay
        self._keyboard = None  # Lazy init to avoid X11 crash on headless

    def _ensure_keyboard(self) -> None:
        if self._keyboard is None:
            from pynput.keyboard import Controller  # type: ignore[import-untyped]

            self._keyboard = Controller()

    def inject(self, text: str) -> None:
        """Copy text to clipboard and simulate Ctrl+V."""
        if not text:
            log.debug("Empty text, skipping injection")
            return

        import pyperclip  # type: ignore[import-untyped]
        from pynput.keyboard import Controller, Key

        self._ensure_keyboard()
        pyperclip.copy(text)
        log.debug("Copied %d chars to clipboard", len(text))

        time.sleep(self._paste_delay)

        assert isinstance(self._keyboard, Controller)
        self._keyboard.press(Key.ctrl)
        self._keyboard.press("v")
        self._keyboard.release("v")
        self._keyboard.release(Key.ctrl)
        log.debug("Simulated Ctrl+V")

    def check_clipboard_available(self) -> bool:
        """Check if clipboard backend is available."""
        try:
            import pyperclip

            pyperclip.paste()
            return True
        except Exception:
            return False
