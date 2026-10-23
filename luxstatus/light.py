"""Luxafor Flag driver and CLI.

    uv run python -m luxstatus.light busy             # set once
    uv run python -m luxstatus.light 0,0,255 --watch  # hold; survives unplug/replug

Report bytes and the colour mapping are pure; ``HidLight`` takes an injectable opener so it
can be tested without hardware.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from luxstatus import config as config_mod
from luxstatus.config import RGB, Config
from luxstatus.state import Resolution, Status

log = logging.getLogger(__name__)

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


class HidDevice(Protocol):
    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...


def open_flag() -> HidDevice:
    import hid

    dev = hid.device()
    dev.open(VENDOR_ID, PRODUCT_ID)
    return dev


Scheduler = Callable[[float, Callable[[], None]], None]


def _timer(delay: float, fn: Callable[[], None]) -> None:
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()


class HidLight:
    """Keeps the Flag showing the wanted colour.

    Every ``tick`` rewrites the colour. After an unplug/replug or sleep the Flag comes back
    dark and the old handle can still look open, so a failing write is the only reliable
    signal. A failed write closes the handle and retries once with a fresh one.

    When a call starts it strobes for ``effects.flash_seconds``, then a timer restores the
    solid colour. Ticks skip rewriting during the flash so they don't cut it short. Writes come
    from the engine thread and the timer thread, so they are serialised with a lock.
    """

    def __init__(
        self,
        config: Config,
        open_device: Callable[[], HidDevice] = open_flag,
        schedule: Scheduler = _timer,
    ) -> None:
        self.config = config
        self._open_device = open_device
        self._schedule = schedule
        self._dev: HidDevice | None = None
        self._warned_missing = False
        self._lock = threading.RLock()
        self._status: Status | None = None
        self._flash_id = 0  # bumped on every show; a stale restore timer does nothing
        self._flashing = False
        self.colour: RGB | None = None

    @property
    def connected(self) -> bool:
        return self._dev is not None

    def show(self, resolution: Resolution) -> None:
        rgb = colour_for(resolution, self.config)
        fx = self.config.effects
        with self._lock:
            starting_call = resolution.status is Status.CALL and self._status is not Status.CALL
            self._status = resolution.status
            self._flash_id += 1
            self._flashing = False
            self.colour = rgb
            if starting_call and fx.call_flash and rgb != OFF:
                if self._send(strobe_report(rgb, fx.flash_speed, fx.flash_repeat)):
                    self._flashing = True
                    flash_id = self._flash_id
                    self._schedule(fx.flash_seconds, lambda: self._end_flash(flash_id))
                    return
            self._send(static_report(rgb))

    def set_rgb(self, rgb: RGB) -> bool:
        with self._lock:
            self._flash_id += 1
            self._flashing = False
            self._status = None
            self.colour = rgb
            return self._send(static_report(rgb))

    def tick(self) -> None:
        with self._lock:
            if self.colour is not None and not self._flashing:
                self._send(static_report(self.colour))

    def close(self) -> None:
        with self._lock:
            if self._dev is not None:
                try:
                    self._dev.close()
                except OSError:
                    pass
                self._dev = None

    def _end_flash(self, flash_id: int) -> None:
        with self._lock:
            if flash_id != self._flash_id or self.colour is None:
                return  # status changed since this flash started
            self._flashing = False
            self._send(static_report(self.colour))

    def _send(self, report: bytes) -> bool:
        for _ in range(2):
            if self._dev is None and not self._connect():
                return False
            assert self._dev is not None
            try:
                if self._dev.write(report) >= 0:
                    return True
                log.warning("Flag write failed; reconnecting")
            except (OSError, ValueError) as e:
                log.warning("Flag write failed (%s); reconnecting", e)
            self.close()
        return False

    def _connect(self) -> bool:
        try:
            self._dev = self._open_device()
        except OSError as e:
            if not self._warned_missing:
                log.warning("Flag not available (%s); will keep retrying", e)
                self._warned_missing = True
            return False
        log.info("Flag connected")
        self._warned_missing = False
        return True


def parse_target(text: str, config: Config) -> RGB:
    """A status name (``busy``, ``off``, ...) or ``r,g,b``."""
    try:
        return colour_for(Resolution(Status[text.strip().upper()]), config)
    except KeyError:
        pass
    parts = text.split(",")
    try:
        rgb = tuple(int(p) for p in parts)
    except ValueError:
        rgb = ()
    if len(rgb) != 3 or not all(0 <= c <= 255 for c in rgb):
        names = ", ".join(s.name.lower() for s in Status)
        raise ValueError(f"expected one of {names} or r,g,b (0-255), got {text!r}")
    return (rgb[0], rgb[1], rgb[2])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m luxstatus.light",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", help="status name (available, busy, ...) or r,g,b")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="keep the colour up, reconnecting after unplug; Ctrl+C turns it off",
    )
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    cfg = config_mod.load(args.config)
    try:
        rgb = parse_target(args.target, cfg)
    except ValueError as e:
        parser.error(str(e))
    light = HidLight(cfg)
    ok = light.set_rgb(rgb)
    if not args.watch:
        light.close()
        return 0 if ok else 1

    print(f"Holding {rgb}. Unplug and replug the Flag to test recovery; Ctrl+C to stop.")
    try:
        while True:
            time.sleep(cfg.resolver.tick_seconds)
            light.tick()
    except KeyboardInterrupt:
        light.set_rgb(OFF)
        light.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
