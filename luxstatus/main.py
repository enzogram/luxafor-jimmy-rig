"""Wiring: build the engine and sources from config and run them.

Threads: the tray (pystray) owns the main thread; the engine, pollers and light run on an
asyncio loop in the "engine" thread; hotkeys have their own message-loop thread. Other threads
reach the engine only through ``App.post``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from luxstatus import config as config_mod
from luxstatus import logs
from luxstatus.config import Config
from luxstatus.engine import Engine, Light
from luxstatus.light import HidLight
from luxstatus.sources.devices import DevicesSource
from luxstatus.sources.manual import ManualControl
from luxstatus.state import Resolution, SourceUpdate, Status

if TYPE_CHECKING:
    from luxstatus.hotkeys import HotkeyThread

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Poller:
    """A source polled from the asyncio loop. ``poll`` runs in a worker thread, so it must not
    hold thread-bound resources (Outlook gets its own thread in milestone 5)."""

    name: str
    poll: Callable[[datetime], SourceUpdate]
    interval: float


def default_pollers(cfg: Config) -> list[Poller]:
    pollers = []
    if cfg.devices.enabled:
        pollers.append(Poller("devices", DevicesSource(cfg.devices).poll, cfg.devices.poll_seconds))
    return pollers


async def poll_loop(
    poller: Poller,
    queue: asyncio.Queue[SourceUpdate],
    stop: asyncio.Event,
    clock: Callable[[], datetime] = datetime.now,
) -> None:
    """Poll until stopped. Errors are logged and skipped; the resolver's staleness check stops
    a failing source from holding its last status."""
    failing = False
    while not stop.is_set():
        try:
            update = await asyncio.to_thread(poller.poll, clock())
        except Exception:
            if not failing:
                log.exception("%s poll failed; will keep retrying", poller.name)
            failing = True
        else:
            if failing:
                log.info("%s poll recovered", poller.name)
            failing = False
            queue.put_nowait(update)
        try:
            await asyncio.wait_for(stop.wait(), poller.interval)
        except TimeoutError:
            pass


async def run(engine: Engine, stop: asyncio.Event, pollers: list[Poller]) -> None:
    await asyncio.gather(engine.run(stop), *(poll_loop(p, engine.queue, stop) for p in pollers))


class App:
    """Runs the engine and pollers on a background asyncio thread."""

    def __init__(self, cfg: Config, light: Light, pollers: list[Poller]) -> None:
        self.light = light
        self.pollers = pollers
        self.listeners: list[Callable[[Resolution], None]] = []
        self.loop = asyncio.new_event_loop()
        self.stop_event = asyncio.Event()
        self.engine = Engine(cfg, light, on_change=self._changed)
        self.manual = ManualControl(self.post)
        self.thread = threading.Thread(target=self._run, name="engine", daemon=True)

    def post(self, update: SourceUpdate) -> None:
        """Submit an update from any thread, including before the loop has started."""
        self.loop.call_soon_threadsafe(self.engine.queue.put_nowait, update)

    def start(self) -> None:
        self.thread.start()

    def shutdown(self) -> None:
        """Stop the engine thread, then leave the light off."""
        if self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.stop_event.set)
            self.thread.join(timeout=10)
        self.light.show(Resolution(Status.OFF))
        close = getattr(self.light, "close", None)
        if close is not None:
            close()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(run(self.engine, self.stop_event, self.pollers))
        finally:
            self.loop.close()

    def _changed(self, resolution: Resolution) -> None:
        for listener in self.listeners:
            try:
                listener(resolution)
            except Exception:
                log.exception("status listener failed")


def start_hotkeys(cfg: Config, manual: ManualControl) -> HotkeyThread | None:
    if sys.platform != "win32":
        return None
    from luxstatus.hotkeys import HotkeyThread

    bindings = {}
    if cfg.hotkeys.dnd:
        bindings[cfg.hotkeys.dnd] = manual.toggle_dnd
    if cfg.hotkeys.auto:
        bindings[cfg.hotkeys.auto] = manual.auto
    thread = HotkeyThread(bindings)
    thread.start()
    return thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m luxstatus")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--console", action="store_true", help="no tray icon; Ctrl+C quits")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    log_file = logs.setup(console=True, verbose=args.verbose)
    cfg = config_mod.load(args.config)
    log.info("config: %s", args.config.resolve() if args.config.exists() else "defaults")
    log.info("log file: %s", log_file)

    app = App(cfg, HidLight(cfg), default_pollers(cfg))
    hotkeys = start_hotkeys(cfg, app.manual)
    try:
        if args.console:
            app.start()
            print("luxstatus running; Ctrl+C to quit.")
            while app.thread.is_alive():
                app.thread.join(timeout=0.5)
        else:
            from luxstatus.tray import Tray

            tray = Tray(cfg, app.manual, log_file)
            app.listeners.append(tray.update)
            app.start()
            tray.run()
    except KeyboardInterrupt:
        pass
    finally:
        if hotkeys is not None:
            hotkeys.stop()
        app.shutdown()
        log.info("stopped")
    return 0
