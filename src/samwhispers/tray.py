"""System tray icon for the supervisor.

Thin wrapper over ``pystray`` (with ``Pillow`` for the icon image). All
third-party imports are lazy so the module imports cleanly on headless hosts;
``tray_available()`` reports whether a tray can actually be shown.
"""

from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import Any

from samwhispers.supervisor import WorkerState, WorkerSupervisor
from samwhispers.notify import notify

log = logging.getLogger("samwhispers.tray")

# Icon dot colour per state: blue=starting, green=running, amber=paused, grey=stopped.
# Used only as a fallback when the bundled PNG artwork cannot be loaded.
_COLORS: dict[WorkerState, tuple[int, int, int]] = {
    WorkerState.STARTING: (45, 108, 223),
    WorkerState.RUNNING: (0, 220, 0),
    WorkerState.PAUSED: (255, 193, 7),
    WorkerState.STOPPED: (158, 158, 158),
}

# Map each worker state to one of the bundled tray artwork variants.
_TRAY_ASSET: dict[WorkerState, str] = {
    WorkerState.RUNNING: "running",
    WorkerState.PAUSED: "warning",
    WorkerState.STOPPED: "not_running",
    WorkerState.STARTING: "not_running",
}

_TRAY_DIR = Path(__file__).parent / "assets" / "tray"
# Sizes available on disk for each tray variant (see assets/tray/<state>/).
_TRAY_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
_image_cache: dict[tuple[WorkerState, int], Any] = {}


def tray_available() -> bool:
    """Return True if a tray icon can be shown.

    On Linux, importing pystray eagerly connects to the X display and raises
    (e.g. ``Xlib.error.DisplayNameError``) when none is present, so any
    exception here means "no tray", not just a missing package.
    """
    try:
        import PIL  # noqa: F401
        import pystray  # type: ignore[import-untyped] # noqa: F401
    except Exception:
        return False
    return True


def _draw_dot(state: WorkerState, size: int) -> Any:
    """Render a simple coloured-dot icon (fallback when artwork is unavailable)."""
    from PIL import Image, ImageDraw

    color = _COLORS.get(state, _COLORS[WorkerState.STOPPED])
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = size // 8
    draw.ellipse((margin, margin, size - margin, size - margin), fill=color)
    return image


def _make_image(state: WorkerState, size: int = 64) -> Any:
    """Return the tray icon for ``state``, loading bundled artwork when possible.

    Falls back to a coloured dot if the PNG cannot be read (e.g. missing asset
    or a Pillow without PNG support), so the tray never fails to render.
    """
    cached = _image_cache.get((state, size))
    if cached is not None:
        return cached

    from PIL import Image

    variant = _TRAY_ASSET.get(state, "not_running")
    asset_size = min(_TRAY_SIZES, key=lambda s: (s < size, abs(s - size)))
    path = _TRAY_DIR / variant / f"samwhispers-tray-{variant}-{asset_size}.png"
    try:
        image = Image.open(path).convert("RGBA")
        if image.size != (size, size):
            image = image.resize((size, size), Image.LANCZOS)
    except Exception:
        log.debug("Falling back to drawn tray icon (could not load %s)", path, exc_info=True)
        image = _draw_dot(state, size)

    _image_cache[(state, size)] = image
    return image


def run_tray(supervisor: WorkerSupervisor, settings_url: str | None = None, stop_event: Any = None) -> None:
    """Run the tray icon loop on the calling (main) thread; blocks until Quit.

    Installs SIGINT/SIGTERM handlers that stop the icon so the supervisor exits
    cleanly when the login session ends or systemd stops the service. If
    ``settings_url`` is given, an "Open settings" item opens it in the browser.
    If ``stop_event`` is a threading.Event, a background thread watches it and
    calls icon.stop() when set (allows the web endpoint to stop the tray).
    """
    import webbrowser

    import pystray

    def status_text(_item: Any) -> str:
        return f"SamWhispers: {supervisor.state.value}"

    def is_paused(_item: Any) -> bool:
        return supervisor.state == WorkerState.PAUSED

    def on_toggle_pause(icon: Any, _item: Any) -> None:
        if supervisor.state == WorkerState.PAUSED:
            supervisor.resume()
        else:
            supervisor.pause()
        icon.update_menu()

    def on_restart(_icon: Any, _item: Any) -> None:
        supervisor.restart()

    def on_restart_all(icon: Any, _item: Any) -> None:
        supervisor.request_relaunch()
        notify("SamWhispers", "SamWhispers is restarting...")
        icon.stop()

    def on_open_settings(_icon: Any, _item: Any) -> None:
        if settings_url:
            webbrowser.open(settings_url)

    def on_quit(icon: Any, _item: Any) -> None:
        icon.stop()

    items = [
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
    ]
    if settings_url:
        items.append(pystray.MenuItem("Open settings", on_open_settings, default=True))
    items += [
        pystray.MenuItem("Pause", on_toggle_pause, checked=is_paused),
        pystray.MenuItem("Restart worker", on_restart),
        pystray.MenuItem("Restart SamWhispers", on_restart_all),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    ]
    menu = pystray.Menu(*items)

    icon = pystray.Icon(
        "samwhispers",
        icon=_make_image(supervisor.state),
        title="SamWhispers",
        menu=menu,
    )

    def on_state_change(state: WorkerState) -> None:
        def _update() -> None:
            try:
                icon.icon = _make_image(state)
                icon.title = f"SamWhispers ({state.value})"
                icon.update_menu()
            except Exception:
                log.debug("Failed to update tray icon", exc_info=True)

        if sys.platform == "linux":
            try:
                from gi.repository import GLib  # type: ignore[import-untyped]

                GLib.idle_add(_update)
                return
            except ImportError:
                pass
        _update()

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("Received signal %d, stopping tray", signum)
        icon.stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    supervisor.set_state_listener(on_state_change)
    try:
        if stop_event is not None:
            import threading

            def _watch_stop() -> None:
                stop_event.wait()
                icon.stop()

            threading.Thread(target=_watch_stop, daemon=True, name="tray-stop-watcher").start()
        icon.run()
    finally:
        supervisor.set_state_listener(None)
