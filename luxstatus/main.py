"""Wiring: build the engine and sources from config and run them.

Threads: the tray (pystray) owns the main thread; the engine, pollers and light run on an
asyncio loop in the "engine" thread; hotkeys have their own message-loop thread. Other threads
reach the engine only through ``App.post``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
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
from luxstatus.reload import ConfigWatcher
from luxstatus.sources.devices import DevicesSource
from luxstatus.sources.manual import ManualControl
from luxstatus.sources.outlook import OutlookThread
from luxstatus.sources.session import SessionSource
from luxstatus.state import Resolution, Source, SourceUpdate, Status

if TYPE_CHECKING:
    from luxstatus.hotkeys import HotkeyThread
    from luxstatus.tray import Tray

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Poller:
    """A source polled from the asyncio loop. ``poll`` runs in a worker thread, so it must not
    hold thread-bound resources (Outlook has its own thread)."""

    name: str
    poll: Callable[[datetime], SourceUpdate]
    interval: float


class SourcePollers:
    """Builds the poll list from config. Source objects are reused across reloads so their
    state (the call debounce) survives a config change."""

    def __init__(self) -> None:
        self.devices: DevicesSource | None = None
        self.session = SessionSource()

    def __call__(self, cfg: Config) -> list[Poller]:
        pollers = []
        if cfg.devices.enabled:
            if self.devices is None:
                self.devices = DevicesSource(cfg.devices)
            else:
                self.devices.reconfigure(cfg.devices)
            pollers.append(Poller("devices", self.devices.poll, cfg.devices.poll_seconds))
        else:
            self.devices = None
        if cfg.session.enabled:
            pollers.append(Poller("session", self.session.poll, cfg.session.poll_seconds))
        return pollers


def default_pollers(cfg: Config) -> list[Poller]:
    return SourcePollers()(cfg)


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

    def __init__(
        self,
        cfg: Config,
        light: Light,
        pollers: list[Poller],
        make_pollers: Callable[[Config], list[Poller]] | None = None,
    ) -> None:
        self.light = light
        self.pollers = pollers
        self.make_pollers = make_pollers
        self.listeners: list[Callable[[Resolution], None]] = []
        self.loop = asyncio.new_event_loop()
        self.stop_event = asyncio.Event()
        self.engine = Engine(cfg, light, on_change=self._changed)
        self.manual = ManualControl(self.post)
        self.thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._poll_tasks: list[asyncio.Task[None]] = []

    def post(self, update: SourceUpdate) -> None:
        """Submit an update from any thread, including before the loop has started."""
        self.loop.call_soon_threadsafe(self.engine.queue.put_nowait, update)

    def reconfigure(self, cfg: Config) -> None:
        """Apply a new config from any thread."""
        self.loop.call_soon_threadsafe(self._apply_config, cfg)

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

    def _apply_config(self, cfg: Config) -> None:
        self.engine.config = cfg
        if hasattr(self.light, "config"):
            self.light.config = cfg  # type: ignore[attr-defined]
        if self.make_pollers is not None:
            for task in self._poll_tasks:
                task.cancel()
            self.pollers = self.make_pollers(cfg)
            self._start_pollers()
        enabled = {
            Source.DEVICES: cfg.devices.enabled,
            Source.SESSION: cfg.session.enabled,
            Source.OUTLOOK: cfg.outlook.enabled,
        }
        for source, on in enabled.items():
            if not on:
                self.engine.latest.pop(source, None)
        self.engine.current = None  # rewrite light and tray: colours may have changed
        self.engine.step()

    def _start_pollers(self) -> None:
        self._poll_tasks = [
            self.loop.create_task(poll_loop(p, self.engine.queue, self.stop_event))
            for p in self.pollers
        ]

    async def _main(self) -> None:
        self._start_pollers()
        try:
            await self.engine.run(self.stop_event)
        finally:
            for task in self._poll_tasks:
                task.cancel()
            await asyncio.gather(*self._poll_tasks, return_exceptions=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
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


def start_outlook(cfg: Config, post: Callable[[SourceUpdate], None]) -> OutlookThread | None:
    if not cfg.outlook.enabled or sys.platform != "win32":
        return None
    thread = OutlookThread(cfg.outlook, post)
    thread.start()
    return thread


class Runtime:
    """Owns the config-dependent pieces outside the engine thread and applies reloads."""

    def __init__(self, cfg: Config, app: App, tray: Tray | None = None) -> None:
        self.cfg = cfg
        self.app = app
        self.tray = tray
        self.hotkeys = start_hotkeys(cfg, app.manual)
        self.outlook = start_outlook(cfg, app.post)

    def apply(self, cfg: Config, _path: Path | None = None) -> None:
        old, self.cfg = self.cfg, cfg
        self.app.reconfigure(cfg)
        if self.tray is not None:
            self.tray.config = cfg
        if cfg.hotkeys != old.hotkeys:
            if self.hotkeys is not None:
                self.hotkeys.stop()
            self.hotkeys = start_hotkeys(cfg, self.app.manual)
        if cfg.outlook != old.outlook:
            if self.outlook is not None:
                self.outlook.stop()
            self.outlook = start_outlook(cfg, self.app.post)

    def stop(self) -> None:
        if self.hotkeys is not None:
            self.hotkeys.stop()
        if self.outlook is not None:
            self.outlook.stop()


def ensure_config_file(found: Path | None, appdata: str | None, template: Path) -> Path:
    """The config file to open for editing; creates the user's copy from the template if the
    app is running on built-in defaults."""
    if found is not None and found.exists():
        return found
    if not appdata:
        raise RuntimeError("APPDATA is not set; cannot create a config file")
    path = config_mod.user_config_path(appdata)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, path)
        log.info("created %s from the template", path)
    return path


def self_check() -> int:
    """Import every runtime dependency; used by CI against the packaged exe."""
    import hid  # noqa: F401
    import psutil  # noqa: F401
    import pystray._win32  # noqa: F401
    import pythoncom  # noqa: F401
    import win32com.client  # noqa: F401

    from luxstatus.tray import icon_image

    icon_image((0, 255, 0))
    if not config_mod.template_path().exists():
        raise FileNotFoundError(f"config template missing: {config_mod.template_path()}")
    log.info("self-check passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luxstatus")
    parser.add_argument("--config", type=Path, help="config file (default: see README)")
    parser.add_argument("--console", action="store_true", help="no tray icon; Ctrl+C quits")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--install-startup", action="store_true", help="start at login")
    parser.add_argument("--uninstall-startup", action="store_true", help="stop starting at login")
    parser.add_argument("--check", action="store_true", help="verify dependencies and exit")
    args = parser.parse_args(argv)

    log_file = logs.setup(console=True, verbose=args.verbose)
    if args.check:
        try:
            return self_check()
        except Exception:
            log.exception("self-check failed")
            return 1
    if args.install_startup or args.uninstall_startup:
        from luxstatus import winapp

        if args.install_startup:
            log.info("start at login enabled: %s", winapp.install_startup())
        else:
            winapp.uninstall_startup()
            log.info("start at login disabled")
        return 0

    if sys.platform == "win32":
        from luxstatus import winapp

        if not winapp.acquire_single_instance():
            log.info("another luxstatus is already running; exiting")
            if not args.console:
                winapp.message_box("luxstatus is already running. Look for it in the tray.")
            return 1

    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else None
    appdata = os.environ.get("APPDATA")

    def locate() -> Path | None:
        return config_mod.find_config(args.config, appdata, Path.cwd(), exe_dir)

    config_path = locate()
    cfg = config_mod.load(config_path)
    found = config_path is not None and config_path.exists()
    log.info("config: %s", config_path.resolve() if found else "built-in defaults")
    log.info("log file: %s", log_file)

    def open_config() -> None:
        path = ensure_config_file(locate(), appdata, config_mod.template_path())
        os.startfile(path)  # type: ignore[attr-defined]

    pollers = SourcePollers()
    app = App(cfg, HidLight(cfg), pollers(cfg), make_pollers=pollers)
    tray = None
    if not args.console:
        from luxstatus.tray import Tray

        tray = Tray(cfg, app.manual, log_file, open_config=open_config)
        app.listeners.append(tray.update)
    runtime = Runtime(cfg, app, tray)
    watcher = ConfigWatcher(
        locate, runtime.apply, on_error=tray.notify if tray is not None else lambda m: None
    )
    try:
        app.start()
        watcher.start()
        if tray is not None:
            tray.run()
        else:
            print("luxstatus running; Ctrl+C to quit.")
            while app.thread.is_alive():
                app.thread.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        runtime.stop()
        app.shutdown()
        log.info("stopped")
    return 0
