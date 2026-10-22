"""Luxafor Flag: HID report bytes and status → colour mapping.

The HID device class arrives in milestone 2; everything here is pure.
"""

from __future__ import annotations

from luxstatus.config import RGB, Config
from luxstatus.state import Resolution, Status

VENDOR_ID = 0x04D8
PRODUCT_ID = 0xF372

ALL_LEDS = 0xFF
CMD_STATIC = 0x01
CMD_FADE = 0x02
CMD_STROBE = 0x03

OFF: RGB = (0, 0, 0)


def static_report(rgb: RGB) -> bytes:
    """Report for a static colour, prefixed with report ID 0x00 as Windows hidapi requires."""
    r, g, b = rgb
    return bytes([0x00, CMD_STATIC, ALL_LEDS, r, g, b, 0, 0, 0])


def fade_report(rgb: RGB, speed: int) -> bytes:
    r, g, b = rgb
    return bytes([0x00, CMD_FADE, ALL_LEDS, r, g, b, speed, 0, 0])


def strobe_report(rgb: RGB, speed: int, repeat: int) -> bytes:
    r, g, b = rgb
    return bytes([0x00, CMD_STROBE, ALL_LEDS, r, g, b, speed, 0, repeat])


def colour_for(resolution: Resolution, config: Config) -> RGB:
    c = config.colours
    match resolution.status:
        case Status.OFF:
            return OFF
        case Status.AVAILABLE:
            return c.available
        case Status.TENTATIVE:
            return c.tentative
        case Status.BUSY:
            return c.busy
        case Status.CALL:
            camera = resolution.camera and config.devices.camera_distinct
            return c.call_camera if camera else c.call
        case Status.DND:
            return c.dnd
