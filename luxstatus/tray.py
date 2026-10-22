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

TOOLTIP_MAX = 127  # Windows notification-area tooltip limit (NOTIFYICONDATAW.szTip)


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


def tooltip(resolution: Resolution | None) -> str:
    text = f"luxstatus — {resolution.describe() if resolution else 'starting'}"
    return text if len(text) <= TOOLTIP_MAX else text[: TOOLTIP_MAX - 1] + "…"


class Tray:
    def __init__(
        self,
        config: Config,
        manual: ManualControl,
        log_file: Path,
        open_file: Callable[[Path], None] | None = None,
    ) -> None:
        import pystray

        self.config = config
        self.manual = manual
        self.log_file = log_file
        self._open_file = open_file or (lambda p: os.startfile(p))  # type: ignore[attr-defined]
        self._icon = pystray.Icon("luxstatus", icon_image(OFF), tooltip(None), self._menu())

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
            MenuItem("Open log", lambda: self._open_file(self.log_file)),
            MenuItem("Quit", lambda icon, _item: icon.stop()),
        )

    def update(self, resolution: Resolution) -> None:
        """Called from the engine thread on every resolution change."""
        self._icon.icon = icon_image(colour_for(resolution, self.config))
        self._icon.title = tooltip(resolution)
        self._icon.update_menu()  # refresh check marks, e.g. after an override expires

    def run(self) -> None:
        """Blocks the main thread until Quit."""
        self._icon.run()
