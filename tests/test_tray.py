import sys

import pytest

from luxstatus.state import Resolution, Status
from luxstatus.tray import TOOLTIP_MAX, icon_image, tooltip


def test_icon_is_filled_with_status_colour():
    img = icon_image((255, 0, 0), size=64)
    assert img.size == (64, 64)
    assert img.getpixel((32, 32)) == (255, 0, 0, 255)
    assert img.getpixel((0, 0))[3] == 0  # transparent corner


def test_off_icon_is_a_hollow_ring():
    img = icon_image((0, 0, 0), size=64)
    assert img.getpixel((32, 32))[3] == 0
    assert img.getpixel((32, 9))[3] == 255


def test_tooltip():
    assert tooltip(None) == "luxstatus — starting"
    r = Resolution(Status.BUSY, detail="Outlook: Weekly TSF sync until 14:30")
    assert tooltip(r) == "luxstatus — BUSY — Outlook: Weekly TSF sync until 14:30"
    long = tooltip(Resolution(Status.BUSY, detail="x" * 300))
    assert len(long) == TOOLTIP_MAX and long.endswith("…")


@pytest.mark.skipif(sys.platform != "win32", reason="pystray win32 backend")
def test_real_tray_builds_menu_and_updates(tmp_path):
    from luxstatus.config import Config
    from luxstatus.sources.manual import ManualControl
    from luxstatus.tray import Tray

    manual = ManualControl(lambda u: None)
    tray = Tray(Config(), manual, tmp_path / "log.txt", open_file=lambda p: None)
    manual.set(Status.DND)
    tray.update(Resolution(Status.DND, detail="Manual"))
    items = {item.text: item for item in tray._icon.menu.items}
    assert items["Do not disturb"].checked
    assert not items["Auto"].checked
    assert tray._icon.title == "luxstatus — DND — Manual"
