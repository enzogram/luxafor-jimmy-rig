"""The asyncio core: collect source updates, resolve on every update and tick, drive the light."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from luxstatus.config import Config
from luxstatus.state import Resolution, Source, SourceUpdate, resolve

log = logging.getLogger(__name__)


class Light(Protocol):
    def show(self, resolution: Resolution) -> None:
        """Display a new status. Must not raise; a driver handles its own device errors."""

    def tick(self) -> None:
        """Called every resolver tick so a driver can reconnect and resend after a replug."""


class Engine:
    def __init__(
        self,
        config: Config,
        light: Light,
        clock: Callable[[], datetime] = datetime.now,
        on_change: Callable[[Resolution], None] | None = None,
    ) -> None:
        self.config = config
        self.light = light
        self.clock = clock
        self.on_change = on_change
        self.latest: dict[Source, SourceUpdate] = {}
        self.current: Resolution | None = None
        self.queue: asyncio.Queue[SourceUpdate] = asyncio.Queue()
        self._failing: set[str] = set()

    def apply(self, update: SourceUpdate) -> None:
        self.latest[update.source] = update

    def step(self) -> Resolution:
        """Resolve now; write to the light only when what it shows changes."""
        resolution = resolve(self.latest, self.clock(), self.config)
        previous = self.current
        if previous is None or (previous.status, previous.camera) != (
            resolution.status,
            resolution.camera,
        ):
            self.light.show(resolution)
        if resolution != previous:
            log.info("status %s", resolution.describe())
            self.current = resolution
            if self.on_change is not None:
                self.on_change(resolution)
        return resolution

    async def run(self, stop: asyncio.Event) -> None:
        """Run until ``stop`` is set. Ticks happen on schedule even under a steady update stream."""
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        stopper = asyncio.ensure_future(stop.wait())
        self._guarded("resolve", self.step)  # set the light at startup without waiting
        try:
            while not stop.is_set():
                if loop.time() >= next_tick:
                    next_tick = loop.time() + self.config.resolver.tick_seconds
                    self._guarded("light tick", self.light.tick)
                getter = asyncio.ensure_future(self.queue.get())
                await asyncio.wait(
                    {getter, stopper},
                    timeout=max(0.0, next_tick - loop.time()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if getter.done():
                    self.apply(getter.result())
                    while not self.queue.empty():
                        self.apply(self.queue.get_nowait())
                else:
                    getter.cancel()  # a cancelled Queue.get() does not consume an item
                if not stop.is_set():
                    self._guarded("resolve", self.step)
        finally:
            stopper.cancel()

    def _guarded(self, what: str, fn: Callable[[], object]) -> None:
        """Run ``fn``; an error is logged once per failure streak and never ends the loop,
        so one bad resolve or light write can't freeze the light for good."""
        try:
            fn()
        except Exception:
            if what not in self._failing:
                log.exception("%s failed; will keep trying", what)
                self._failing.add(what)
        else:
            if what in self._failing:
                log.info("%s recovered", what)
                self._failing.discard(what)
