"""Notice config.toml changes and hand the new Config to the running app."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from luxstatus import config as config_mod
from luxstatus.config import Config, ConfigError

log = logging.getLogger(__name__)

Stamp = tuple[Path | None, float | None]


def stamp(path: Path | None) -> Stamp:
    try:
        return path, path.stat().st_mtime if path is not None else None
    except OSError:
        return path, None


class ConfigWatcher(threading.Thread):
    """Every ``interval`` seconds, re-run the config lookup and compare (path, mtime).

    This also catches a newly created %APPDATA% config taking over from the defaults. A file
    that fails to parse is reported through ``on_error`` and the running config is kept.
    """

    def __init__(
        self,
        locate: Callable[[], Path | None],
        on_change: Callable[[Config, Path | None], None],
        on_error: Callable[[str], None] = lambda message: None,
        interval: float = 2,
    ) -> None:
        super().__init__(name="config-watcher", daemon=True)
        self.locate = locate
        self.on_change = on_change
        self.on_error = on_error
        self.interval = interval
        self._stamp = stamp(locate())
        self._stop_event = threading.Event()

    def check(self) -> bool:
        """Reload if the config changed. Returns True when a new config was applied."""
        path = self.locate()
        current = stamp(path)
        if current == self._stamp:
            return False
        self._stamp = current
        try:
            cfg = config_mod.load(path)
        except (ConfigError, OSError) as e:
            log.error("config not reloaded: %s", e)
            self.on_error(f"Config error, keeping previous settings: {e}")
            return False
        log.info("config reloaded from %s", path if current[1] is not None else "defaults")
        self.on_change(cfg, path)
        return True

    def run(self) -> None:
        while not self._stop_event.wait(self.interval):
            try:
                self.check()
            except Exception:
                log.exception("applying the new config failed")

    def stop(self) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=self.interval + 1)
