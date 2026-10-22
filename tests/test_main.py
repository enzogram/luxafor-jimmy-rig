import asyncio
from dataclasses import replace

from luxstatus.config import Config, DevicesConfig
from luxstatus.main import Poller, default_pollers, poll_loop, run
from luxstatus.sources.devices import MICROPHONE, WEBCAM, DevicesSource
from luxstatus.state import Source, SourceUpdate, Status
from tests.fakes import FakeLight

LIVE = {"LastUsedTimeStart": 134000000000000000, "LastUsedTimeStop": 0}


def test_default_pollers_respect_enabled():
    assert [p.name for p in default_pollers(Config())] == ["devices"]
    off = replace(Config(), devices=replace(Config().devices, enabled=False))
    assert default_pollers(off) == []


def test_poll_loop_survives_errors_and_stops():
    calls = []

    def poll(now):
        calls.append(now)
        if len(calls) == 2:
            raise OSError("registry hiccup")
        return SourceUpdate(Source.DEVICES, now)

    async def main():
        queue = asyncio.Queue()
        stop = asyncio.Event()
        task = asyncio.create_task(poll_loop(Poller("t", poll, 0.01), queue, stop))
        while len(calls) < 4:
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, 1)
        return queue.qsize()

    delivered = asyncio.run(main())
    assert delivered == len(calls) - 1  # the failing poll delivers nothing


def test_light_follows_a_call_end_to_end():
    """Fake registry → DevicesSource → poll loop → engine → light."""
    registry = {MICROPHONE: {}, WEBCAM: {}}
    cfg = replace(Config(), work_hours=replace(Config().work_hours, enabled=False))
    devices = DevicesSource(
        DevicesConfig(on_debounce_reads=1, off_debounce_seconds=0),
        read_tree=lambda cap: registry[cap],
        running_names=set,
    )
    light = FakeLight()
    seen = []

    async def main():
        stop = asyncio.Event()

        def on_change(r):
            seen.append(r.status)

        task = asyncio.create_task(
            run(cfg, light, stop, [Poller("devices", devices.poll, 0.01)], on_change)
        )

        async def until(status):
            for _ in range(200):
                if light.shown and light.shown[-1].status is status:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError(f"light never showed {status}")

        await until(Status.AVAILABLE)
        registry[MICROPHONE] = {"MSTeams_8wekyb3d8bbwe": LIVE}
        await until(Status.CALL)
        registry[MICROPHONE] = {}
        await until(Status.AVAILABLE)
        stop.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(main())
    assert [r.status for r in light.shown] == [Status.AVAILABLE, Status.CALL, Status.AVAILABLE]
    assert light.shown[1].detail == "MSTeams (mic)"
