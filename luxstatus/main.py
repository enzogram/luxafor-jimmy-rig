"""Wiring: build the engine and sources from config and run them.

Milestone 3 runs headless in a console (Ctrl+C quits). The tray arrives in milestone 4.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from luxstatus import config as config_mod
from luxstatus.config import Config
from luxstatus.engine import Engine, Light
from luxstatus.light import OFF, HidLight
from luxstatus.sources.devices import DevicesSource
from luxstatus.state import Resolution, SourceUpdate

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


async def run(
    cfg: Config,
    light: Light,
    stop: asyncio.Event,
    pollers: list[Poller],
    on_change: Callable[[Resolution], None] | None = None,
) -> Engine:
    engine = Engine(cfg, light, on_change=on_change)
    await asyncio.gather(engine.run(stop), *(poll_loop(p, engine.queue, stop) for p in pollers))
    return engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m luxstatus")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = config_mod.load(args.config)
    log.info("config: %s", args.config.resolve() if args.config.exists() else "defaults")
    light = HidLight(cfg)

    def on_change(r: Resolution) -> None:
        print(f"{datetime.now():%H:%M:%S}  {r.describe()}", flush=True)

    async def runner() -> None:
        await run(cfg, light, asyncio.Event(), default_pollers(cfg), on_change)

    print("luxstatus running; Ctrl+C to quit.")
    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
    finally:
        light.set_rgb(OFF)
        light.close()
    return 0
