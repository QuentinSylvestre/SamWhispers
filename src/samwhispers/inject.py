"""Text injection via clipboard and paste simulation."""

from __future__ import annotations

import logging
import subprocess
import time

log = logging.getLogger("samwhispers")


class TextInjector:
    """Copy text to clipboard and simulate Ctrl+V to paste into active app (X11/Windows)."""

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
        from pynput.keyboard import Key

        self._ensure_keyboard()
        pyperclip.copy(text)
        log.debug("Copied %d chars to clipboard", len(text))

        time.sleep(self._paste_delay)

        if self._keyboard is None:
            raise RuntimeError("Keyboard controller not initialized")
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


class WSLTextInjector:
    """Copy text to clipboard via clip.exe and paste via PowerShell SendKeys (WSL)."""

    def __init__(self, paste_delay: float = 0.1) -> None:
        from samwhispers.wsl import find_windows_exe

        self._paste_delay = paste_delay
        self._clip: str = find_windows_exe("clip.exe") or ""
        self._powershell: str = find_windows_exe("powershell.exe") or ""
        if not self._clip or not self._powershell:
            raise RuntimeError(
                f"WSL interop executables not found (clip.exe={self._clip or 'missing'}, "
                f"powershell.exe={self._powershell or 'missing'}). Is Windows interop enabled?"
            )
        log.debug("WSL injector: clip=%s, powershell=%s", self._clip, self._powershell)

    def inject(self, text: str) -> None:
        """Copy text to Windows clipboard via clip.exe, then Ctrl+V via SendKeys."""
        if not text:
            log.debug("Empty text, skipping injection")
            return

        # Write to clipboard
        subprocess.run(
            [self._clip],
            input=text.encode("utf-16-le"),
            check=True,
            timeout=5,
        )
        log.debug("Copied %d chars to clipboard via clip.exe", len(text))

        time.sleep(self._paste_delay)

        # Simulate Ctrl+V via PowerShell SendKeys
        subprocess.run(
            [
                self._powershell,
                "-NoProfile",
                "-c",
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.SendKeys]::SendWait('^v')",
            ],
            check=True,
            timeout=5,
        )
        log.debug("Simulated Ctrl+V via PowerShell SendKeys")

    def check_clipboard_available(self) -> bool:
        """Check if clip.exe and powershell.exe are accessible."""
        try:
            subprocess.run(
                [self._powershell, "-NoProfile", "-c", "Get-Clipboard"],
                capture_output=True,
                timeout=5,
            )
            return True
        except Exception:
            return False
