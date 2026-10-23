import asyncio
from dataclasses import replace
from datetime import datetime

from luxstatus.config import Config
from luxstatus.engine import Engine
from luxstatus.state import Source, SourceUpdate, Status
from tests.fakes import FakeLight

NOW = datetime(2026, 10, 21, 10, 0)


def make(**kw):
    light = FakeLight()
    changes = []
    engine = Engine(Config(), light, clock=lambda: NOW, on_change=changes.append, **kw)
    return engine, light, changes


def call(detail="MSTeams (mic)", camera=False):
    return SourceUpdate(Source.DEVICES, NOW, Status.CALL, detail, camera=camera)


def test_light_written_only_when_status_changes():
    engine, light, changes = make()
    engine.step()
    engine.step()
    assert [r.status for r in light.shown] == [Status.AVAILABLE]

    engine.apply(call())
    engine.step()
    engine.apply(call(detail="MSTeams, Zoom.exe (mic)"))
    engine.step()
    assert [r.status for r in light.shown] == [Status.AVAILABLE, Status.CALL]
    # Tray still hears about the new detail.
    assert [r.detail for r in changes][-1] == "MSTeams, Zoom.exe (mic)"
    assert len(changes) == 3


def test_camera_change_rewrites_light():
    engine, light, _ = make()
    engine.apply(call())
    engine.step()
    engine.apply(call(camera=True))
    engine.step()
    assert [r.camera for r in light.shown] == [False, True]


def test_run_sets_light_at_startup_processes_queue_and_stops():
    engine, light, changes = make()

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(engine.run(stop))
        await asyncio.sleep(0.05)
        engine.queue.put_nowait(call())
        for _ in range(100):
            if light.shown and light.shown[-1].status is Status.CALL:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(main())
    assert [r.status for r in light.shown] == [Status.AVAILABLE, Status.CALL]
    assert light.ticks >= 1


def test_ticks_keep_coming_under_a_steady_update_stream():
    light = FakeLight()
    cfg = replace(Config(), resolver=replace(Config().resolver, tick_seconds=0.05))
    engine = Engine(cfg, light, clock=lambda: NOW)

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(engine.run(stop))
        # Updates arrive faster than the tick for 0.3 s. Run by elapsed time, not by count:
        # Windows' ~15 ms clock makes short sleeps return immediately.
        loop = asyncio.get_running_loop()
        end = loop.time() + 0.3
        while loop.time() < end:
            engine.queue.put_nowait(call())
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(main())
    assert light.ticks >= 3  # ~6 expected
