"""System tray icon: colour matches the light, tooltip gives the reason, menu sets overrides.

pystray must run on the main thread on Windows. Menu actions run on the tray thread and go
through ManualControl, which is thread-safe.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from luxstatus.config import RGB, Config
from luxstatus.light import OFF, colour_for
from luxstatus.sources.manual import ManualControl
from luxstatus.state import Resolution, Status

log = logging.getLogger(__name__)

# NOTIFYICONDATAW buffer limits, in UTF-16 code units excluding the terminator. A longer
# string makes ctypes raise, which can stop the tray icon from appearing at all.
TOOLTIP_MAX = 127  # szTip
NOTIFY_MAX = 255  # szInfo

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016


def icon_image(rgb: RGB, size: int = 64) -> Any:
    """A filled circle in the status colour; a grey ring when the light is off."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size // 8
    box = (pad, pad, size - pad - 1, size - pad - 1)
    if rgb == OFF:
        draw.ellipse(box, outline=(140, 140, 140, 255), width=max(2, size // 10))
    else:
        draw.ellipse(box, fill=(*rgb, 255), outline=(40, 40, 40, 255), width=max(1, size // 32))
    return img


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def fit(text: str, limit: int) -> str:
    """Shorten ``text`` to at most ``limit`` UTF-16 units, ending with an ellipsis if cut.
    Emoji in a meeting subject take two units each, so ``len()`` is not enough."""
    if utf16_len(text) <= limit:
        return text
    while utf16_len(text) > limit - 1:
        text = text[:-1]
    return text + "…"


def tooltip(resolution: Resolution | None) -> str:
    return fit(f"luxstatus — {resolution.describe() if resolution else 'starting'}", TOOLTIP_MAX)


class Tray:
    def __init__(
        self,
        config: Config,
        manual: ManualControl,
        log_file: Path,
        open_file: Callable[[Path], None] | None = None,
        open_config: Callable[[], None] | None = None,
        on_session_end: Callable[[], None] | None = None,
    ) -> None:
        import pystray

        self.config = config
        self.manual = manual
        self.log_file = log_file
        self._open_file = open_file or (lambda p: os.startfile(p))  # type: ignore[attr-defined]
        self._open_config = open_config
        self._on_session_end = on_session_end
        self._icon = pystray.Icon("luxstatus", icon_image(OFF), tooltip(None), self._menu())
        # pystray's hidden window answers unhandled messages with 0. For WM_QUERYENDSESSION
        # that means "block sign-out/shutdown", and WM_ENDSESSION is ignored so the light is
        # left on. Hook both (private API of the win32 backend; pystray is pinned in uv.lock).
        handlers = getattr(self._icon, "_message_handlers", None)
        if handlers is not None:
            handlers[WM_QUERYENDSESSION] = lambda _w, _l: 1
            handlers[WM_ENDSESSION] = self._session_ending

    def _menu(self) -> Any:
        from pystray import Menu, MenuItem

        def mode(status: Status | None) -> Callable[[Any], bool]:
            return lambda _item: self.manual.current() is status

        def action(fn: Callable[[], None]) -> Callable[[Any, Any], None]:
            def run(icon: Any, _item: Any) -> None:
                fn()
                icon.update_menu()

            return run

        def dnd(minutes: int | None) -> Callable[[Any, Any], None]:
            duration = timedelta(minutes=minutes) if minutes else None
            return action(lambda: self.manual.set(Status.DND, duration))

        return Menu(
            MenuItem("Auto", action(self.manual.auto), checked=mode(None), radio=True),
            MenuItem(
                "Do not disturb",
                Menu(
                    MenuItem("30 minutes", dnd(30)),
                    MenuItem("1 hour", dnd(60)),
                    MenuItem("Until cleared", dnd(None)),
                ),
                checked=mode(Status.DND),
                radio=True,
            ),
            MenuItem(
                "Available",
                action(lambda: self.manual.set(Status.AVAILABLE)),
                checked=mode(Status.AVAILABLE),
                radio=True,
            ),
            MenuItem(
                "Off",
                action(lambda: self.manual.set(Status.OFF)),
                checked=mode(Status.OFF),
                radio=True,
            ),
            Menu.SEPARATOR,
            MenuItem("Start at login", self._toggle_startup, checked=lambda _i: self._startup()),
            MenuItem("Open config", self._edit_config, visible=self._open_config is not None),
            MenuItem("Open log", lambda: self._open_file(self.log_file)),
            MenuItem("Quit", lambda icon, _item: icon.stop()),
        )

    def _startup(self) -> bool:
        from luxstatus import winapp

        return winapp.startup_installed()

    def _toggle_startup(self, icon: Any, _item: Any) -> None:
        from luxstatus import winapp

        try:
            if winapp.startup_installed():
                winapp.uninstall_startup()
                log.info("start at login disabled")
            else:
                log.info("start at login enabled: %s", winapp.install_startup())
        except Exception:
            log.exception("could not change start at login")
        icon.update_menu()

    def _edit_config(self) -> None:
        try:
            if self._open_config is not None:
                self._open_config()
        except Exception as e:
            log.exception("could not open the config file")
            self.notify(f"Could not open the config file: {e}")

    def _session_ending(self, wparam: int, _lparam: int) -> int:
        """Windows is signing out or shutting down; the process may be killed on return."""
        if wparam and self._on_session_end is not None:
            log.info("session ending")
            self._on_session_end()
        return 0

    def notify(self, message: str) -> None:
        """A Windows notification from the tray icon; failures are only logged."""
        try:
            self._icon.notify(fit(message, NOTIFY_MAX), "luxstatus")
        except Exception:
            log.warning("tray notification failed: %s", message)

    def update(self, resolution: Resolution) -> None:
        """Called from the engine thread on every resolution change."""
        self._icon.icon = icon_image(colour_for(resolution, self.config))
        self._icon.title = tooltip(resolution)
        self._icon.update_menu()  # refresh check marks, e.g. after an override expires

    def run(self, on_ready: Callable[[], None] | None = None) -> None:
        """Blocks the main thread until Quit. ``on_ready`` runs once the icon is visible, so a
        status that arrived during startup can be repainted onto the real icon."""

        def setup(icon: Any) -> None:
            icon.visible = True
            if on_ready is not None:
                try:
                    on_ready()
                except Exception:
                    log.exception("tray setup callback failed")

        self._icon.run(setup)
